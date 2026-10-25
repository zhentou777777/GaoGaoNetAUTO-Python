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
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

try:
    import requests
except Exception:  # pragma: no cover - 打包环境异常兜底
    requests = None  # type: ignore

try:
    import netprobe_win
except Exception:  # pragma: no cover - 打包环境异常兜底
    netprobe_win = None  # type: ignore

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
# 明确的失败措辞 —— 即使 code 是 0/200，只要出现这些词就不能算成功。
_FAIL_WORDS = (
    "失败", "错误", "异常", "error", "fail", "exception", "denied",
    "拒绝", "无效参数", "参数错误",
)


def _looks_like_token(s: str) -> bool:
    """粗判一个字符串像不像 Bearer Token。

    要求：长度合理、只含 Token 常见字符、且**有足够熵**（不是自然语言）。
    这样能挡住「错误信息里的一串中文/英文单词被当成 Token」。
    """
    if not s or len(s) < 20:
        return False
    allowed = set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_."
    )
    if not all(c in allowed for c in s):
        return False
    # 必须混有大写/小写/数字中的至少两类，避免纯英文单词被误判
    has_upper = any(c.isupper() for c in s)
    has_lower = any(c.islower() for c in s)
    has_digit = any(c.isdigit() for c in s)
    if sum([has_upper, has_lower, has_digit]) < 2:
        return False
    return True


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

    def transport_is_secure(self) -> bool:
        """网关是否走 HTTPS。

        云高校园网网关历史上只提供 HTTP（端口 1110），所以**多数情况
        必然是 False**。程序不会因此拒绝工作（否则整个工具不可用），
        但会：
        * 在界面上明确提示「Token 以明文经过内网」；
        * 每次带 Token 的请求都记一条日志，避免用户不知情。
        """
        return str(self.cfg.get("scheme", "")).strip().lower() == "https"

    def transport_warning(self) -> str:
        """返回传输层风险提示文本（无风险时返回空串）。"""
        if self.transport_is_secure():
            return ""
        host = str(self.cfg.get("host") or "")
        return (
            f"网关 {host} 使用 HTTP，Bearer Token 以明文经过校园网内网。"
            "这是云高网关的既有形态，本工具无法单方面升级为 HTTPS。"
        )

    def gateway_allowed(self) -> Tuple[bool, str]:
        """校验网关地址是否在白名单内（防止 Token 被发到任意地址）。"""
        try:
            return self.cfg.gateway_valid()
        except Exception:
            return True, ""

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

    def detect_gateway(self) -> str:
        """探测本机默认网关（走哪条链路出去）。

        用途：判断当前是「直连校园网」还是「经手机热点 NAT」——
        后者本机 IP 是 192.168.x.x，而云高只认出口 IP。

        实现走 ``iphlpapi`` 的 ctypes 封装（:mod:`netprobe_win`），
        **不创建任何子进程**（``ipconfig`` 会被 Defender 盯上）。
        """
        if netprobe_win is None:
            return ""
        try:
            # 优先取「承载本机出口 IP 的那张网卡」的网关，比全局默认网关准
            ip = self.detect_local_ip()
            if ip:
                gw = netprobe_win.gateway_for_ip(ip)
                if gw:
                    return gw
            return netprobe_win.default_gateway()
        except Exception:
            return ""

    def detect_exit_ip(self) -> str:
        """探测「出口 IP」——即外网看到的本机地址。

        与 :meth:`detect_local_ip` 的区别：后者是本机网卡地址（192.168.x.x），
        前者是经过 NAT 之后的公网地址。手机热点场景下两者不同，
        云高认证必须用出口 IP。

        用轻量 HTTP 回显服务查询，失败返回空串（不阻塞主流程）。
        结果缓存 60 秒，避免频繁外呼。
        """
        now = time.time()
        cached = getattr(self, "_exit_ip_cache", None)
        if isinstance(cached, tuple) and now - cached[0] < 60:
            return cached[1]
        ip = ""
        if requests is not None:
            for url in ("https://api.ipify.org",
                        "https://ipinfo.io/ip",
                        "https://myip.ipip.net"):
                try:
                    resp = requests.get(url, timeout=3)
                    text = (resp.text or "").strip()
                    m = re.search(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b", text)
                    if m:
                        ip = m.group(1)
                        break
                except Exception:
                    continue
        self._exit_ip_cache = (now, ip)
        return ip

    def is_nat_mode(self) -> bool:
        """判断本机是否处于 NAT 之后（典型场景：连手机热点）。

        ⚠️ 这个判断**只能作为提示**，不能单独用来决定「跳过认证」。

        历史实现用「出口 IP ≠ 本机 IP」判定 NAT，但这在**直连校园网时
        同样成立**：校园网给电脑分配的是 10.x 内网地址（10.135.x.x），
        而外网看到的出口是校园网的公网地址 —— 两者当然不同。
        于是这个判据在直连时也几乎恒为 True，导致程序误以为「在走手机热点」
        而**跳过本该做的认证**，表现为「明明断了网却一直显示已连接」。

        现在改成保守判据：只有当本机地址是**家庭/热点网段**
        （192.168.x.x / 10.x / 172.16–31.x，且网关也是私有地址）
        且出口地址确实是**公网地址**时，才判为 NAT。
        判不出来就返回 False —— **宁可多认证一次，也不要跳过认证**。
        """
        local_ip = self.detect_local_ip()
        exit_ip = self.detect_exit_ip()
        if not local_ip or not exit_ip:
            return False
        if exit_ip == local_ip:
            return False   # 出口就是本机 → 必然不是 NAT
        # 本机必须是私有地址，且不能是校园网常见的 10.135.x.x 段
        if not _is_private_ipv4(local_ip):
            return False
        if local_ip.startswith("10.135."):
            return False   # 云高校园网内网段，不是热点
        # 出口必须是公网地址
        if _is_private_ipv4(exit_ip):
            return False
        # 网关也应是私有地址（路由器/手机）
        gw = self.detect_gateway()
        if gw and not _is_private_ipv4(gw):
            return False
        return True

    def is_already_online(self, ip: str, token: str) -> bool:
        """权威判据：服务端在线列表里**已经有**本机出口 IP。

        这是判断「是否需要再认证一次」的**唯一可靠依据** ——
        服务端说什么就是什么，不靠本地启发式猜。

        典型场景：手机热点下，手机认证后整条 NAT 链路都被放行，
        服务端在线列表里就有这个出口 IP，PC 不需要再认证一次
        （再认证反而会因为单设备限制把手机挤掉）。
        """
        if not ip or not token:
            return False
        try:
            devices = self.online_devices(token)
        except Exception:
            return False
        for d in devices or []:
            if str(d.get("ip", "")) == ip:
                return True
        return False

    def detect_mac(self) -> str:
        """取当前活动网卡的真实 MAC（按出口 IP 精确匹配）。"""
        ip = self.detect_local_ip()
        if ip and netprobe_win is not None:
            try:
                mac = netprobe_win.mac_for_ip(ip)
                if mac:
                    return mac
            except Exception:
                pass
        return self._mac_fallback()

    def _mac_fallback(self) -> str:
        """兜底取 MAC：优先取活动网卡的物理地址，其次用 uuid.getnode()。"""
        if netprobe_win is not None:
            try:
                ip = self.detect_local_ip()
                adapters = netprobe_win.adapters()
                # 优先选「有 IP 且带 MAC」的网卡里索引最小的那张
                cands = [a for a in adapters
                         if a.get("mac") and (a.get("ip") or a.get("extra_ips"))]
                if cands:
                    if ip:
                        for a in cands:
                            if a.get("ip") == ip or ip in (a.get("extra_ips") or []):
                                return str(a["mac"])
                    cands.sort(key=lambda a: int(a.get("index") or 0))
                    return str(cands[0]["mac"])
            except Exception:
                pass
        try:
            node = uuid_getnode()
            raw = f"{node:012X}"
            return ":".join(raw[i:i + 2] for i in range(0, 12, 2))
        except Exception:
            return ""

    # ------------------------------------------------------------ 请求封装

    def _get(self, url: str, token: str = "") -> Tuple[Optional[int], str, Dict[str, str]]:
        # ⚠️ 安全闸门：带 Token 的请求必须确认网关在白名单内。
        # 否则配置文件被篡改（或用户误填）时，Bearer Token 会被发到
        # 任意第三方地址。这道闸门放在最前面 —— 先拦下，再谈能不能发。
        if token:
            ok, reason = self.gateway_allowed()
            if not ok:
                self.log(f"已阻止认证请求：{reason}")
                return None, f"网关地址不在白名单内，已阻止发送 Token（{reason}）", {}
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

            # ── 顺序很重要 ────────────────────────────────────────
            # 历史实现的判定顺序是「success=True → OK」在前，
            # 「code=0/200 → OK」在后。后果是：
            #   {"code":500,"success":true,"msg":"服务器错误"}  → 被判成功
            #   {"code":0,"msg":"token无效"}                    → 被判成功
            # 现在改成：**先判失败，再判成功**。

            # 1) 明确的状态码优先（410 失效 / 409 设备占用）
            if code is not None:
                if code in (410, "410"):
                    return TOKEN_INVALID, msg or "登录状态已失效"
                if code in (409, "409"):
                    return DEVICE_LIMIT, msg or "套餐只支持单设备认证，已有设备在线"

            # 2) 失败文案优先 —— 即使 code 是 0/200，
            #    只要明确说了"无效/已满/失败"，就不能算成功。
            low_msg = msg.lower()
            if msg and (
                any(w in msg for w in _TOKEN_BAD_WORDS)
                or any(w in low_msg for w in _TOKEN_BAD_WORDS)
            ):
                return TOKEN_INVALID, msg
            if msg and any(w in msg for w in _DEVICE_LIMIT_WORDS):
                return DEVICE_LIMIT, msg
            if msg and any(w in low_msg for w in _FAIL_WORDS):
                return UNKNOWN_ERR, msg or "服务端返回失败"

            # 3) 显式失败标志
            if success is False:
                return UNKNOWN_ERR, msg or "服务端返回失败"

            # 4) 只有在「没有失败信号」时才判成功
            if success is True:
                return OK, msg or "认证成功"
            if code in (0, "0", 200, "200"):
                return OK, msg or "认证成功"

            if code is not None:
                return UNKNOWN_ERR, msg or f"服务端返回 code={code}"

        # 纯文本响应
        for w in _TOKEN_BAD_WORDS:
            if w in low:
                return TOKEN_INVALID, f"响应提示：{w}"
        for w in _DEVICE_LIMIT_WORDS:
            if w in low:
                return DEVICE_LIMIT, f"响应提示：{w}"
        # 失败措辞优先于成功措辞（"失败"里也可能含 "ok" 之类子串）
        if any(w in low for w in ("fail", "失败", "error", "错误", "异常")):
            return UNKNOWN_ERR, "服务端返回失败"
        if any(w in low for w in ("success", "成功")):
            return OK, "认证成功"
        # 空响应 / 2xx 但没有任何状态线索 → **不能**当成成功。
        # 历史实现在这里直接返回 OK，导致「服务端说设备已满」被当成认证成功，
        # 程序继续往下走而不做清理/降级。
        if not text.strip():
            return UNKNOWN_ERR, "服务端返回空响应，无法确认认证结果"
        if http_code is not None and 200 <= http_code < 300:
            return UNKNOWN_ERR, ("服务端未返回可识别的状态，"
                                 "无法确认认证是否成功")
        if http_code is not None:
            return HTTP_ERR, f"HTTP {http_code}"
        return NETWORK_ERR, "无法连接认证服务器"

    @staticmethod
    def _extract_new_token(body: str, headers: Dict[str, str]) -> Optional[str]:
        """从响应里找服务端新下发的 Token（L1 自动续存）。

        认证成功后服务端常会回一个轮换后的 Token，拿到它就能一直续命，
        不必重新抓包。

        ⚠️ 抓取要**保守**：历史上是「body 里任意 20–4096 字符的字符串」都算
        Token，结果失败响应里的错误信息、CSRF token、refresh token 都会被
        当成 Bearer Token，把本来还能用的 Token 覆盖掉。

        现在只接受：
          * 明确命名的字段（access_token / id_token / token / bearer …）
          * 或形如 JWT 的三段式结构
        并且**只在认证成功时**才使用（调用方保证）。
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
                # 明确命名的 token 字段
                r'"(?:access_?token|id_?token|bearer_?token|auth_?token)"\s*:\s*"([^"]{20,})"',
                r"'(?:access_?token|id_?token|bearer_?token|auth_?token)'\s*:\s*'([^']{20,})'",
                # Bearer 头形式
                r"Bearer\s+([A-Za-z0-9\-._~+/]{20,}=*)",
            ):
                for m in re.finditer(pattern, body):
                    candidates.append(m.group(1))

        for cand in candidates:
            cand = (cand or "").strip()
            if not (20 <= len(cand) <= 4096):
                continue
            if cand.lower().startswith("v1:"):
                continue
            # JWT 形态（三段式）——最可信
            if cand.count(".") == 2 and all(c.isalnum() or c in "-_." for c in cand):
                return cand
            # 明确字段名来源的（candidates 前半段来自 header / 命名字段）
            if cand in candidates[:len(candidates)] and _looks_like_token(cand):
                return cand
        return None

    # ------------------------------------------------------------ 认证动作

    def auth_by_ip(self, ip: str, token: str) -> AuthResult:
        """按 IP 认证（免扫码主路径）。"""
        url = self._url("/api/network/auth", {"ip": ip})
        code, body, headers = self._get(url, token)
        status, msg = self._classify(code, body, headers)
        # 只在**认证成功**时才提取新 Token —— 失败响应里的错误信息、
        # CSRF token 等绝不能当成 Bearer Token 保存。
        new_token = (self._extract_new_token(body, headers)
                     if status == OK else None)
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
        # 只在**认证成功**时才提取新 Token —— 失败响应里的错误信息、
        # CSRF token 等绝不能当成 Bearer Token 保存。
        new_token = (self._extract_new_token(body, headers)
                     if status == OK else None)
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
        takeover: bool = False,
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

        # 3) 自动抢占：踢掉占用名额的设备
        # 只在 takeover=True 时启用（用户主动点了「立即认证」/「抢占上线」）。
        # 自动重连场景不抢占，否则会和手机互相踢，形成"抢登录"死循环。
        if takeover:
            for d in devices:
                other = str(d.get("ip", ""))
                if other and other != ip:
                    self.log(f"抢占：下线占用设备 {other}")
                    self.logout_ip(other, token)
            res = self.auth_by_ip(ip, token)
            if res.ok:
                res.stage = "名额被占→自动抢占→认证成功"
                return res
            attempts.append(f"抢占后仍失败：{res.message}")

        # 4) 顶替指定手机 IP
        if kick_phone and phone_ip:
            self.logout_ip(phone_ip, token)
            res = self.auth_by_ip(ip, token)
            if res.ok:
                res.stage = f"顶替手机（{phone_ip}）后认证成功"
                return res
            attempts.append(f"顶替手机后仍失败：{res.message}")

        # 5) 明确归因
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
                    "【想两台同时用】让电脑连手机热点（同一出口 IP），"
                    "手机认证后电脑直接能上网，无需再认证\n"
                    "【想让电脑独占】点「抢占上线」，本机会主动下线对方并接管"
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
        """轻量体检：只验证 Token 是否还能被服务端接受（**不改变在线状态**）。

        ⚠️ 这里**必须**用只读接口。

        历史实现用的是 ``/api/network/auth?ip=...`` —— 那是**认证**接口，
        会真的把本机推上线。在单设备套餐上，这意味着「打开程序看一眼」
        就把手机踢了、占掉了唯一名额。

        现在改用 ``/api/network/onestatus?ip=...``：只查询某 IP 的在线状态，
        不发起认证、不改变任何设备的在线状态。
        """
        url = self._url("/api/network/onestatus", {"ip": ip})
        code, body, headers = self._get(url, token)
        status, msg = self._classify(code, body, headers)
        # onestatus 返回的是「状态查询」语义，单独判读 code 字段
        if status == OK:
            try:
                payload = json.loads(body or "{}")
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                c = payload.get("code", payload.get("status"))
                if c in (401, 403, "401", "403"):
                    return AuthResult(
                        ok=False, status=TOKEN_INVALID,
                        message="服务端拒绝该 Token（401/403）",
                        detail=(body or "")[:400],
                        stage="token 健康检查", http_code=code)
                if c in (410, "410"):
                    return AuthResult(
                        ok=False, status=TOKEN_INVALID,
                        message="登录状态已失效",
                        detail=(body or "")[:400],
                        stage="token 健康检查", http_code=code)
        return AuthResult(
            ok=status == OK,
            status=status,
            message=msg,
            detail=(body or "")[:400],
            stage="token 健康检查（只读）",
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

        ⚠️ 失败时返回空列表，**但语义是"查不到"而不是"没有设备"**。
        历史上网络错误 / Token 失效 / JSON 解析失败统统返回 []，
        UI 会显示「当前没有设备在线」，用户据此做共享/抢占判断就全错了。
        需要区分时请用 ``online_devices_result()``。
        """
        return self.online_devices_result(token)[0]

    def online_devices_result(self, token: str
                              ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """同 ``online_devices``，但额外返回错误原因（None = 成功）。

        返回 ``(devices, error)``；error 非 None 时 devices 不可信。
        """
        url = self._url("/api/network/online", {})
        try:
            code, body, headers = self._get(url, token)
        except Exception as exc:
            return [], f"查询在线设备失败：{exc}"

        if code is None:
            return [], "无法连接认证服务器"
        if code in (401, 403):
            return [], "Token 已失效（服务端拒绝）"

        try:
            payload = json.loads(body or "{}")
        except Exception:
            return [], "服务端返回的不是合法 JSON"

        if not isinstance(payload, dict):
            return [], "服务端返回结构异常"

        # 服务端用 code 表达业务错误时，不能当成"没有设备"
        scode = payload.get("code", payload.get("status"))
        if scode is not None and scode not in (0, "0", 200, "200"):
            msg = str(payload.get("msg", payload.get("message", "")))
            return [], f"服务端返回 code={scode} {msg}".strip()

        data = payload.get("data")
        if data is None:
            data = payload.get("list") or payload.get("devices")
        if not isinstance(data, list):
            return [], "服务端未返回设备列表字段"
        return [d for d in data if isinstance(d, dict)], None

    def describe_devices(self, token: str, my_ip: str = "") -> str:
        """把在线设备列表格式化成一行人话，用于状态卡显示。"""
        devices, err = self.online_devices_result(token)
        if err:
            # 区分「查不到」和「真的没有设备」——
            # 前者是故障，不能让用户以为「没人占名额，可以直接连」。
            return f"设备状态未知（{err}）"
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
        takeover: bool = False,
    ) -> AuthResult:
        """四级认证链 + 账号密码兜底换 Token。

        这是「开机自启 / 断网自动重连」真正闭环的关键：
        光重放旧 Token，在 Token 被服务端轮换后会一直失败；
        只有能**自己换到一个新 Token** 才叫自愈。

        流程：
            1) 四级认证链（直连 → 清理本机会话 → 顶替手机）
            2) 若失败且判定为 Token 失效 → 用账号密码换新 Token → 重跑认证链
        """
        res = self.login_chain(ip, token, phone_ip, kick_phone, takeover=takeover)

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
        res2 = self.login_chain(ip, lr.token, phone_ip, kick_phone,
                                takeover=takeover)
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


def _ip_to_int(ip: str) -> int:
    try:
        parts = [int(x) for x in str(ip).split(".")]
        if len(parts) != 4:
            return -1
        if any(p < 0 or p > 255 for p in parts):
            return -1
        return (parts[0] << 24) | (parts[1] << 16) | (parts[2] << 8) | parts[3]
    except Exception:
        return -1


def _is_private_ipv4(ip: str) -> bool:
    """判断是否为 RFC1918 私有地址。"""
    n = _ip_to_int(ip)
    if n < 0:
        return False
    if (n >> 24) == 10:
        return True
    second = (n >> 16) & 0xFF
    if (n >> 24) == 172 and 16 <= second <= 31:
        return True
    if (n >> 24) == 192 and second == 168:
        return True
    return False
