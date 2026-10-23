# -*- coding: utf-8 -*-
"""账号密码登录自检：确认云高登录接口的真实字段与 Token 位置。

用法：
    python login_check.py <手机号> <密码>

成功时会打印：
  * 登录接口实际返回的 JSON（Token 字段已定位）
  * 拿到的 Token 长度与前缀
  * 用该 Token 调 auth?ip= 的认证结果

失败时会打印服务端原始应答与错误分类，便于对照修正。
密码不会写入任何文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acclogin import AccountLogin, mask_mobile  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python login_check.py <手机号> <密码>")
        return 1

    mobile = sys.argv[1].strip()
    password = sys.argv[2]

    print(f"· 登录目标 : https://user.v4.yungao-tech.com/api/login")
    print(f"· 账号     : {mask_mobile(mobile)}")
    print()

    al = AccountLogin()
    res = al.login(mobile, password)

    print("── 服务端原始应答 ──")
    try:
        print(json.dumps(json.loads(res.raw), ensure_ascii=False, indent=2))
    except Exception:
        print(res.raw or "(空)")
    print()

    print("── 解析结果 ──")
    print(f"  成功    : {res.ok}")
    print(f"  分类    : {res.kind}")
    print(f"  说明    : {res.message}")
    if res.token:
        print(f"  Token   : 长度 {len(res.token)}，前 24 字符 {res.token[:24]}…")
    print()

    if not res.ok:
        return 2

    if not res.token:
        print("✗ 登录成功但没定位到 Token 字段，需要人工看一下上面的 JSON 结构。")
        return 3

    # 用新 Token 试着认证一次
    print("── 用新 Token 试认证 ──")
    from auth import CampusAuth
    from config import Config

    cfg = Config()
    auth = CampusAuth(cfg, logger=lambda m: print("  LOG:", m))
    ip = auth.detect_local_ip()
    print(f"  本机出口 IP: {ip}")

    r = auth.auth_by_ip(ip, res.token)
    print(f"  auth?ip= → ok={r.ok} status={r.status}")
    print(f"  说明: {r.message}")
    print(f"  原文: {r.detail[:200]}")
    print()

    if r.ok:
        print("★ 完整链路打通：账号密码 → Token → 认证上线")
        print("  把账号密码填进程序的「账号密码」栏即可永久免抓包。")
        return 0

    print("? Token 拿到了，但认证未成功。可能是单设备名额被占（手机在线），")
    print("  或该 Token 不是用于校园网认证的类型。")
    return 4


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover
        print(f"出错: {exc}")
        sys.exit(1)
