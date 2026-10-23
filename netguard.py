# -*- coding: utf-8 -*-
"""网络守卫：监控断网 / 出口 IP 变化，触发自动重认证。

单设备限制下 PC 独占在线，所以 PC 一断网就必须尽快自愈。守护线程做两件事：
1. 定时探测公网连通性（TCP connect，不发应用层流量）；
2. 盯住本机出口 IP 是否变化（校园网重新分配地址后，旧的授权就失效了）。
"""
from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

# 探测目标：(host, port)——任一连通即认为外网可达
_PROBE_TARGETS: List[Tuple[str, int]] = [
    ("223.5.5.5", 53),
    ("119.29.29.29", 53),
    ("1.1.1.1", 443),
    ("114.114.114.114", 80),
]

STATE_ONLINE = "online"
STATE_OFFLINE = "offline"
STATE_UNKNOWN = "unknown"


@dataclass
class GuardEvent:
    kind: str          # online / offline / ip_changed
    detail: str = ""
    ip: str = ""


def probe_online(timeout: float = 1.2) -> bool:
    """TCP 探测外网连通性。"""
    for host, port in _PROBE_TARGETS:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            continue
    return False


class NetGuard:
    """后台守护线程。"""

    def __init__(
        self,
        auth,
        interval: int = 20,
        logger: Optional[Callable[[str], None]] = None,
        on_event: Optional[Callable[[GuardEvent], None]] = None,
    ):
        self.auth = auth
        self.interval = interval
        self.log = logger or (lambda *a, **k: None)
        self.on_event = on_event or (lambda e: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_ip = ""
        self.online = False
        self._fail_streak = 0

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="netguard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ 主循环

    def _run(self) -> None:
        # 启动后先等一小会儿，别和开机自启抢网络
        time.sleep(3)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                self.log(f"守护线程异常: {exc}")
            self._stop.wait(self.interval)

    def _tick(self) -> None:
        ip = ""
        try:
            ip = self.auth.detect_local_ip()
        except Exception:
            ip = ""

        with self._lock:
            ip_changed = bool(ip) and bool(self.last_ip) and ip != self.last_ip
            if ip:
                self.last_ip = ip

        if ip_changed:
            self.log(f"出口 IP 变化: {ip}")
            self.on_event(GuardEvent("ip_changed", f"出口 IP 变为 {ip}", ip))

        online = probe_online()
        with self._lock:
            was_online = self.online
            self.online = online

        if online and not was_online:
            self._fail_streak = 0
            self.log("网络已连通")
            self.on_event(GuardEvent("online", "网络恢复", ip))
        elif not online:
            self._fail_streak += 1
            # 连续 2 次判定为断网才上报，避免瞬时抖动误触发
            if self._fail_streak >= 2 and was_online:
                self.log("检测到断网，准备重新认证")
                self.on_event(GuardEvent("offline", "网络中断", ip))
