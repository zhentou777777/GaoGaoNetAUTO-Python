# -*- coding: utf-8 -*-
"""Windows 任务栏占用控制。

三个能力：

1. 单实例锁（命名 Mutex）
   防止重复启动导致系统托盘里出现多个「糕糕校园网自动认证」图标。
   第二次启动时尝试把已有实例的窗口顶到前台，然后直接退出。

2. 工具窗口样式（WS_EX_TOOLWINDOW）
   Tkinter 的 Toplevel 默认会在任务栏上另起一个按钮，
   扫码窗口 / 在线设备窗口 / Token 帮助窗口同时开着就是 3 个额外位置。
   加上 WS_EX_TOOLWINDOW 后它们只作为主窗口的附属窗，不再占任务栏。

3. 独立 AppUserModelID
   让 Windows 正确归组任务栏图标，避免和 python.exe 之类的宿主混在一起。

全部走 ctypes，不引入第三方依赖；非 Windows 平台自动降级为空操作。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

# ---------------------------------------------------------------- 常量

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
SW_RESTORE = 9
SW_SHOW = 5

_MUTEX_NAME = "Local\\GaoGaoNetAUTO_SingleInstance_v1"
_WINDOW_TITLE = "糕糕校园网自动认证"

_kernel32 = None
_user32 = None
if sys.platform == "win32":
    try:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:  # pragma: no cover
        _kernel32 = None
        _user32 = None


# ---------------------------------------------------------------- 单实例

_mutex_handle: Optional[int] = None


def acquire_single_instance() -> bool:
    """尝试取得单实例锁。

    返回 True = 本进程是唯一实例，可以继续启动；
    返回 False = 已有实例在跑，调用方应当提示并退出。
    """
    global _mutex_handle

    if _kernel32 is None:
        return True

    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.CreateMutexW.argtypes = [
        ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR,
    ]
    handle = _kernel32.CreateMutexW(None, True, _MUTEX_NAME)
    err = ctypes.get_last_error()
    if not handle:
        # 创建失败（资源不足 / 权限异常）不能当成「已有实例在跑」——
        # 那样程序会静默退出，用户双击多少次都没反应。
        # 宁可多开一个实例，也别把人挡在门外。
        return True
    # 183 = ERROR_ALREADY_EXISTS
    if err != 183:
        _mutex_handle = handle
        return True
    _kernel32.CloseHandle(handle)
    return False


def activate_existing_instance() -> bool:
    """把已经在跑的实例的窗口顶到前台。

    找不到窗口（对方可能还在启动、或已隐藏到托盘）时返回 False。
    """
    if _user32 is None:
        return False
    try:
        _user32.FindWindowW.restype = wintypes.HWND
        _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        hwnd = _user32.FindWindowW(None, _WINDOW_TITLE)
        if not hwnd:
            return False
        _user32.ShowWindow(hwnd, SW_SHOW)
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def release_single_instance() -> None:
    """释放单实例锁（正常退出时调用，非必需但更干净）。"""
    global _mutex_handle
    if _mutex_handle and _kernel32 is not None:
        try:
            _kernel32.CloseHandle(_mutex_handle)
        except Exception:
            pass
        _mutex_handle = None


# ---------------------------------------------------------------- 任务栏

def set_tool_window(win) -> None:
    """把 Tk 窗口标记为「工具窗口」：不在任务栏另起按钮、不进 Alt+Tab。"""
    if _user32 is None:
        return
    try:
        # Tk 的 toplevel：winfo_id() 可能是外层 wrapper，
        # 也可能需要再取一层 parent。两个都处理一遍最稳。
        raw = win.winfo_id()
        candidates = [raw]
        try:
            parent = ctypes.windll.user32.GetParent(raw)
            if parent:
                candidates.append(parent)
        except Exception:
            pass

        _user32.GetWindowLongW.restype = ctypes.c_long
        _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        _user32.SetWindowLongW.restype = ctypes.c_long
        _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int,
                                          ctypes.c_long]

        for hwnd in candidates:
            try:
                style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                if not style:
                    continue
                new_style = (style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
                _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, new_style)
            except Exception:
                continue
    except Exception:
        pass


def set_app_user_model_id(app_id: str = "Gaogao.CampusAuth.Desktop") -> None:
    """给进程设置独立的 AppUserModelID，任务栏图标独立分组。"""
    if _kernel32 is None:
        return
    try:
        _kernel32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
        _kernel32.SetCurrentProcessExplicitAppUserModelID.argtypes = [
            wintypes.LPCWSTR,
        ]
        _kernel32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except Exception:
        pass
