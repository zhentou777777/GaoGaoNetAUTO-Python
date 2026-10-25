# -*- coding: utf-8 -*-
"""账号密码登录自检：确认云高登录接口的真实字段与 Token 位置。

用法：
    python login_check.py                 # 交互式输入手机号与密码（推荐）
    python login_check.py <手机号>        # 手机号走命令行，密码仍交互输入
    python login_check.py <手机号> <密码>  # 不推荐：密码会出现在进程列表与
                                          # shell 历史里，仅供自动化测试用

**为什么默认改成交互输入**：命令行参数会被 ``ps``/任务管理器/审计日志
看到，等于把口令暴露给同机的其他账号。交互输入不落任何日志。

成功时会打印：
  * 登录接口实际返回的 JSON（Token 字段已定位，值已掩码）
  * 拿到的 Token 长度与前缀
  * 用该 Token 调 auth?ip= 的认证结果

失败时会打印服务端原始应答与错误分类，便于对照修正。
密码不会写入任何文件，也不会出现在命令行里。
"""
from __future__ import annotations

import getpass
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acclogin import AccountLogin, mask_mobile  # noqa: E402


def _read_secret(prompt: str) -> str:
    """交互式读取口令（不回显、不进历史）。"""
    try:
        return getpass.getpass(prompt)
    except Exception:
        # 非 TTY（重定向、CI）时退回普通输入
        return sys.stdin.readline().rstrip("\n")


def _mask_token(token: str) -> str:
    """Token 只展示长度与极短片段，不整段打印。"""
    if not token:
        return "(空)"
    return f"长度 {len(token)}，前 8 字符 {token[:8]}… 末 4 字符 {token[-4:]}"


def main() -> int:
    if len(sys.argv) >= 3:
        mobile = sys.argv[1].strip()
        password = sys.argv[2]
        print("! 你把密码放在了命令行里 —— 同机的其他账号可以通过进程列表看到它。")
    elif len(sys.argv) == 2:
        mobile = sys.argv[1].strip()
        password = _read_secret("云高密码: ")
    else:
        mobile = input("云高手机号: ").strip()
        password = _read_secret("云高密码: ")

    if not mobile or not password:
        print("手机号或密码为空，已退出。")
        return 1

    print(f"· 登录目标 : https://user.v4.yungao-tech.com/api/login")
    print(f"· 账号     : {mask_mobile(mobile)}")
    print()

    al = AccountLogin()
    res = al.login(mobile, password)

    print("── 服务端原始应答 ──")
    try:
        raw = json.loads(res.raw)
        # 应答里可能直接带 Token —— 打码后再显示
        if isinstance(raw, dict):
            for key in list(raw.keys()):
                val = raw[key]
                if isinstance(val, str) and len(val) > 20:
                    raw[key] = f"{val[:8]}…{val[-4:]}(len={len(val)})"
        print(json.dumps(raw, ensure_ascii=False, indent=2))
    except Exception:
        print(res.raw or "(空)")
    print()

    print("── 解析结果 ──")
    print(f"  成功    : {res.ok}")
    print(f"  分类    : {res.kind}")
    print(f"  说明    : {res.message}")
    if res.token:
        print(f"  Token   : {_mask_token(res.token)}")
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
