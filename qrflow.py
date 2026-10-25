# -*- coding: utf-8 -*-
"""扫码助手：一条完全不依赖 Token 的联网路径。

设计定位：**扫码助手是永远可用的基础能力，Token 只是加速器**。
当 Token 失效/轮换时，本模块仍能让用户在 30 秒内恢复联网——用手机扫一下。

逆向结论（2026-10 对 hgs.auth.yungao-tech.com:1110 实测）：

1. ``GET /web/network/code`` 会 301 到自身并下发 ``Set-Cookie: str=<UUID>``，
   这个 UUID 就是本次授权会话的标识，10 分钟有效。
2. 页面把二维码内容用 **XOR 混淆** 内嵌在 JS 里::

       var encoded = [253,172,255, ...];
       var key    = 158;
       var str = encoded.map(function (c) {
           return String.fromCharCode(c ^ key);
       }).join('');

   解码结果与 ``str`` cookie **完全一致**（UUID 形式）。
   因此二维码可以**纯本地渲染**，不必去扒服务端图片。
3. 页面里 **没有任何状态轮询接口**——手机在 APP 里确认后，
   服务端直接把「该 session 对应的 IP」置为已上线。
   所以判定「授权是否完成」的正确信号不是轮询接口，而是
   **本机是否已经能连通外网**（见 :func:`netguard.probe_online`）。

流程：
1. 加载 ``/web/network/code`` 页面，取出 ``str``（cookie 或 XOR 解码）；
2. 用 segno 本地生成二维码 PNG（拿不到载荷时才回退服务端图片）；
3. 轮询：轮询会话是否被服务端轮换（→ 过期）＋ 本机是否已联网（→ 授权成功）。
"""
from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Union

# 扫码页里可能出现的"二维码内容"字段名（按优先级）
_QR_VAR_NAMES = (
    "code", "qrCode", "qrcode", "qrUrl", "qr_url", "qrLink",
    "authUrl", "auth_url", "url", "link", "token",
)

# 扫码页状态判定。
#
# ⚠️ 这里**不能**用 "success" 这类泛词做全文子串匹配：
# 扫码页的 JS 里出现 `var success = true;` 之类是家常便饭，
# 全文匹配会得出「还没扫就授权成功」的假阳性。
#
# 现在的做法：
#   1) 先尝试**结构化**提取状态字段（status/state/code 变量或 JSON 字段）；
#   2) 只有结构化提取失败时，才退回中文状态文案匹配，
#      且成功词只用中文/明确英文短语，不用裸 "success"。
_STATUS_OK = ("已认证", "认证成功", "已上线", "已通过", "授权成功",
              "已登录", "auth_success", "authorized")
_STATUS_SCANNED = ("已扫描", "等待确认", "请在手机上确认", "已扫码")
_STATUS_EXPIRED = ("已过期", "已失效", "二维码失效", "expired", "请刷新")

# 结构化状态字段名（按优先级）
_STATUS_FIELD_NAMES = ("status", "state", "scanStatus", "qrStatus",
                        "authStatus", "result")

# 结构化状态值 → 内部状态
_STATUS_VALUE_MAP = {
    "authorized": "authorized", "auth_success": "authorized",
    "success": "authorized", "ok": "authorized", "confirmed": "authorized",
    "scanned": "scanned", "waiting_confirm": "scanned",
    "pending": "scanned",
    "expired": "expired", "invalid": "expired", "timeout": "expired",
    "waiting": "waiting", "pending_scan": "waiting", "created": "waiting",
}

# 二维码会话有效期（分钟）——与服务端 str cookie 的 10 分钟一致
SESSION_TTL_MINUTES = 10


@dataclass
class QrState:
    code: str = ""
    payload: str = ""
    image_bytes: Optional[bytes] = None
    status: str = "waiting"
    message: str = "等待扫描"

    @property
    def ready(self) -> bool:
        return bool(self.payload or self.image_bytes)


