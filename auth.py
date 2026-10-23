# -*- coding: utf-8 -*-
"""云高校园网认证核心。

接口语义（逆向自 JasonYANG170/GaoGaoNetAUTO 的 Qt 源码）：

    GET /web/network/code?...&clientip=&clientmac=&wlanacname=  -> 页面含 var code="..."
    GET /api/network/auth?str=<code>   + Authorization: Bearer <token>
    GET /api/network/auth?ip=<设备IP>    + Authorization: Bearer <token>   <- 免扫码主路径
    GET /api/network/logout?token=&ip=<ip> + Authorization: Bearer <token>

服务端只认请求头里的 Bearer Token；``code`` 只是网页流程中传递
"这次授权给哪台设备"的一次性载体。因此 ``auth?ip=`` 可以整环跳过 code，
这正是"免手机"的技术依据。
"""
from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    import requests
except Exception:  # pragma: no cover - 打包环境异常兜底
    requests = None  # type: ignore

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 结果分类
OK = "ok"
TOKEN_INVALID = "token_invalid"
DEVICE_LIMIT = "device_limit"
NETWORK_ERR = "network_err"
HTTP_ERR = "http_err"
NOT_AUTH = "not_auth"
UNKNOWN_ERR = "unknown_err"

_TOKEN_BAD_WORDS = (
    "token无效", "token invalid", "invalid token", "token已失效", "token失效",
    "token过期", "token expired", "未登录", "登录已失效", "请重新登录",
    "认证已失效", "凭证无效", "unauthorized", "forbidden", "token错误",
    # 云高统一的失效应答：{"code":410,"msg":"登录状态已失效，您的账号可能在别处登录。"}
    "登录状态已失效", "账号可能在别处登录", "登录状态失效", "登录已过期",
)
_DEVICE_LIMIT_WORDS = (
    "设备已满", "设备数量", "超出设备", "设备上限", "最多允许", "设备受限",
    "已有设备", "其他设备", "重复登录", "已顶替", "被顶替", "设备冲突",
    "已达到上限", "在线设备", "下线其它设备", "请下线",
)


@dataclass
class AuthResult:
    """一次认证调用的结果。"""

    ok: bool = False
    status: str = UNKNOWN_ERR
    message: str = ""
    detail: str = ""
    stage: str = ""
    new_token: Optional[str] = None
    http_code: Optional[int] = None
    attempts: List[str] = field(default_factory=list)

    @property
    def token_invalid(self) -> bool:
        return self.status == TOKEN_INVALID

    @property
    def device_limit(self) -> bool:
        return self.status == DEVICE_LIMIT


