# -*- coding: utf-8 -*-
"""糕糕校园网自动认证 —— 程序入口。

默认启动 **Web 界面**：起一个只监听 127.0.0.1 的本地服务，
用浏览器显示（``webui.py`` + ``core.py``）。

加 ``--tk`` 参数走旧版 Tk 界面（``legacy/tkui.py``），作为无浏览器环境的兜底。

业务逻辑在 ``core.py``，两种界面共用同一套认证行为。
"""
from __future__ import annotations

import sys
import threading

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
    # 开机自启（--silent）不弹浏览器 —— 那时候用户多半不在电脑前，
    # 弹出来只是变成一个开机就冒出来的窗口。要靠托盘「打开界面」进去。
    if not silent:
        app.open_browser()
    # 打包版是 --windowed（没有控制台），托盘是用户唯一的入口：
    # 右键 → 打开界面 / 立即认证 / 退出
    app.start_tray()

    # 启动检查：自启条目与配置对齐 / Token 体检 / 该认证时延迟自动认证。
    #
    # 以前这条线是**断的**：``Controller.startup_checks()`` 定义了却没人
    # 调用。表现是「自启开关是开的，但开机后不会自动认证」「配置里的自启
    # 状态和启动文件夹里的文件长期对不上」。它会发网络请求，必须放后台，
    # 不能挡着界面起来。
    threading.Thread(
        target=app.controller.startup_checks, daemon=True
    ).start()

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
    """旧版 Tk 界面（``--tk``）。

    只在**源码运行**时可用：打包版只带 Web 界面。Tk 界面要拖进整套
    tcl/tk，体积大，而且只是「没有浏览器时的兜底」，不值得让每个用户的
    包都胖一圈。它是惰性 import，PyInstaller 的静态分析也抓不到，
    所以这里显式给出人话提示，而不是甩一个莫名的 ModuleNotFoundError。
    """
    try:
        from legacy.tkui import run_tk_ui
    except ImportError:
        print("当前是打包版，只带 Web 界面（旧版 Tk 界面没有打进来）。")
        print("想用 Tk 界面请从源码运行：python main.py --tk")
        return 2
    return run_tk_ui(silent)


def _open_running_instance() -> bool:
    """把已经在跑的实例的界面打开。

    Web 版**没有窗口**可以顶到前台（``activate_existing_instance`` 找的是
    Tk 窗口，永远是 False），所以第二次双击 exe 时以前是「什么都没发生」——
    用户以为程序坏了，又双击几次。

    地址写在数据目录的 ``webui.url`` 里（带一次性令牌），直接打开它。
    """
    try:
        from config import data_dir

        url = (data_dir() / "webui.url").read_text(encoding="utf-8").strip()
    except Exception:
        return False
    if not url.startswith("http://127.0.0.1:"):
        return False
    try:
        import webbrowser

        return webbrowser.open(url)
    except Exception:
        return False


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
            # Tk 版有真窗口；Web 版没有，退回「打开已有实例的页面」
            if not activate_existing_instance():
                _open_running_instance()
        return 0

    try:
        if use_tk:
            return _run_tk_ui(silent)
        return _run_web_ui(silent)
    finally:
        release_single_instance()


if __name__ == "__main__":
    sys.exit(main())
