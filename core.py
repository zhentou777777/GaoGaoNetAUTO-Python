# -*- coding: utf-8 -*-
"""业务控制器 —— 与界面无关的认证核心。

这个模块把「认证 / 设备 / 扫码 / 设置」的业务逻辑从界面层剥离出来：

* ``core.py`` 只管**做什么**，通过回调往外抛事件
* ``webui.py``（浏览器界面）和 ``main.py``（旧版 Tk 界面）都只是**渲染方**

好处：Web 版和 Tk 版的认证行为完全一致 —— 不会出现「网页版认证逻辑
和桌面版不一样」这种最要命的偏差。

事件（``on_event`` 回调，字典）：

===============  ==========================================================
``log``         ``{"text": "..."}``  一条日志
``status``      ``{"state": ..., "text": ..., "color": ..., "badge": ...}``
``info``        ``{}``  状态信息变了，该刷新了
``devices``     ``{"devices": [...], "error": "", "ip": "..."}``
``qr``          ``{"png": "<base64>"}``  二维码图片
``qr_state``    ``{"status": ..., "message": ...}``
``token_state`` ``{"text": "..."}``
===============  ==========================================================
"""
from __future__ import annotations

import queue
import threading
import time
from typing import Callable, Optional

import logs
from auth import (
    DEVICE_LIMIT,
    NOT_AUTH,
    TOKEN_INVALID,
    CampusAuth,
)
from config import Config
from netguard import NetGuard
from qrflow import QrAssistant, SESSION_TTL_MINUTES

# ---------------------------------------------------------------- 状态色
#
# 用**语义名**而不是具体色值往外抛，由界面层决定怎么渲染。
# 这样换主题时不用动业务代码。

STATE_ONLINE = "online"
STATE_WORKING = "working"
STATE_ERROR = "error"
STATE_IDLE = "idle"
STATE_UNKNOWN = "unknown"

# 设备类型 → 人话
DEVICE_KINDS = {
    1: "手机 WIFI 使用中",
    2: "电脑有线使用中",
    3: "电脑 WIFI 使用中",
}


