# -*- coding: utf-8 -*-
"""开机自启。

**不用注册表 Run 项**（``HKCU\\...\\CurrentVersion\\Run`` 属于 Defender
T1547.001「注册表自启动/持久化」的高危特征，容易被杀软直接拦下）。
改为在「启动」文件夹放一个 ``.cmd`` 快捷脚本，行为等价但特征风险低得多。

抽成独立模块，让 Web 版和旧版 Tk 界面共用同一份逻辑。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

CMD_NAME = "GaoGaoNetAUTO.cmd"


def startup_dir() -> Optional[Path]:
    """当前用户的「启动」文件夹路径。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, "Startup")
        raw = os.path.expandvars(str(raw))
        d = Path(raw)
        if d.is_dir():
            return d
    except Exception:
        pass
    try:
        appdata = Path(os.environ.get("APPDATA", ""))
        d = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        if d.is_dir():
            return d
    except Exception:
        pass
    return None


def startup_cmd_path() -> Optional[Path]:
    d = startup_dir()
    return (d / CMD_NAME) if d else None


def autostart_enabled() -> bool:
    """开机自启是否已开启（以启动文件夹里的实际文件为准）。"""
    p = startup_cmd_path()
    try:
        return bool(p and p.exists())
    except Exception:
        return False


def _entry_command() -> str:
    """自启要执行的命令行。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --silent'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{Path(__file__).resolve().parent / "main.py"}" --silent'


def hide_file(path: Path):
    """给文件加「隐藏」属性（避免启动时闪一个黑框）。"""
    try:
        import ctypes

        FILE_ATTRIBUTE_HIDDEN = 0x02
        SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
        SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        SetFileAttributesW.restype = ctypes.c_uint32
        SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def apply_autostart(enable: bool) -> tuple:
    """开关开机自启。返回 ``(ok, message)``。"""
    if sys.platform != "win32":
        return False, "开机自启仅支持 Windows"
    target = startup_cmd_path()
    if target is None:
        return False, "找不到「启动」文件夹，开机自启不可用"
    try:
        if enable:
            # .cmd 由 cmd.exe 读取，中文必须用 GBK（系统 ANSI 代码页），
            # UTF-8 会变成乱码
            target.write_bytes(
                ("@echo off\r\n"
                 "rem 糕糕校园网自动认证 —— 开机自启\r\n"
                 f"start \"\" {_entry_command()}\r\n").encode("gbk", errors="replace")
            )
            hide_file(target)
            return True, "已开启开机自启（启动文件夹）"
        else:
            if target.exists():
                target.unlink()
            return True, "已关闭开机自启"
    except Exception as exc:
        return False, f"设置开机自启失败: {exc}"


def migrate_autostart_legacy() -> bool:
    """把旧版写在注册表 Run 里的自启项迁到启动文件夹。

    旧版用的是 ``HKCU\\...\\CurrentVersion\\Run``。升级后自动清掉注册表项
    并在启动文件夹落一个 ``.cmd``，行为不变但不再触发杀软对
    「注册表自启动」的规则。
    """
    if sys.platform != "win32":
        return False
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
            try:
                val, _ = winreg.QueryValueEx(key, "GaoGaoNetAUTO")
            except FileNotFoundError:
                return False
            if not val:
                return False
            winreg.DeleteValue(key, "GaoGaoNetAUTO")
        if not autostart_enabled():
            apply_autostart(True)
        return True
    except Exception:
        return False