# ------------------------------------------------------------ 载荷解析


def decode_xor_payload(html: str) -> str:
    """把页面里 XOR 混淆的二维码内容还原出来。

    形如::

        var encoded = [253,172,255, ...];
        var key    = 158;

    返回解码后的明文（UUID）；解析不出来返回空串。
    """
    if not html:
        return ""
    m = re.search(r"var\s+encoded\s*=\s*\[([^\]]*)\]", html)
    if not m:
        return ""
    km = re.search(r"var\s+key\s*=\s*(\d+)", html)
    if not km:
        return ""
    try:
        key = int(km.group(1))
        arr = [int(x) for x in re.findall(r"-?\d+", m.group(1))]
    except Exception:
        return ""
    if not arr:
        return ""
    try:
        return "".join(chr(c ^ key) for c in arr)
    except Exception:
        return ""


def extract_qr_payload(html: str) -> str:
    """从扫码页 HTML 里取出二维码承载的内容（优先 XOR 解码）。"""
    if not html:
        return ""
    decoded = decode_xor_payload(html)
    if decoded:
        return decoded
    for name in _QR_VAR_NAMES:
        m = re.search(rf'var\s+{re.escape(name)}\s*=\s*"([^"]+)"', html)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def extract_code(html: str) -> str:
    """取出 var code 的值（兼容老页面结构）。"""
    m = re.search(r'var\s+code\s*=\s*"([^"]+)"', html)
    return m.group(1) if m else ""


def extract_status(html: str) -> str:
    """从扫码页里**结构化**提取状态，返回内部状态名或空串。

    优先找 JS 变量（``var status = "authorized"``）和 JSON 字段
    （``{"status": "authorized"}``）。这样避免把页面里任意位置出现的
    ``success`` 字样误判成「已授权」。

    提取不到时返回空串，由调用方退回文案匹配。
    """
    if not html:
        return ""

    # 1) JSON 片段
    for m in re.finditer(r"\{[^{}]{0,400}\}", html):
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(obj, dict):
            for name in _STATUS_FIELD_NAMES:
                val = obj.get(name)
                if isinstance(val, str):
                    st = _STATUS_VALUE_MAP.get(val.strip().lower())
                    if st:
                        return st

    # 2) JS 变量
    for name in _STATUS_FIELD_NAMES:
        m = re.search(rf'var\s+{re.escape(name)}\s*=\s*"([^"]*)"', html)
        if m:
            st = _STATUS_VALUE_MAP.get(m.group(1).strip().lower())
            if st:
                return st

    # 3) JSON 里以引号包裹的字段（不严格是合法 JSON 的情况）
    for name in _STATUS_FIELD_NAMES:
        m = re.search(rf'"{re.escape(name)}"\s*:\s*"([^"]*)"', html)
        if m:
            st = _STATUS_VALUE_MAP.get(m.group(1).strip().lower())
            if st:
                return st

    return ""


def find_qr_image(html: str) -> Optional[Union[bytes, str]]:
    """尝试直接取出服务端渲染好的二维码图片。

    返回：
      * ``bytes`` —— 内联 base64 的图片数据
      * ``str``   —— 外链图片 URL（需再下载）
      * ``None``  —— 页面里没有现成的图片
    """
    if not html:
        return None

    # 1) 内联 base64
    for m in re.finditer(r'src\s*=\s*["\']data:image/(\w+);base64,([^"\']+)["\']', html):
        try:
            return base64.b64decode(m.group(2))
        except Exception:
            continue

    # 2) 外链 URL（优先名字里带 qr/code 的图片）
    urls = re.findall(r'src\s*=\s*["\']([^"\']+\.(?:png|jpg|jpeg|gif))["\']', html, re.I)
    for url in urls:
        if "qr" in url.lower() or "code" in url.lower():
            return url
    return None


