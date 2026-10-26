# -*- coding: utf-8 -*-
"""Token 寿命实测实验（一次性运行）。

要回答的问题很具体：**"手机连一次校园网之后，PC 上存的 Token 还能不能用？"**

做法：
1. 用当前 Token 认证一次，记下结果（基线）；
2. 提示你在手机上做一次"设备登录"（例如用 APP 扫码给某台设备上线）；
3. 再用**同一个** Token 认证一次，对比结果；
4. 输出结论：Token 会不会因为设备登录而轮换 → 决定你日常要不要掏手机。

用法：
    python tokenlab.py
"""
from __future__ import annotations

import sys
from typing import Optional

from auth import CampusAuth, TOKEN_INVALID
from config import Config


def _line(char: str = "─", width: int = 58) -> str:
    return char * width


def _title(text: str) -> None:
    print()
    print(_line("═"))
    print(f"  {text}")
    print(_line("═"))


def main() -> int:
    cfg = Config()
    auth = CampusAuth(cfg)

    token = cfg.get_token()
    if not token:
        print("✗ 尚未保存 Token。请先在主程序里粘贴 Token 并点「校验并保存」，再运行本脚本。")
        return 1

    ip = auth.detect_local_ip()
    mac = auth.detect_mac()
    print(f"• 认证网关 : {cfg.base_url}")
    print(f"• 本机 IP  : {ip or '未探测到'}")
    print(f"• 本机 MAC : {mac or '未探测到'}")

    _title("第 1 步：用当前 Token 认证（基线）")
    base = auth.auth_by_ip(ip, token)
    print(f"结果：{'成功' if base.ok else '失败'}  {base.message}")
    if base.detail:
        print(f"响应：{base.detail[:300]}")
    if base.new_token and base.new_token != token:
        print("★ 响应里带了新 Token（说明服务端会下发轮换后的 Token，L1 自动续存有效）")
    else:
        print("· 响应里没有新 Token")

    if not base.ok:
        _title("结论")
        print("当前 Token 已经无法通过认证 → 需要重新抓取 Token。")
        print("建议先跑一次扫码助手（主程序里的「扫码登录」按钮）恢复联网。")
        return 2

    _title("第 2 步：现在去手机上做一次「设备登录」")
    print("例如：在糕糕 APP 里扫码给某台设备上线（哪怕是你自己另一台设备）。")
    print("做完之后回到这个窗口按回车继续。")
    print()
    try:
        input("按回车继续（Ctrl+C 取消）...")
    except (KeyboardInterrupt, EOFError):
        print("\n已取消。")
        return 0

    _title("第 3 步：用同一个 Token 再认证一次")
    after = auth.auth_by_ip(ip, token)
    print(f"结果：{'成功' if after.ok else '失败'}  {after.message}")
    if after.detail:
        print(f"响应：{after.detail[:300]}")

    _title("结论")
    if after.ok:
        print("✅ 设备登录之后，旧 Token 依然可用。")
        print("   → 你的账号 Token 稳定，可以放心使用「完全免手机」模式：")
        print("     开机自启 + 断网自动重认证，全程不需要碰手机。")
    elif after.status == TOKEN_INVALID:
        print("⚠ 设备登录之后，旧 Token 失效了 —— Token 会轮换。")
        print("   → 建议工作流：")
        print("     1) 平时用 Token 免手机；")
        print("     2) 脚本发现 Token 失效时自动弹出二维码，手机扫一下即可恢复；")
        print("     3) 只有在你主动点「重新抓取」时才需要走 SSL 抓包流程。")
    else:
        print("⚠ 第二次认证失败，但原因不是 Token 失效：", after.message)
        print("   → 可能是单设备名额被占（手机重新认证把 PC 顶掉了）。")
        print("     在主程序设置里填上「手机 IP」，脚本就会自动顶替回来。")
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # pragma: no cover
        print(f"实验脚本出错: {exc}")
        sys.exit(3)
