# -*- coding: utf-8 -*-
"""开机自启。

**不用注册表 Run 项**（``HKCU\\...\\CurrentVersion\\Run`` 属于 Defender
T1547.001「注册表自启动/持久化」的高危特征，容易被杀软直接拦下）。
改为在「启动」文件夹放一个 ``.cmd`` 快捷脚本，行为等价但特征风险低得多。

抽成独立模块，让 Web 版和旧版 Tk 界面共用同一份逻辑。
"""
from __future__ import annotations

import os
import re
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


def _clear_attributes(path: Path) -> None:
    """去掉只读 / 隐藏属性。

    只读文件会让 `write_bytes` 直接抛 PermissionError —— 而自启脚本是
    「写一次就不再管」的东西，很容易在别处被标上只读。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        FILE_ATTRIBUTE_NORMAL = 0x80
        SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
        SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        SetFileAttributesW.restype = ctypes.c_uint32
        SetFileAttributesW(str(path), FILE_ATTRIBUTE_NORMAL)
    except Exception:
        pass


def _cmd_bytes() -> bytes:
    """自启脚本内容。.cmd 由 cmd.exe 读取，中文必须用 GBK（系统 ANSI 代码页）。"""
    return ("@echo off\r\n"
            "rem 糕糕校园网自动认证 —— 开机自启\r\n"
            f"start \"\" {_entry_command()}\r\n").encode("gbk", errors="replace")


def _write_direct(target: Path, data: bytes) -> None:
    _clear_attributes(target)
    target.write_bytes(data)


def _write_cmd(target: Path, data: bytes) -> None:
    """把自启脚本写下去，带一条兜底路径。

    直接写失败（文件被占用 / 只读 / 被安全软件拦）时，改成
    「同目录临时文件 + `os.replace` 原子替换」—— 替换只要求对**目录**
    有删除权限，不少拦「打开文件写入」的方案放的是后者，这条路常常能过。
    """
    try:
        _write_direct(target, data)
        return
    except PermissionError:
        pass
    tmp = target.with_name(target.name + ".tmp")
    _clear_attributes(tmp)
    tmp.write_bytes(data)
    os.replace(tmp, target)


def _entry_target(text: str) -> str:
    """从 .cmd 内容里取出被启动的那个程序路径（取不到返回空串）。"""
    m = re.search(r'start\s+""\s+"([^"]+)"', text or "")
    return m.group(1) if m else ""


def autostart_stale() -> bool:
    """自启文件在，但已经没用了（程序被移走 / 删掉）。

    只看「文件存在」会把坏条目当成正常 —— 开机静默启动一个不存在的路径，
    用户完全看不出问题。

    ⚠️ 只把「指向的程序不在了」判为过期。仅仅是路径不同（源码运行 vs
    打包版）不算 —— 否则每换一种运行方式就重写一次，启动项会来回翻烧饼。
    """
    p = startup_cmd_path()
    if not (p and p.exists()):
        return False
    try:
        text = p.read_text(encoding="gbk", errors="replace")
    except Exception:
        return True
    if _entry_command() in text:
        return False
    target = _entry_target(text)
    if not target:
        return True          # 解析不出启动项，宁可重写
    return not Path(target).exists()


def apply_autostart(enable: bool) -> tuple:
    """开关开机自启。返回 ``(ok, message)``。"""
    if sys.platform != "win32":
        return False, "开机自启仅支持 Windows"
    target = startup_cmd_path()
    if target is None:
        return False, "找不到「启动」文件夹，开机自启不可用"
    try:
        if enable:
            _write_cmd(target, _cmd_bytes())
            hide_file(target)
            return True, "已开启开机自启（启动文件夹）"
        if target.exists():
            _clear_attributes(target)
            target.unlink()
        return True, "已关闭开机自启"
    except PermissionError as exc:
        # PermissionError 在「启动」文件夹上最常见的原因不是 Windows 权限，
        # 而是安全软件的「启动项保护」或文件正被占用。给出可操作的建议，
        # 别让用户对着一句 Errno 13 发呆。
        return False, (
            f"设置开机自启失败：没有权限写入「启动」文件夹（{exc}）\n"
            "常见原因：360 / 火绒等安全软件的「启动项保护」拦下了写入，"
            "或该文件正被其他程序占用。\n"
            "手动做法：按 Win+R 输入 shell:startup 打开启动文件夹，"
            "把程序 exe 的快捷方式放进去即可。"
        )
    except OSError as exc:
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