class Controller:
    """认证核心控制器。

    线程模型：所有网络 I/O 都在后台线程跑，结果通过 ``_events`` 队列
    回传给界面层（界面层决定怎么渲染）。这与原 Tk 版的做法一致，
    避免了「主线程卡死」的老问题。
    """

    # 账号密码自动登录熔断参数
    ACCT_BREAKER_THRESHOLD = 3      # 连续失败多少次后熔断
    ACCT_BREAKER_COOLDOWN = 300.0   # 熔断持续秒数（5 分钟）

    def __init__(self, on_event: Optional[Callable[[dict], None]] = None,
                 cfg: Optional[Config] = None):
        self.cfg = cfg or Config()
        self._on_event = on_event
        self._events: "queue.Queue[dict]" = queue.Queue()
        self.auth = CampusAuth(self.cfg, logger=self.log)
        self.guard: Optional[NetGuard] = None
        self._qr_assistant: Optional[QrAssistant] = None
        self._qr_cancel: Optional[threading.Event] = None
        # 状态缓存（避免每次刷新都打网络）
        self._cached_ip = ""
        self._cached_mac = ""
        self._cached_devices = ""
        # 账号密码熔断器状态
        self._acct_fail_count = 0
        self._acct_fail_until = 0.0
        # 忙碌标记：避免用户连点导致并发认证
        self._busy = False
        self._busy_lock = threading.Lock()

    # ------------------------------------------------------------ 事件

    def emit(self, kind: str, **payload):
        """抛一个事件给界面层。"""
        ev = {"kind": kind}
        ev.update(payload)
        self._events.put(ev)

    def poll_events(self, timeout: float = 0.0) -> list:
        """取走已排队的事件（界面层循环调用）。"""
        out = []
        deadline = time.time() + timeout
        while True:
            try:
                out.append(self._events.get_nowait())
            except queue.Empty:
                break
            if timeout <= 0:
                break
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            try:
                out.append(self._events.get(timeout=remaining))
            except queue.Empty:
                break
        return out

    def log(self, msg: str):
        """记一条日志（同时落盘，自动脱敏 + 轮转）。"""
        try:
            logs.info(msg)
        except Exception:
            pass
        self.emit("log", text=f"[{time.strftime('%H:%M:%S')}] {msg}")

    def set_status(self, state: str, text: str, badge: str = ""):
        self.emit("status", state=state, text=text, badge=badge)

    def _set_busy(self, flag: bool) -> bool:
        """设置忙碌标记；已经被占用时返回 False（说明该忽略本次请求）。"""
        with self._busy_lock:
            if flag and self._busy:
                return False
            self._busy = flag
            return True

    # ------------------------------------------------------------ 状态信息

    def info_snapshot(self) -> dict:
        """当前状态（纯读缓存，不发网络请求）。"""
        ip = self._cached_ip
        host = str(self.cfg.get("host", "") or "")
        port = self.cfg.get("port", "")
        return {
            "gateway": self.cfg.base_url,
            "host": host,
            "port": port,
            "scheme": str(self.cfg.get("scheme", "") or ""),
            "ip": ip,
            "mac": self._cached_mac,
            "slot_ip": self.cfg.get("last_ip") or ip,
            "phone_ip": str(self.cfg.get("phone_ip", "") or ""),
            "devices_brief": self._cached_devices,
            "has_token": self.cfg.has_token(),
            "has_account": self.cfg.has_account(),
            "account_masked": self.account_masked(),
            "autostart": bool(self.cfg.get("autostart", False)),
            "auto_kick_phone": bool(self.cfg.get("auto_kick_phone", False)),
            "last_auth_ok": bool(self.cfg.get("last_auth_ok", False)),
        }

    def refresh_info(self):
        """刷新状态信息（后台线程做网络 I/O）。"""
        threading.Thread(target=self._refresh_info_bg, daemon=True).start()

    def _refresh_info_bg(self):
        try:
            ip = self.auth.detect_local_ip()
            mac = self.auth.detect_mac()
            devices = self._devices_brief()
        except Exception as exc:
            self.log(f"状态刷新异常: {exc}")
            return
        self._cached_ip = ip
        self._cached_mac = mac
        self._cached_devices = devices
        self.emit("info")

    def _devices_brief(self) -> str:
        """在线设备一行摘要。"""
        token = self.cfg.get_token()
        if not token:
            return "未设置 Token"
        try:
            devices = self.auth.online_devices(token)
        except Exception:
            return "查询失败"
        if not devices:
            return "当前无在线设备"
        ip = self._cached_ip or self.auth.detect_local_ip()
        others = [str(d.get("ip", "")) for d in devices
                  if str(d.get("ip", "")) and str(d.get("ip", "")) != ip]
        if others:
            return f"{len(devices)} 台在线（其他：{', '.join(others)}）"
        return f"{len(devices)} 台在线"

    def _query_devices(self, token: str) -> list:
        """查在线设备；任何异常都当成「查不到」返回空列表。

        抢占/在线列表这类路径**不能**因为一次网络抖动就抛出去 ——
        调用方拿空列表还能继续走降级流程。
        """
        if not token:
            return []
        try:
            return self.auth.online_devices(token) or []
        except Exception as exc:
            self.log(f"查询在线设备失败: {exc}")
            return []

    def other_devices(self, my_ip: str = "") -> list:
        """列出占用名额的其他设备 IP。"""
        if not my_ip:
            my_ip = self.auth.detect_local_ip()
        devices = self._query_devices(self.cfg.get_token())
        return [str(d.get("ip", "")) for d in devices
                if str(d.get("ip", "")) and str(d.get("ip", "")) != my_ip]

    # ------------------------------------------------------------ 设置

    def save_settings(self, data: dict) -> tuple:
        """保存设置。

        返回 ``(ok, message)``。网关地址先校验白名单 —— 不在白名单内的
        地址会让「保存」变成一次把 Token 发到第三方的隐患，当场拦下。
        """
        host = str(data.get("host", "") or "").strip() or "hgs.auth.yungao-tech.com"
        try:
            port = int(str(data.get("port", "") or "1110").strip() or 1110)
        except ValueError:
            port = 1110
        scheme = str(data.get("scheme", "") or self.cfg.get("scheme") or "http")

        ok, reason = self.cfg.validate_gateway(host, scheme, port)
        if not ok:
            return False, reason

        self.cfg.set("phone_ip", str(data.get("phone_ip", "") or "").strip())
        self.cfg.set("host", host)
        self.cfg.set("port", port)
        self.cfg.set("scheme", scheme)
        self.cfg.set("auto_kick_phone", bool(data.get("auto_kick_phone")))
        self.cfg.set("autostart", bool(data.get("autostart")))
        return True, "设置已保存"

    def save_token(self, raw: str) -> tuple:
        """保存 Token（自动剥掉 ``Bearer `` 前缀）。"""
        token = (raw or "").strip()
        if not token:
            return False, "Token 为空，未保存"
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.cfg.set_token(token)
        return True, "Token 已加密保存"

    def clear_credentials(self) -> tuple:
        """清除本机已保存的所有凭据。"""
        try:
            self.cfg.clear_credentials()
        except Exception as exc:
            return False, f"清除凭据失败：{exc}"
        return True, "已清除本机保存的 Token、手机号与密码"

    def has_credentials(self) -> bool:
        return bool(self.cfg.has_token() or self.cfg.has_account())

    def account_masked(self) -> str:
        """已保存账号的掩码形式（不回显明文）。"""
        mobile, _ = self.cfg.get_account()
        if not mobile:
            return ""
        if len(mobile) >= 7:
            return f"{mobile[:3]}****{mobile[-4:]}"
        return "****"

    # ------------------------------------------------------------ 账号密码

    def _acct_breaker_open(self) -> bool:
        if self._acct_fail_until <= time.time():
            return False
        return True

    def _acct_breaker_trip(self):
        self._acct_fail_count += 1
        if self._acct_fail_count >= self.ACCT_BREAKER_THRESHOLD:
            self._acct_fail_until = time.time() + self.ACCT_BREAKER_COOLDOWN
            left = int(self.ACCT_BREAKER_COOLDOWN)
            self.log(f"账号密码连续失败 {self._acct_fail_count} 次，"
                     f"已暂停自动尝试 {left} 秒（避免触发服务端风控）")

    def _acct_breaker_reset(self):
        self._acct_fail_count = 0
        self._acct_fail_until = 0.0

    def _auto_login_account(self) -> str:
        """用已保存的账号密码换一个 Token。失败返回空串。带熔断。"""
        if self._acct_breaker_open():
            left = int(self._acct_fail_until - time.time())
            self.log(f"账号密码自动登录已熔断，{left} 秒后再试（期间可手动扫码）")
            return ""

        mobile, pwd = self.cfg.get_account()
        if not (mobile and pwd):
            return ""

        try:
            from acclogin import AccountLogin, mask_mobile

            lr = AccountLogin().login(mobile, pwd)
        except Exception as exc:
            self._acct_breaker_trip()
            self.log(f"自动换 Token 异常: {exc}")
            return ""

        if not (lr.ok and lr.token):
            self._acct_breaker_trip()
            self.log(f"自动换 Token 失败：{lr.message}")
            return ""

        self._acct_breaker_reset()
        self.cfg.set_token(lr.token)
        self.log(f"★ 已换取新 Token（账号 {mask_mobile(mobile)}）")
        return lr.token

    def login_with_account(self, mobile: str, pwd: str):
        """用账号密码登录换取 Token 并保存（后台线程）。"""
        threading.Thread(target=self._account_login_bg,
                         args=(mobile, pwd), daemon=True).start()

    def _account_login_bg(self, mobile: str, pwd: str):
        try:
            from acclogin import AccountLogin, mask_mobile

            lr = AccountLogin().login(mobile, pwd)
        except Exception as exc:
            self._acct_breaker_trip()
            self.log(f"账号登录异常: {exc}")
            self.set_status(STATE_ERROR, "账号登录失败")
            return

        if not lr.ok:
            self._acct_breaker_trip()
            self.log(f"账号登录失败：{lr.message}")
            self.set_status(STATE_ERROR, "账号登录失败")
            return

        if not lr.token:
            self._acct_breaker_trip()
            self.log(f"账号登录成功但未取到 Token：{lr.message}")
            self.set_status(STATE_ERROR, "未取到 Token")
            return

        self._acct_breaker_reset()
        self.cfg.set_account(mobile, pwd)
        self.cfg.set_token(lr.token)
        self.log(f"★ 账号 {mask_mobile(mobile)} 登录成功，Token 已保存（已加密）")
        self.log("账号密码已保存——以后 Token 失效会自动换新的，断网也能自愈")
        self.emit("token_state", text="✓ Token 有效")
        self.emit("info")

        # 顺手认证一次，确认真的能上网
        ip = self.auth.detect_local_ip()
        res = self.auth.login_chain(
            ip, lr.token, str(self.cfg.get("phone_ip") or ""),
            bool(self.cfg.get("auto_kick_phone", False)))
        if res.ok:
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.log(f"认证成功：{res.message}")
            self.set_status(STATE_ONLINE, "已认证在线", f"IP {ip}")
        else:
            self.log(f"Token 已保存，但当前认证未成功：{res.message}")

    # ------------------------------------------------------------ Token 体检

    def check_token(self):
        """只读体检（调 onestatus，**不改变在线状态**）。"""
        if not self.cfg.has_token():
            self.log("尚未设置 Token")
            return
        self.log("正在检查 Token…")
        threading.Thread(target=self._check_token_bg, daemon=True).start()

    def _check_token_bg(self):
        ip = self.auth.detect_local_ip()
        res = self.auth.check_token(ip, self.cfg.get_token())
        if res.ok:
            self.emit("token_state", text="✓ Token 有效")
            self.log("Token 校验通过")
        elif res.status == TOKEN_INVALID:
            self.emit("token_state", text="✗ Token 已失效，需要重新抓取")
            self.log("Token 校验失败：服务端拒绝了该 Token")
        elif res.status == DEVICE_LIMIT:
            self.emit("token_state", text="! Token 有效，但名额被其他设备占用")
            self.log("Token 有效，但账号在线名额被其他设备占用")
        else:
            self.emit("token_state", text=f"? 校验未通过：{res.message}")
            self.log(f"Token 校验未通过：{res.message}")

    # ------------------------------------------------------------ 认证

    def login(self, takeover: bool = False):
        """立即认证。

        ``takeover=True`` 会踢掉占用名额的设备（破坏性操作，
        界面层必须先做二次确认）。
        """
        if not self._set_busy(True):
            self.log("已有认证在进行中，请稍候")
            return
        self.set_status(STATE_WORKING, "正在认证…")
        threading.Thread(target=self._login_bg, args=(takeover,),
                         daemon=True).start()

    def _login_bg(self, takeover: bool = False):
        try:
            self._login_bg_inner(takeover)
        except Exception as exc:
            self.log(f"认证流程异常：{exc}")
            self.set_status(STATE_ERROR, "认证流程出错")
        finally:
            self._set_busy(False)
            self.emit("info")

    def _login_bg_inner(self, takeover: bool = False):
        ip = self.auth.detect_local_ip()
        if not ip:
            self.log("未探测到本机出口 IP，请确认已连接校园网")
            self.set_status(STATE_ERROR, "未连接校园网")
            return

        # ── 共享模式（手机热点）────────────────────────────────────
        # 判据必须是**服务端的在线列表**（权威），不能只靠本地
        # 「像不像 NAT」的启发式 —— 那个启发式在直连校园网时同样成立，
        # 会导致本该做的认证被跳过。
        token_now = self.cfg.get_token()
        if token_now and self.auth.is_already_online(ip, token_now):
            self.log("服务端在线列表里已有本机出口 IP —— 已经在线，无需重复认证")
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.set_status(STATE_ONLINE, "已在线", f"出口 {ip}")
            self.emit("info")
            return

        if self.auth.is_nat_mode():
            self.log("检测到本机走手机热点（经 NAT）——将继续确认是否需要认证")

        token = self.cfg.get_token()
        if not token:
            if self.cfg.has_account():
                self.log("没有可用 Token，正在用账号密码自动换取…")
                token = self._auto_login_account()

        if not token:
            self.log("没有 Token，改走扫码流程")
            self.start_qr()
            return

        self.log(f"开始认证，IP={ip}")
        res = self.auth.login_chain_self_heal(
            ip, token, str(self.cfg.get("phone_ip") or ""),
            bool(self.cfg.get("auto_kick_phone", False)),
            self.cfg.get_account(),
            takeover=takeover,
        )

        # 当前 Token 判定失效 → 回退试一次上一版 Token
        if (not res.ok) and res.token_invalid:
            prev = self.cfg.get_token_prev()
            if prev and prev != token:
                self.log("当前 Token 已失效，正在尝试回退到上一版 Token…")
                res2 = self.auth.login_chain_self_heal(
                    ip, prev, str(self.cfg.get("phone_ip") or ""),
                    False,   # 回退不抢占
                    self.cfg.get_account(),
                    takeover=False,
                )
                if res2.ok:
                    self.log("★ 回退到上一版 Token 成功")
                    self.cfg.set_token(prev)
                    res = res2

        # L1 自动续存：服务端下发了新 Token 就立刻换掉。
        # ⚠️ 只有认证**成功**时才存新 Token —— 失败响应里也可能带
        # token 字段，存进去会把本来还能用的 Token 覆盖掉。
        if res.ok and res.new_token and res.new_token != token:
            self.cfg.set("token_prev", self.cfg.get("token"))
            self.cfg.set_token(res.new_token)
            self.log("★ 检测到服务端下发了新 Token，已自动续存")

        if res.ok:
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.log(f"认证成功：{res.stage or 'OK'} {res.message}")

            # 认证成功 ≠ 外网真通（校园网常见 DNS 未生效），实测一次
            time.sleep(1.0)
            from netguard import probe_online

            if probe_online():
                self.set_status(STATE_ONLINE, "已认证在线", f"IP {ip}")
            else:
                self.log("认证已通过，但外网暂时不通——可能是 DNS 未生效，稍后自动重试")
                self.set_status(STATE_WORKING, "已认证，外网未通", f"IP {ip}")
                if self.guard is not None:
                    self.guard.start_reconnect()
        else:
            self.log(f"认证失败：{res.message}")
            if res.stage:
                self.log(f"  过程：{res.stage}")
            if res.status == TOKEN_INVALID:
                self.set_status(STATE_ERROR, "Token 已失效", "建议扫码登录")
            elif res.status == DEVICE_LIMIT:
                others = self.other_devices(ip)
                who = f"被 {others[0]} 占用" if others else "名额被占用"
                self.set_status(STATE_ERROR, who, "点「抢占上线」接管")
            elif res.status == NOT_AUTH:
                self.set_status(STATE_ERROR, "认证接口不可达", "请确认已连校园网")
            else:
                self.set_status(STATE_ERROR, "认证失败")

    # ------------------------------------------------------------ 抢占

    def takeover_preview(self) -> dict:
        """抢占前的确认信息（要踢掉哪些设备）。"""
        token = self.cfg.get_token()
        if not token:
            return {"ok": False, "message": "没有 Token，无法抢占——请先扫码登录或填账号密码"}
        my_ip = self.auth.detect_local_ip()
        devices = self._query_devices(token)
        others = [str(d.get("ip", "")) for d in devices
                  if str(d.get("ip", "")) and str(d.get("ip", "")) != my_ip]
        return {"ok": True, "others": others, "my_ip": my_ip}

    def takeover(self):
        """抢占上线（破坏性，界面层需先确认）。"""
        if not self._set_busy(True):
            self.log("已有操作在进行中，请稍候")
            return
        self.log("正在抢占上线…")
        threading.Thread(target=self._takeover_bg, daemon=True).start()

    def _takeover_bg(self):
        try:
            self._takeover_bg_inner()
        except Exception as exc:
            self.log(f"抢占流程异常：{exc}")
            self.set_status(STATE_ERROR, "抢占流程出错")
        finally:
            self._set_busy(False)
            self.emit("info")

    def _takeover_bg_inner(self):
        token = self.cfg.get_token()
        ip = self.auth.detect_local_ip()

        # 没探测到本机 IP 说明还没连上校园网。此时继续往下走会先把
        # 别人踢下线，结果自己还上不去网 —— 属于「没确认就破坏」。
        if not ip:
            self.log("未探测到本机出口 IP，无法抢占——请先连接校园网")
            self.set_status(STATE_ERROR, "未连接校园网", "请先联网再抢占")
            return

        devices = self._query_devices(token)
        if not devices:
            if not token:
                self.log("没有 Token，且当前没有其他设备在线——改走正常认证流程")
            else:
                self.log("当前没有其他设备在线，直接认证")
            # ⚠️ 不能调 self.login() —— 那会再抢一次忙碌锁，而忙碌锁此刻
            # 已经被 takeover() 持有，于是「已有认证在进行中，请稍候」，
            # 抢占按钮变成静默无效。这里直接跑认证主体（同一后台线程）。
            self._login_bg_inner(takeover=True)
            return

        others = [str(d.get("ip", "")) for d in devices
                  if str(d.get("ip", "")) != ip]
        for other in others:
            self.log(f"占用名额的设备：{other}")

        res = self.auth.login_chain_self_heal(
            ip, token, str(self.cfg.get("phone_ip") or ""),
            bool(self.cfg.get("auto_kick_phone", False)),
            self.cfg.get_account(),
            takeover=True,
        )
        if res.ok:
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.log(f"★ 抢占成功，本机已上线（IP {ip}）")
            self.set_status(STATE_ONLINE, "已抢占上线", f"IP {ip}")
        else:
            self.log(f"抢占失败：{res.message}")
            self.set_status(STATE_ERROR, "抢占失败")

    # ------------------------------------------------------------ 设备

    def refresh_devices(self):
        """刷新在线设备列表（后台线程）。"""
        threading.Thread(target=self._refresh_devices_bg, daemon=True).start()

    def _refresh_devices_bg(self):
        devices: list = []
        err = ""
        ip = ""
        try:
            ip = self.auth.detect_local_ip()
            token = self.cfg.get_token()
            devices = self.auth.online_devices(token)
        except Exception as exc:
            err = str(exc)
        self.emit("devices", devices=devices, error=err, ip=ip)

    def kick_device(self, ip: str):
        """下线指定设备（把名额还给它）。"""
        token = self.cfg.get_token()
        if not token:
            self.log("没有 Token，无法操作在线设备")
            return
        self.log(f"正在下线设备 {ip}…")
        threading.Thread(target=self._kick_device_bg, args=(ip,),
                         daemon=True).start()

    def _kick_device_bg(self, ip: str):
        res = self.auth.logout_ip(ip, self.cfg.get_token())
        if res.ok:
            self.log(f"已下线 {ip}")
            self.refresh_devices()
        else:
            self.log(f"下线 {ip} 失败：{res.message}")

    def logout_self(self):
        """归还名额（只下线**当前**探测到的 IP）。"""
        # 只用**当前**探测到的 IP。历史上优先用 last_ip，但换网络/
        # 校园网重分配地址后那个 IP 可能属于别人的设备 —— 会踢错人。
        ip = self.auth.detect_local_ip()
        if not ip:
            self.log("未探测到本机出口 IP，无法确定要下线哪台设备")
            self.set_status(STATE_ERROR, "未探测到本机 IP", "请确认已连接校园网")
            return
        token = self.cfg.get_token()
        if not token:
            self.log("没有 Token，无法下线")
            return
        self.log(f"正在归还名额（{ip}）…")
        threading.Thread(target=self._logout_bg, args=(ip, token),
                         daemon=True).start()

    def _logout_bg(self, ip: str, token: str):
        res = self.auth.logout_ip(ip, token)
        if res.ok:
            self.log("已下线，名额已归还（手机可以重新认证了）")
            self.set_status(STATE_UNKNOWN, "已下线")
        else:
            self.log(f"下线未成功：{res.message}")

    # ------------------------------------------------------------ 扫码

    def start_qr(self):
        """开始扫码流程（新建会话）。"""
        if self._qr_cancel is not None:
            self._qr_cancel.set()
        cancel = threading.Event()
        self._qr_cancel = cancel
        threading.Thread(target=self._qr_bg, daemon=True).start()

    def cancel_qr(self):
        """取消扫码（关窗 / 刷新）。"""
        if self._qr_cancel is not None:
            self._qr_cancel.set()
        self._qr_cancel = None
        self._qr_assistant = None
        self._cleanup_qr_tmp()

    def _qr_bg(self):
        try:
            self._qr_bg_inner()
        except Exception as exc:
            self.log(f"扫码流程异常：{exc}")
            self.emit("qr_state", status="failed", message=f"扫码流程出错：{exc}")
        finally:
            self._cleanup_qr_tmp()

    def _qr_bg_inner(self):
        cancel = self._qr_cancel
        ip = self.auth.detect_local_ip()
        mac = self.auth.detect_mac()
        assistant = QrAssistant(self.auth, self.log)
        self._qr_assistant = assistant
        try:
            state = assistant.load(ip, mac)
        except Exception as exc:
            self.emit("qr_state", status="failed", message=f"加载扫码页失败：{exc}")
            return
        if not state.ready:
            self.emit("qr_state", status=state.status, message=state.message)
            return

        png = assistant.render_png(str(_qr_tmp_path()))
        self.emit("qr", png=png)
        self.emit("qr_state", status=state.status, message=state.message)

        # 轮询时长与二维码 TTL 对齐（TTL = 10 分钟），每 2 秒一次
        max_polls = (SESSION_TTL_MINUTES * 60) // 2
        for _ in range(max_polls):
            if cancel is not None and cancel.wait(timeout=2):
                return
            try:
                s = assistant.poll(ip, mac)
            except Exception as exc:
                self.emit("qr_state", status="failed", message=f"轮询失败：{exc}")
                return
            self.emit("qr_state", status=s.status, message=s.message)
            if s.status in ("authorized", "expired"):
                if s.status == "authorized":
                    self.log("扫码授权成功，正在确认上线…")
                    token = self.cfg.get_token()
                    if token:
                        # poll() 已尝试过 auth?str=<会话ID>；这里再用 IP
                        # 兜一次，确保服务端把「本机 IP」这一侧真正放行。
                        res = self.auth.auth_by_ip(ip, token)
                        if res.ok:
                            self.log(f"完成认证：{res.message}")
                        else:
                            self.log(f"扫码已授权，但 IP 认证未确认：{res.message}")
                    else:
                        self.log("扫码授权成功，设备已上线（未配置 Token）")
                    self.emit("info")
                break
        else:
            self.emit("qr_state", status="expired",
                      message="二维码已过期，请点「刷新」重新生成")

    @staticmethod
    def _cleanup_qr_tmp():
        """清理临时二维码文件（用后即删，不留在磁盘上）。"""
        try:
            p = _qr_tmp_path()
            if p.exists():
                p.unlink()
        except Exception:
            pass

    # ------------------------------------------------------------ 守护

    def start_guard(self):
        self.guard = NetGuard(self.auth, interval=20, logger=self.log,
                               on_event=self._on_guard_event)
        self.guard.start()

    def _on_guard_event(self, ev):
        """守护线程回调。"""
        if ev.kind == "offline":
            self.set_status(STATE_ERROR, "网络中断", "准备重连")
            # 自动重连**不抢占** —— 否则会和手机互相踢
            self.login(takeover=False)
            if self.guard is not None:
                self.guard.start_reconnect()
        elif ev.kind == "reconnect":
            self.log(ev.detail or "重连尝试")
            self.login(takeover=False)
        elif ev.kind == "ip_changed":
            self.log(f"出口 IP 变化，重新认证以免掉线：{ev.ip}")
            self.login(takeover=False)
        elif ev.kind == "online":
            if self.guard is not None:
                self.guard.stop_reconnect()
            self.emit("info")

    # ------------------------------------------------------------ 开机自启

    def autostart_enabled(self) -> bool:
        from autostart import autostart_enabled

        return autostart_enabled()

    def apply_autostart(self, enable: bool) -> tuple:
        from autostart import apply_autostart, migrate_autostart_legacy

        ok, msg = apply_autostart(enable)
        if ok:
            self.log(msg)
            if enable:
                migrate_autostart_legacy()
        else:
            self.log(msg)
            # 没写成就不把开关留在「开」的状态 —— 否则每次启动都要再失败一次，
            # 用户还会以为自启已经生效了。
            if enable:
                self.cfg.set("autostart", False)
                self.emit("info")
        return ok, msg

    def sync_autostart(self):
        """把「启动文件夹里的实际文件」与「配置里的开关」对齐。"""
        from autostart import autostart_stale

        want = bool(self.cfg.get("autostart", False))
        have = self.autostart_enabled()
        if want and not have:
            self.apply_autostart(True)
            self.log("已按配置补建开机自启（启动文件夹）")
        elif have and not want:
            self.apply_autostart(False)
            self.log("已按配置清理开机自启")
        elif want and have and autostart_stale():
            # 文件在但内容过期（程序换了位置）—— 开机静默启动一个不存在的
            # 路径，比没有自启更难排查。重写一次。
            self.log("开机自启条目已过期（程序路径变了），正在重写…")
            self.apply_autostart(True)

    def delayed_login(self, seconds: int):
        """延迟若干秒后自动认证（开机时网络尚未就绪）。"""
        def _worker():
            time.sleep(seconds)
            self.login(takeover=False)

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------ 启动检查

    def startup_checks(self):
        self.emit("info")
        self.sync_autostart()
        if self.cfg.has_token():
            # 只读体检：走 onestatus，不会改变在线状态、不占名额
            self.check_token()
            if self.cfg.get("autostart"):
                self.log("开机自启已开启，正在自动认证上线…")
                self.delayed_login(8)
            else:
                self.log("已设置 Token，点「立即认证」即可上线")
        elif self.cfg.has_account():
            self.log("已配置账号密码，开机自启时会自动登录换取 Token…")
            if self.cfg.get("autostart"):
                self.delayed_login(8)
            else:
                self.log("点「立即认证」即可上线")
        else:
            self.log("尚未设置 Token，可点「扫码登录」先连上网，或在设置里填账号密码")


def _qr_tmp_path():
    from config import data_dir

    return data_dir() / "qr_tmp.png"