class CampusAuth:
    """云高统一认证客户端。"""

    def __init__(self, cfg, timeout: int = 8, logger=None):
        self.cfg = cfg
        self.timeout = timeout
        self.log = logger or (lambda *a, **k: None)
        self._session = requests.Session() if requests else None
        if self._session:
            self._session.headers.update({"User-Agent": USER_AGENT})

    # ------------------------------------------------------------ 基础地址

    @property
    def base_url(self) -> str:
        return self.cfg.base_url

    def _url(self, path: str, params: Dict[str, str]) -> str:
        base = self.base_url.rstrip("/")
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items())
        return f"{base}{path}" + (f"?{query}" if query else "")

    def _headers(self, token: str) -> Dict[str, str]:
        headers: Dict[str, str] = {"Accept": "*/*", "Connection": "keep-alive"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ------------------------------------------------------------ 网络探测

    def detect_local_ip(self) -> str:
        """探测本机通往认证网关的出口 IP。

        用 UDP connect 选定路由（不实际发包），比遍历网卡更准，
        能自动挑中承载校园网的那张网卡。
        """
        host = str(self.cfg.get("host"))
        port = int(self.cfg.get("port"))
        ip = ""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.5)
            s.connect((host, port))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = ""
        if not ip or ip.startswith("127."):
            ip = self._fallback_ip()
        return ip

    def _fallback_ip(self) -> str:
        """UDP 探测失败时的兜底：找出默认出口网卡地址。"""
        best = ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1.0)
                s.connect(("223.5.5.5", 53))
                best = s.getsockname()[0]
        except Exception:
            pass
        if best:
            return best
        try:
            host = socket.gethostname()
            for info in socket.getaddrinfo(host, None, socket.AF_INET):
                addr = info[4][0]
                if not addr.startswith("127."):
                    return addr
        except Exception:
            pass
        return ""

    def detect_mac(self) -> str:
        """取当前活动网卡的真实 MAC（Windows 下按 IP 精确匹配）。"""
        ip = self.detect_local_ip()
        if ip:
            mac = self._mac_by_ip(ip)
            if mac:
                return mac
        return self._mac_fallback()

    def _mac_by_ip(self, ip: str) -> str:
        """解析 ipconfig 输出，把 IPv4 与其所属网卡的物理地址配对。"""
        if sys.platform != "win32":
            return ""
        try:
            out = subprocess.run(
                ["ipconfig", "/all"],
                capture_output=True,
                text=True,
                timeout=8,
                errors="ignore",
            ).stdout
        except Exception:
            return ""
        mac_re = re.compile(r"(?<![\w-])([0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5})(?![\w-])")
        ip_re = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
        for block in re.split(r"\r?\n\s*\r?\n", out):
            if ip not in ip_re.findall(block):
                continue
            macs = mac_re.findall(block)
            if macs:
                return macs[0][0].replace("-", ":").upper()
        return ""

    def _mac_fallback(self) -> str:
        try:
            node = uuid_getnode()
            raw = f"{node:012X}"
            return ":".join(raw[i:i + 2] for i in range(0, 12, 2))
        except Exception:
            return ""

    # ------------------------------------------------------------ 请求封装

    def _get(self, url: str, token: str = "") -> Tuple[Optional[int], str, Dict[str, str]]:
        if self._session is None:
            return None, "requests 库不可用", {}
        try:
            resp = self._session.get(
                url, headers=self._headers(token), timeout=self.timeout, allow_redirects=True
            )
            try:
                text = resp.text
            except Exception:
                text = resp.content.decode("utf-8", "ignore")
            headers = dict(resp.headers)
            # 记录是否被重定向——校外访问认证主机会 301 到公网站点，
            # 若不识别就会把门户首页误判成"认证成功"。
            if getattr(resp, "history", None):
                headers["__redirected__"] = "1"
                headers["__final_url__"] = str(resp.url)
            return resp.status_code, text, headers
        except Exception as exc:
            return None, f"网络请求失败: {exc}", {}

    # ------------------------------------------------------------ 结果判定

    def _classify(self, http_code: Optional[int], body: str,
                  headers: Optional[Dict[str, str]] = None) -> Tuple[str, str]:
        """把服务端返回归类为 OK / TOKEN_INVALID / DEVICE_LIMIT / ... 。"""
        text = (body or "").strip()
        low = text.lower()
        headers = headers or {}

        # 被重定向到别的站点（多半是未连接校园网，被门户挡到了公网站点）
        if headers.get("__redirected__"):
            final = headers.get("__final_url__", "")
            return NOT_AUTH, f"请求被重定向到 {final}，不是认证接口响应（请确认已连接校园网）"

        if http_code in (401, 403):
            return TOKEN_INVALID, "服务端拒绝该 Token（401/403）"

        # 认证接口却回了 HTML —— 同样是"没打在认证服务上"
        if "<html" in low or "<!doctype html" in low:
            return NOT_AUTH, "认证接口返回了网页内容（可能被门户拦截，请确认已连接校园网）"

        # 优先看 JSON 字段
        payload: Any = None
        try:
            payload = json.loads(text)
        except Exception:
            payload = None

        if isinstance(payload, dict):
            code = payload.get("code", payload.get("status", payload.get("errcode")))
            msg = str(
                payload.get("msg", payload.get("message", payload.get("errmsg", "")))
            )
            success = payload.get("success")
            if success is True:
                return OK, msg or "认证成功"
            if code in (0, "0", 200, "200") and success is not False:
                return OK, msg or "认证成功"
            if code is not None:
                # 云高用 410 统一表示"登录状态已失效"（Token 过期/未认证）
                if code in (410, "410"):
                    return TOKEN_INVALID, msg or "登录状态已失效"
                # 409 = 套餐只允许单设备，且已有设备在线（实测）
                if code in (409, "409"):
                    return DEVICE_LIMIT, msg or "套餐只支持单设备认证，已有设备在线"
                low_msg = msg.lower()
                if any(w in msg for w in _TOKEN_BAD_WORDS) or any(
                    w in low_msg for w in _TOKEN_BAD_WORDS
                ):
                    return TOKEN_INVALID, msg
                if any(w in msg for w in _DEVICE_LIMIT_WORDS):
                    return DEVICE_LIMIT, msg
                return UNKNOWN_ERR, msg or f"服务端返回 code={code}"

        # 纯文本响应
        for w in _TOKEN_BAD_WORDS:
            if w in low:
                return TOKEN_INVALID, f"响应提示：{w}"
        for w in _DEVICE_LIMIT_WORDS:
            if w in low:
                return DEVICE_LIMIT, f"响应提示：{w}"
        if any(w in low for w in ("success", "成功", "ok")):
            return OK, "认证成功"
        if any(w in low for w in ("fail", "失败", "error", "错误")):
            return UNKNOWN_ERR, "服务端返回失败"
        if http_code is not None and 200 <= http_code < 300:
            return OK, "已提交（服务端未返回明确状态）"
        if http_code is not None:
            return HTTP_ERR, f"HTTP {http_code}"
        return NETWORK_ERR, "无法连接认证服务器"

    @staticmethod
    def _extract_new_token(body: str, headers: Dict[str, str]) -> Optional[str]:
        """从响应里找服务端新下发的 Token（L1 自动续存）。

        认证成功后服务端常会回一个轮换后的 Token，拿到它就能一直续命，
        不必重新抓包。
        """
        candidates: List[str] = []

        for key in ("authorization", "x-token", "x-auth-token", "x-access-token"):
            val = None
            for hk, hv in headers.items():
                if hk.lower() == key:
                    val = hv
                    break
            if val:
                m = re.search(r"Bearer\s+([^\s,;\"']+)", val)
                candidates.append(m.group(1) if m else val)

        if body:
            for pattern in (
                r'"(?:access_?token|id_?token|token)"\s*:\s*"([^"]{12,})"',
                r"'(?:access_?token|id_?token|token)'\s*:\s*'([^']{12,})'",
                r"Bearer\s+([A-Za-z0-9\-._~+/]{20,}=*)",
            ):
                for m in re.finditer(pattern, body):
                    candidates.append(m.group(1))

        for cand in candidates:
            cand = (cand or "").strip()
            if 20 <= len(cand) <= 4096 and not cand.lower().startswith("v1:"):
                return cand
        return None

    # ------------------------------------------------------------ 认证动作

    def auth_by_ip(self, ip: str, token: str) -> AuthResult:
        """按 IP 认证（免扫码主路径）。"""
        url = self._url("/api/network/auth", {"ip": ip})
        code, body, headers = self._get(url, token)
        status, msg = self._classify(code, body, headers)
        new_token = self._extract_new_token(body, headers)
        return AuthResult(
            ok=status == OK,
            status=status,
            message=msg,
            detail=(body or "")[:800],
            stage="auth?ip=",
            new_token=new_token,
            http_code=code,
        )

    def auth_by_code(self, code: str, token: str) -> AuthResult:
        """按 code 认证（网页扫码流程的最后一跳）。"""
        url = self._url("/api/network/auth", {"str": code})
        code_, body, headers = self._get(url, token)
        status, msg = self._classify(code_, body, headers)
        new_token = self._extract_new_token(body, headers)
        return AuthResult(
            ok=status == OK,
            status=status,
            message=msg,
            detail=(body or "")[:800],
            stage="auth?str=",
            new_token=new_token,
            http_code=code_,
        )

    def logout_ip(self, ip: str, token: str) -> AuthResult:
        """把某个 IP 下线（归还名额 / 清理陈旧会话）。"""
        url = self._url("/api/network/logout", {"token": "", "ip": ip})
        code, body, headers = self._get(url, token)
        status, msg = self._classify(code, body, headers)
        return AuthResult(
            ok=status == OK,
            status=status,
            message=msg,
            detail=(body or "")[:400],
            stage=f"logout?ip={ip}",
            http_code=code,
        )

    def fetch_code_page(self, ip: str, mac: str) -> Tuple[str, str]:
        """加载 /web/network/code 页面，返回 (会话标识, 页面 HTML)。

        服务端会 301 到自身并下发 ``Set-Cookie: str=<UUID>``，这个 UUID
        就是本次授权会话的标识，也是二维码里承载的内容（页面用 XOR 混淆）。
        老页面结构里还有 ``var code="..."``，作为兼容回退。
        """
        params = {
            "type": "17",
            "wlanuserip": ip,
            "wlanacname": str(self.cfg.get("wlan_acname") or ""),
            "clientip": ip,
            "clientmac": mac,
            "paip": str(self.cfg.get("paip") or ""),
            "vlan": str(self.cfg.get("vlan") or "0.0"),
        }
        custom = str(self.cfg.get("code_page_url") or "")
        url = custom or self._url("/web/network/code", params)
        code, body, _ = self._get(url, "")
        if code is None:
            return "", body or ""

        # 优先取 str cookie（服务端真正的会话标识）
        session_id = ""
        try:
            if self._session is not None:
                session_id = self._session.cookies.get("str") or ""
        except Exception:
            session_id = ""

        if not session_id:
            # 回退：XOR 解码页面里的二维码载荷
            try:
                from qrflow import decode_xor_payload

                session_id = decode_xor_payload(body)
            except Exception:
                session_id = ""

        if not session_id:
            m = re.search(r'var\s+code\s*=\s*"([^"]+)"', body)
            session_id = m.group(1) if m else ""

        return session_id, body

    # ------------------------------------------------------------ 认证链

    def login_chain(
        self,
        ip: str,
        token: str,
        phone_ip: str = "",
        kick_phone: bool = True,
    ) -> AuthResult:
        """四级认证链：直连 -> 清本机陈旧会话 -> 顶替手机 -> 明确报错。

        **共享模式**（单设备套餐下不挤掉手机）：
        云高按**出口 IP** 记录在线设备。若本机出口 IP 已经出现在服务端
        在线列表里，说明这台设备（可能是经手机热点共享同一出口）**已经在线**，
        此时直接判定成功，绝不执行 logout —— 这样就不会把手机踢下线。
        """
        attempts: List[str] = []

        # 0) 共享模式：本机出口 IP 已在服务端在线列表中 → 已经在线
        try:
            devices = self.online_devices(token)
        except Exception:
            devices = []
        for d in devices:
            if str(d.get("ip", "")) == ip:
                kind = {1: "手机 WIFI", 2: "电脑有线", 3: "电脑 WIFI"}.get(
                    d.get("type"), "设备"
                )
                return AuthResult(
                    ok=True,
                    status=OK,
                    message=f"本机出口 IP 已在服务端在线列表中（{kind}），无需重复认证",
                    detail=str(d),
                    stage="共享模式：检测到本机已在线",
                    http_code=200,
                )

        # 1) 直连
        res = self.auth_by_ip(ip, token)
        if res.ok:
            return res
        attempts.append(f"直连失败：{res.message}")

        # 2) 清掉本机陈旧会话后重试
        self.logout_ip(ip, token)
        res = self.auth_by_ip(ip, token)
        if res.ok:
            res.stage = "直连失败→清理本机会话后重试成功"
            return res
        attempts.append(f"清理本机会话后仍失败：{res.message}")

        # 3) 顶替手机
        if kick_phone and phone_ip:
            self.logout_ip(phone_ip, token)
            res = self.auth_by_ip(ip, token)
            if res.ok:
                res.stage = f"顶替手机（{phone_ip}）后认证成功"
                return res
            attempts.append(f"顶替手机后仍失败：{res.message}")

        # 4) 明确归因
        res.attempts = attempts
        res.stage = " / ".join(attempts)
        if res.status == TOKEN_INVALID:
            res.message = "Token 已失效（可能因在 APP 内退出账号或发生设备登录）→ 请重新抓取 Token"
        elif res.status == DEVICE_LIMIT:
            others = [str(d.get("ip", "")) for d in devices
                      if str(d.get("ip", "")) != ip]
            if others:
                res.message = (
                    f"套餐只支持单设备，账号当前被 {', '.join(others)} 占用。\n"
                    "· 想两台同时用：让电脑连手机热点（同一出口 IP），只认证一次\n"
                    "· 想独占：填上手机 IP 开启自动顶替，或点「归还名额」手动切换"
                )
            else:
                res.message = (
                    "账号在线名额被其他设备占用 → 已在设置里填入手机 IP 以便自动顶替"
                )
        elif res.status == NOT_AUTH:
            res.message = "认证接口不可达或被门户拦截 → 请确认已连接校园网"
        else:
            res.message = f"认证失败：{res.message}"
        return res

    def check_token(self, ip: str, token: str) -> AuthResult:
        """轻量体检：只验证 Token 是否还能被服务端接受（不改变在线状态）。"""
        url = self._url("/api/network/auth", {"ip": ip})
        code, body, headers = self._get(url, token)
        status, msg = self._classify(code, body, headers)
        return AuthResult(
            ok=status == OK,
            status=status,
            message=msg,
            detail=(body or "")[:400],
            stage="token 健康检查",
            http_code=code,
        )

    # ------------------------------------------------------------ 在线设备

    def online_devices(self, token: str) -> List[Dict[str, Any]]:
        """查询账号当前在线的设备列表。

        云高 APP 里「查看当前设备」用的就是这个接口。
        返回形如 ``[{"ip": "10.135.130.202", "type": 2}]``。

        type 含义（逆向自 APP 模板文案）：
            1 = 手机 WIFI 使用中
            2 = 电脑有线使用中
            3 = 电脑 WIFI 使用中
        """
        url = self._url("/api/network/online", {})
        code, body, headers = self._get(url, token)
        if code is None:
            return []
        try:
            payload = json.loads(body or "{}")
        except Exception:
            return []
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return []
        return [d for d in data if isinstance(d, dict)]

    def one_status(self, ip: str, token: str) -> bool:
        """查询某个 IP 当前是否已在线。"""
        url = self._url("/api/network/onestatus", {"ip": ip})
        code, body, headers = self._get(url, token)
        if code is None:
            return False
        try:
            payload = json.loads(body or "{}")
        except Exception:
            return False
        return isinstance(payload, dict) and payload.get("code") in (200, "200")

    def describe_devices(self, token: str, my_ip: str = "") -> str:
        """把在线设备列表格式化成一行人话，用于状态卡显示。"""
        devices = self.online_devices(token)
        if not devices:
            return "当前无在线设备"
        parts: List[str] = []
        for d in devices:
            ip = str(d.get("ip", "?"))
            t = d.get("type")
            kind = {1: "手机 WIFI", 2: "电脑有线", 3: "电脑 WIFI"}.get(t, "未知设备")
            tag = "（本机）" if ip == my_ip else ""
            parts.append(f"{kind} {ip}{tag}")
        return " | ".join(parts)

    # ------------------------------------------------------------ 自愈认证

    def login_chain_self_heal(
        self,
        ip: str,
        token: str,
        phone_ip: str = "",
        kick_phone: bool = True,
        account: Tuple[str, str] = ("", ""),
    ) -> AuthResult:
        """四级认证链 + 账号密码兜底换 Token。

        这是「开机自启 / 断网自动重连」真正闭环的关键：
        光重放旧 Token，在 Token 被服务端轮换后会一直失败；
        只有能**自己换到一个新 Token** 才叫自愈。

        流程：
            1) 四级认证链（直连 → 清理本机会话 → 顶替手机）
            2) 若失败且判定为 Token 失效 → 用账号密码换新 Token → 重跑认证链
        """
        res = self.login_chain(ip, token, phone_ip, kick_phone)

        if res.ok:
            return res

        # 只有 Token 失效才值得换新 Token
        if res.status != TOKEN_INVALID:
            return res

        mobile, password = account
        if not mobile or not password:
            res.message = (
                "Token 已失效，且未配置账号密码 → "
                "请在设置里填账号密码以实现自动续期，或用「扫码登录」"
            )
            return res

        try:
            from acclogin import AccountLogin, mask_mobile

            self.log(f"Token 已失效，尝试用账号 {mask_mobile(mobile)} 换取新 Token…")
            lr = AccountLogin().login(mobile, password)
        except Exception as exc:
            res.message = f"Token 已失效，且自动换 Token 失败: {exc}"
            return res

        if not lr.ok:
            res.message = f"Token 已失效，自动换 Token 失败：{lr.message}"
            res.stage = (res.stage + " | " if res.stage else "") + "换 Token 失败"
            return res

        if not lr.token:
            res.message = "Token 已失效，自动换 Token 失败：响应中没有 Token 字段"
            return res

        self.log("★ 已用账号密码换取新 Token，重新认证…")
        res2 = self.login_chain(ip, lr.token, phone_ip, kick_phone)
        if res2.ok:
            res2.stage = "Token 失效→自动换新 Token→认证成功"
            res2.new_token = lr.token
            return res2

        res2.message = f"已换取新 Token 但认证仍失败：{res2.message}"
        res2.stage = ("Token 失效→自动换新 Token→仍失败 | "
                      + (res2.stage or ""))
        res2.new_token = lr.token
        return res2


def uuid_getnode() -> int:
    import uuid

    return uuid.getnode()
