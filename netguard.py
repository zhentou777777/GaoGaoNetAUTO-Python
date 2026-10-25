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

# 连通性探测目标：HTTPS + **带预期内容**。
#
# 为什么不能只做 TCP connect：很多环境（校园网未认证时的「重定向门户」、
# 企业代理、防火墙的 SYN-ACK 响应）会让 TCP 握手成功，但**根本没有真的上网**。
# 只看 TCP 会把「连上了但拿不到任何内容」误判成已联网，
# 于是程序以为网络正常，**永远不去触发重新认证**。
#
# 现在要求：拿回 200 状态码，且响应体里出现预期特征串。
_PROBE_TARGETS = [
    ("https://connectivitycheck.gstatic.com/generate_204", "204"),
    ("https://www.msftconnecttest.com/connecttest.txt", "Microsoft Connect Test"),
    ("https://captive.apple.com/hotspot-detect.html", "Success"),
]


@dataclass
class GuardEvent:
    kind: str          # online / offline / ip_changed
    detail: str = ""
    ip: str = ""


def probe_online(timeout: float = 3.0) -> bool:
    """探测外网连通性。

    判据是**拿到预期的响应内容**，而不是「TCP 握手成功」。
    原因见 :data:`_PROBE_TARGETS` 的注释：未认证的校园网会接受 TCP
    连接但把 HTTP 请求重定向到登录门户，只看握手会把「没网」误判成「有网」，
    程序就永远不去重认证。

    判定不了（DNS 解析失败、证书问题、全部超时）时返回 False ——
    宁可多触发一次重认证，也不要在断网时假装正常。
    """
    try:
        import requests
    except Exception:
        requests = None  # type: ignore

    if requests is not None:
        for url, expect in _PROBE_TARGETS:
            try:
                resp = requests.get(url, timeout=timeout,
                                    allow_redirects=False,
                                    headers={"User-Agent": "Mozilla/5.0"})
                # 302/301 说明被门户劫持 → 不算联网
                if resp.status_code in (301, 302, 303, 307, 308):
                    continue
                if resp.status_code != 200:
                    continue
                body = resp.text or ""
                if expect.lower() in body.lower():
                    return True
            except Exception:
                continue
        return False

    # 没有 requests 时退回 TCP 探测（弱一些，但总比不探测强）
    for host, port in (("223.5.5.5", 53), ("1.1.1.1", 443)):
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
        retry_base: int = 15,
        retry_max: int = 120,
    ):
        self.auth = auth
        self.interval = interval
        self.log = logger or (lambda *a, **k: None)
        self.on_event = on_event or (lambda e: None)
        self.retry_base = retry_base
        self.retry_max = retry_max
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reconnect_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self.last_ip = ""
        self.online = False
        self._fail_streak = 0
        self._reconnecting = False

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="netguard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def start_reconnect(self) -> None:
        """启动断线重连循环（幂等）。"""
        with self._lock:
            if self._reconnecting:
                return
            self._reconnecting = True
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, name="reconnect", daemon=True
        )
        self._reconnect_thread.start()

    def stop_reconnect(self) -> None:
        with self._lock:
            self._reconnecting = False

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
            # ⚠️ 必须盯**出口 IP**，不是本机网卡地址。
            # 云高把授权绑在出口 IP 上：手机热点下本机是 192.168.x.x，
            # 但云高认的是手机的公网地址。盯本机 IP 在热点场景下
            # 永远不变，校园网重分配地址时反而可能误判。
            ip = self.auth.detect_exit_ip() or self.auth.detect_local_ip()
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

    # ------------------------------------------------------------ 断线重连

    def _reconnect_loop(self) -> None:
        """断网后的重连循环：指数退避重试，直到网络恢复。

        与 NetGuard 的职责分工：
            - NetGuard 负责**发现**断网与 IP 漂移
            - 本循环负责**持续重试**，避免只试一次就放弃

        **抢占防抖**：单设备套餐下，如果每次重连都踢掉占用设备，
        而手机端 APP 也在自动重连，双方会互相踢、形成死循环。
        因此重连流程**只做非抢占式认证**；要抢占请用户手动点按钮。
        """
        attempt = 0
        while not self._stop.is_set():
            # 等网络先恢复一点，再试
            self._stop.wait(self.retry_base)

            if probe_online():
                self.log("网络已恢复，结束重连循环")
                return

            attempt += 1
            delay = min(self.retry_base * (2 ** (attempt - 1)), self.retry_max)
            self.log(f"第 {attempt} 次重连尝试（{self.retry_base}s 间隔）")
            try:
                self.on_event(GuardEvent("reconnect", f"第 {attempt} 次重连尝试", ""))
            except Exception as exc:
                self.log(f"重连回调异常: {exc}")

            # 退避等待，同时随时可被停止
            self._stop.wait(min(delay, self.retry_max))
