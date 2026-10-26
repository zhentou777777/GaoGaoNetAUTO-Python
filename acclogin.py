# -*- coding: utf-8 -*-
"""账号密码直接登录：免抓包、免手机的 Token 获取路径。

逆向结论（2026-10 实测 user.v4.yungao-tech.com）：

    POST /api/login
    Content-Type: application/json
    {"mobile": "<手机号>", "password": "<密码>"}
    → 200 {"code":0, "msg":"登录成功", "result":{...}}

- ``mobile`` + ``password`` 是**唯一必需**的两个字段（换成别的键名会返回
  400「参数不能为空」；只有 ``mobile`` 会被识别并进入手机号校验）。
- 错误码语义：
  - ``400 参数不能为空``  → 字段名不对
  - ``408 手机号不合法``  → 手机号格式错
  - ``401 账号不存在``    → 该手机号在云高没注册
  - ``401 密码错误``      → 账号存在但密码不对
- 成功时 Token 在 ``result`` 里（字段名待真实账号确认，见 ``_pick_token``）。

这条路径的价值：**Token 可以自己续**，不必再抓包。
断网 → 用账号密码重新换 Token → 重新认证，形成闭环。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36"
)

# 云高统一用户中心（不同学校共用同一套账号体系）
USER_API = "https://user.v4.yungao-tech.com"

# 响应里可能承载 Token 的字段名（按优先级）
_TOKEN_KEYS = (
    "token", "accessToken", "access_token", "idToken", "id_token",
    "jwt", "authorization", "userToken", "user_token", "authToken",
)

# 错误文案 → 归类
ERR_BAD_PARAMS = "bad_params"
ERR_BAD_MOBILE = "bad_mobile"
ERR_NO_ACCOUNT = "no_account"
ERR_BAD_PASSWORD = "bad_password"
ERR_NEED_CAPTCHA = "need_captcha"
ERR_NEED_SMS = "need_sms"
ERR_UNKNOWN = "unknown"


@dataclass
class LoginResult:
    ok: bool = False
    token: str = ""
    message: str = ""
    kind: str = ERR_UNKNOWN
    raw: str = ""

    @property
    def bad_params(self) -> bool:
        return self.kind == ERR_BAD_PARAMS

    @property
    def bad_mobile(self) -> bool:
        return self.kind == ERR_BAD_MOBILE

    @property
    def no_account(self) -> bool:
        return self.kind == ERR_NO_ACCOUNT

    @property
    def bad_password(self) -> bool:
        return self.kind == ERR_BAD_PASSWORD


def _pick_token(payload: Any) -> str:
    """从成功响应里递归找出 Token 字段。"""
    if isinstance(payload, dict):
        for key in _TOKEN_KEYS:
            val = payload.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, dict):
                inner = _pick_token(val)
                if inner:
                    return inner
        # result 容器里再找一层
        for key in ("result", "data", "user", "userInfo", "info"):
            if key in payload:
                inner = _pick_token(payload[key])
                if inner:
                    return inner
        # 兜底：任意看起来像 JWT 的长字符串
        for val in payload.values():
            if isinstance(val, str) and len(val) > 40 and val.count(".") == 2:
                return val.strip()
    return ""


def _classify(payload: Dict[str, Any]) -> str:
    """把登录失败应答归类。

    ⚠️ 判定顺序有讲究：「账号不存在」的文案里同时含「手机号」，
    先判「手机号」会把 ERR_NO_ACCOUNT 误报成「手机号格式不正确」，
    用户会以为号码写错了而不是账号没注册。
    """
    msg = str(payload.get("msg", ""))
    if "验证码" in msg or "captcha" in msg.lower():
        return ERR_NEED_CAPTCHA
    if "短信" in msg or "sms" in msg.lower():
        return ERR_NEED_SMS
    if "不存在" in msg or "未注册" in msg:
        return ERR_NO_ACCOUNT
    if "密码" in msg:
        return ERR_BAD_PASSWORD
    if "参数" in msg or "不能为空" in msg:
        return ERR_BAD_PARAMS
    if "手机号" in msg or "号码" in msg:
        return ERR_BAD_MOBILE
    return ERR_UNKNOWN


class AccountLogin:
    """账号密码换 Token。"""

    def __init__(self, api_base: str = USER_API, timeout: int = 10):
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session() if requests else None
        if self._session:
            self._session.headers.update({
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Accept": "application/json",
            })

    def login(self, mobile: str, password: str) -> LoginResult:
        """用手机号 + 密码登录换取 Token。"""
        mobile = (mobile or "").strip()
        password = password or ""
        if not mobile or not password:
            return LoginResult(
                ok=False, kind=ERR_BAD_PARAMS,
                message="手机号或密码不能为空",
            )
        if self._session is None:
            return LoginResult(ok=False, kind=ERR_UNKNOWN, message="requests 库不可用")

        url = f"{self.api_base}/api/login"
        try:
            resp = self._session.post(
                url,
                json={"mobile": mobile, "password": password},
                timeout=self.timeout,
                allow_redirects=False,
            )
        except Exception as exc:
            return LoginResult(ok=False, kind=ERR_UNKNOWN, message=f"网络请求失败: {exc}")

        body = resp.text or ""
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}

        code = payload.get("code", payload.get("status"))
        msg = str(payload.get("msg", payload.get("message", "")))

        # 成功判定
        if code in (0, "0", 200, "200"):
            token = _pick_token(payload)
            if token:
                return LoginResult(ok=True, token=token, message=msg or "登录成功",
                                   raw=body)
            # code 成功但没找到 token —— 把响应原样带回去便于排查
            return LoginResult(ok=False, kind=ERR_UNKNOWN,
                               message=msg or "登录成功但响应中没有 Token 字段",
                               raw=body)

        kind = _classify(payload)
        friendly = {
            ERR_BAD_PARAMS: "接口参数不匹配（云高可能改了登录接口）",
            ERR_BAD_MOBILE: "手机号格式不正确",
            ERR_NO_ACCOUNT: "该手机号在云高平台不存在——请确认是否用手机号登录，"
                            "或改用「扫码登录」/ 抓包取 Token",
            ERR_BAD_PASSWORD: "密码错误",
            ERR_NEED_CAPTCHA: "需要图形验证码（该接口不支持直接登录）",
            ERR_NEED_SMS: "需要短信验证码（该接口不支持直接登录）",
        }.get(kind, msg or f"登录失败（code={code}）")
        return LoginResult(ok=False, kind=kind, message=friendly, raw=body)


def mask_mobile(mobile: str) -> str:
    """手机号脱敏，用于日志显示。"""
    m = (mobile or "").strip()
    if len(m) >= 7:
        return f"{m[:3]}****{m[-4:]}"
    return m