class QrAssistant:
    """二维码助手：拉取 → 渲染 → 轮询。"""

    def __init__(self, auth, logger=None):
        self.auth = auth
        self.log = logger or (lambda *a, **k: None)
        self.state = QrState()
        # 本机在开始扫码前是否已经在线——用来区分"扫完码上线了"和"本来就在线"
        self._online_before = False
        self._session_uuid = ""
        # 二维码生成时刻——用于判断是否超过服务端有效期（10 分钟）
        self._created_at = 0.0
        # 轮询期间服务端会因重新拉页面而不断下发新的 str cookie，
        # 这里把原始的会话 UUID 固定下来，避免被覆盖。
        self._session_cookie = ""
        # 初始页面 HTML 缓存 —— 轮询阶段**不再**重新拉 code 页，
        # 只在需要时重新请求状态接口。
        self._initial_html = ""

    # ------------------------------------------------------------ 拉取

    def load(self, ip: str, mac: str) -> QrState:
        """加载扫码页并准备二维码内容。"""
        from netguard import probe_online

        code, html = self.auth.fetch_code_page(ip, mac)
        self.state = QrState()
        self.state.code = code or extract_code(html)
        self.state.payload = extract_qr_payload(html)
        # 缓存初始页面，供轮询阶段做状态判定（不再重复请求 code 页）
        self._initial_html = html or ""

        # str cookie 是服务端认的会话标识，优先用它
        cookie_uuid = ""
        try:
            cookie_uuid = self.auth._session.cookies.get("str") or ""
        except Exception:
            cookie_uuid = ""
        self._session_uuid = cookie_uuid or self.state.payload or self.state.code
        self._session_cookie = cookie_uuid

        img = find_qr_image(html)
        if isinstance(img, bytes):
            self.state.image_bytes = img
        elif isinstance(img, str) and img:
            self.state.image_bytes = self._download(img)
            if not self.state.image_bytes:
                # 外链取不到就退回用 payload 本地生成
                self.state.payload = self.state.payload or self.state.code

        # 没有载荷但有服务端图片也照样能用
        if not self.state.payload and not self.state.image_bytes:
            self.state.status = "failed"
            self.state.message = "未能解析出二维码内容（页面结构可能已变化）"
            return self.state

        # 载荷缺失但有图片时，仍然需要 str 作为"完成认证"的 str 参数
        if not self.state.payload:
            self.state.payload = self._session_uuid

        self._online_before = probe_online()
        self._created_at = time.time()
        self.state.status = "waiting"
        self.state.message = "请用糕糕 APP 扫描二维码"
        return self.state

    def _download(self, url: str) -> Optional[bytes]:
        try:
            if url.startswith("//"):
                url = "http:" + url
            elif url.startswith("/"):
                url = self.auth.base_url.rstrip("/") + url
            elif not url.startswith("http"):
                url = self.auth.base_url.rstrip("/") + "/" + url.lstrip("/")
            resp = self.auth._session.get(url, timeout=self.auth.timeout)
            if resp.status_code == 200:
                return resp.content
        except Exception:
            pass
        return None

    # ------------------------------------------------------------ 渲染

    def render_png(self, path: str, scale: int = 8) -> Optional[str]:
        """生成二维码 PNG；返回文件路径，失败返回 None。"""
        payload = self.state.payload or self.state.code
        if not payload:
            return None
        try:
            import io

            import segno

            qr = segno.make(payload)
            # segno 1.6: save(out, kind=...) 需要可写对象或文件路径
            buf = io.BytesIO()
            qr.save(buf, kind="png", scale=scale, border=2,
                    dark="#000000", light="#FFFFFF")
            data = buf.getvalue()
            with open(path, "wb") as fh:
                fh.write(data)
            return path
        except Exception as exc:
            self.log(f"二维码渲染失败: {exc}")
            # 退回到直接写文件
            try:
                import segno

                segno.make(payload).save(path, scale=scale, border=2)
                return path
            except Exception:
                return None

    # ------------------------------------------------------------ 轮询

    def poll(self, ip: str, mac: str) -> QrState:
        """检查扫码结果。

        页面无轮询接口，因此用两个信号判断：

        * **联网成功**：本机由「离线」变为「能连通外网」→ 服务端已放行本 session；
        * **会话过期**：重新拉页面时服务端换了新的 ``str`` UUID → 旧二维码作废。
        """
        if self.state.status in ("authorized", "expired", "failed"):
            return self.state

        from netguard import probe_online

        # --- 信号 1（最强）：带 Token 调 auth?str=<会话ID> ---
        # 这正是网页扫码流程的最后一跳：APP 授权后，PC 用同一个 str 确认上线。
        # 服务端认的 str 就是 cookie/XOR 载荷里的那个 UUID。
        token = ""
        try:
            token = self.auth.cfg.get_token() or ""
        except Exception:
            token = ""
        if token and self._session_uuid:
            try:
                res = self.auth.auth_by_code(self._session_uuid, token)
                if res.ok:
                    self.state.status = "authorized"
                    self.state.message = "已授权，设备已上线"
                    return self.state
            except Exception:
                pass

        # --- 信号 2：本机是否已联网（零 Token 场景下的完成信号）---
        if not self._online_before and probe_online():
            self.state.status = "authorized"
            self.state.message = "已授权，设备已上线"
            return self.state

        # --- 信号 3：会话是否被服务端作废（超时）---
        # 注意：每次请求 /web/network/code 服务端都会下发**新的** str，
        # 所以不能用"UUID 变了"判断过期——那会永远误判。
        # 真正的过期判据是"距生成超过服务端给的 10 分钟有效期"。
        if self._created_at and (time.time() - self._created_at) > SESSION_TTL_MINUTES * 60:
            self.state.status = "expired"
            self.state.message = "二维码已过期，请刷新"
            return self.state

        # --- 信号 4：页面状态 ---
        # ⚠️ 这里**不能**在轮询里重新拉 /web/network/code：
        #   每次请求服务端都会下发新的 str，会让正在显示的二维码/会话失效，
        #   也会浪费请求。轮询阶段只用缓存的初始页面 + 结构化状态提取。
        html = self._initial_html or ""
        if html:
            # 3.1) 先做结构化状态提取（可靠）
            st = extract_status(html)
            if st == "authorized":
                self.state.status = "authorized"
                self.state.message = "已授权，正在为设备上线"
                return self.state
            if st == "expired":
                self.state.status = "expired"
                self.state.message = "二维码已过期，请刷新"
                return self.state
            if st == "scanned":
                self.state.status = "scanned"
                self.state.message = "已扫描，请在手机上确认"
                return self.state
            if st == "waiting":
                self.state.status = "waiting"
                self.state.message = "请用糕糕 APP 扫描二维码"
                return self.state

            # 3.2) 结构化提取不到，才退回中文文案匹配
            #     （成功词只用中文/明确短语，不用裸 "success"）
            low = html.lower()
            for kw in _STATUS_OK:
                if kw.lower() in low:
                    self.state.status = "authorized"
                    self.state.message = "已授权，正在为设备上线"
                    return self.state
            for kw in _STATUS_EXPIRED:
                if kw.lower() in low:
                    self.state.status = "expired"
                    self.state.message = "二维码已过期，请刷新"
                    return self.state
            for kw in _STATUS_SCANNED:
                if kw.lower() in low:
                    self.state.status = "scanned"
                    self.state.message = "已扫描，请在手机上确认"
                    return self.state

        if self._online_before:
            self.state.status = "waiting"
            self.state.message = "请在 APP 中确认授权，如已确认请稍候…"
        else:
            self.state.status = "waiting"
            self.state.message = "请用糕糕 APP 扫描二维码"
        return self.state
