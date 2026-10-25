# -*- coding: utf-8 -*-
"""糕糕校园网自动认证 —— 程序入口。

默认启动 **Web 界面**：起一个只监听 127.0.0.1 的本地服务，
用浏览器显示（``webui.py`` + ``core.py``）。

加 ``--tk`` 参数走旧版 Tk 界面（``tkui.py``），作为无浏览器环境的兜底。

业务逻辑在 ``core.py``，两种界面共用同一套认证行为。
"""
from __future__ import annotations

import sys

import logs
from config import logs_dir
from taskbar import (
    activate_existing_instance,
    acquire_single_instance,
    release_single_instance,
    set_app_user_model_id,
)


def _run_web_ui(silent: bool) -> int:
    """Web 界面模式（默认）。

    起一个只监听 127.0.0.1 的本地服务，用浏览器显示界面。
    """
    from webui import WebApp, pick_port

    app = WebApp(port=pick_port())
    app.start()
    url = app.url()
    # 打包版没有控制台，把地址写进配置目录（仅当前用户可读），
    # 用户万一关掉了页面可以自己找回来
    url_file = app.write_url_file()

    # 打开界面 + 起托盘。
    #
    # ⚠️ 这里**不能**用 activate_existing_instance() 判断「是不是首个实例」——
    # 单实例锁在 main() 里已经拿过了，那个函数是用来「把已有实例的
    # 窗口顶到前台」的，在 Web 模式下没有窗口可顶，只会白白失败一次。
    app.open_browser()
    # 打包版是 --windowed（没有控制台），托盘是用户唯一的入口：
    # 右键 → 打开界面 / 立即认证 / 退出
    app.start_tray()

    if not silent:
        print("=" * 62)
        print(" 糕糕校园网自动认证 · Web 界面")
        print("=" * 62)
        print(f"  地址：{url}")
        print("  （地址带一次性令牌，请勿分享）")
        if url_file is not None:
            print(f"  地址也写在：{url_file}")
        print("  关闭本窗口即退出程序")
        print("=" * 62)
    try:
        app.serve_forever()
    finally:
        app.stop()
    return 0


def _run_tk_ui(silent: bool) -> int:
    """旧版 Tk 界面（``--tk``）。"""
    from tkui import run_tk_ui

    return run_tk_ui(silent)


def main() -> int:
    silent = "--silent" in sys.argv
    use_tk = "--tk" in sys.argv

    # 文件日志（脱敏 + 轮转）—— 初始化失败不影响程序运行
    try:
        logs.init(logs_dir())
    except Exception:
        pass

    # 任务栏占用控制：
    # 1) 独立 AppUserModelID —— 任务栏图标独立分组，不和宿主进程混一起
    set_app_user_model_id()
    # 2) 单实例锁 —— 重复启动时不再多出一个托盘图标，
    #    改为把已有实例的窗口顶到前台后退出
    if not acquire_single_instance():
        # 只有「用户手动双击」才唤起窗口；
        # 开机自启（--silent）撞上已在跑的实例时保持安静，
        # 否则会在用户没操作的情况下把窗口弹到前面。
        if not silent:
            activate_existing_instance()
        return 0

    try:
        if use_tk:
            return _run_tk_ui(silent)
        return _run_web_ui(silent)
    finally:
        release_single_instance()


if __name__ == "__main__":
    sys.exit(main())
