# -*- coding: utf-8 -*-
"""开机自启。

**主入口：注册表 Run 项**；**兜底：「启动」文件夹里的 .cmd**。

为什么改回 Run 项
-----------------
早期刻意只用「启动」文件夹，是因为 ``HKCU\\...\\CurrentVersion\\Run`` 属于
Defender T1547.001「注册表自启动/持久化」的特征。但实测下来，这两种方式
在 Windows 上的**执行时机完全不同**：

* **Run 项**：用户登录时由 Explorer 立即执行。实测同一台机器上 OneDrive、
  Steam、IDMan 全在登录后 10–30 秒内就起来了。
* **启动文件夹**：在这台机器上被推迟到了**用户解锁屏幕之后**。
  开机 09:11:09 → 桌面 09:11:22 → 其他自启程序 09:11:35 起 →
  我们的 .cmd **09:31:18**（解锁后 28 秒）才被执行，程序晚了整整 20 分钟。

也就是说，「开机自启」在启动文件夹这条路上实际是「**解锁自启**」。
对校园网认证这种「开机就该有网」的场景，这个差别是致命的。

因此改成 Run 项为主（登录即启动），启动文件夹的 .cmd 保留为**兜底**：
万一 Run 项被安全软件清掉，至少还有一条路能起来。单实例锁保证不会双开。
写入 Run 项失败时会**优雅降级**为只用启动文件夹，并如实告知用户会慢一些。

抽成独立模块，让 Web 版和旧版 Tk 界面共用同一份逻辑。
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

CMD_NAME = "GaoGaoNetAUTO.cmd"

# 注册表 Run 项（主入口）。HKCU 下不需要管理员权限。
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "GaoGaoNetAUTO"

# 脚本里的格式标记。看到它说明这份 .cmd 是「带执行痕迹」的新版本；
# 没有就是旧版本，需要重写一次才能诊断开机自启到底跑到哪一步。
_ENTRY_MARK = "GaoGaoNetAUTO startup launcher"

# 写入失败的重试次数与间隔。
#
# 启动文件夹是杀软重点盯防的位置，「往这里写 .cmd」很容易被实时防护、
# 启动项保护之类的模块**短暂**拦一下 —— 拦完就放，隔一两百毫秒再试
# 往往就过了。这属于典型的一次性抖动，不能当成永久失败。
_WRITE_TRIES = 3
_RETRY_DELAY = 0.12


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


def _run_get() -> Optional[str]:
    """读注册表 Run 项的当前值（没有返回 None）。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
        return str(value) if value else None
    except OSError:
        return None


def _run_set(value: str) -> None:
    """写注册表 Run 项。被安全软件拦时抛 OSError，由调用方降级。"""
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, value)


def _run_delete() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        pass


def _first_quoted(text: str) -> str:
    """取出命令行里第一个被引号包住的路径。"""
    m = re.search(r'"([^"]+)"', text or "")
    return m.group(1) if m else ""


def autostart_enabled() -> bool:
    """开机自启是否已开启（Run 项或启动文件夹任一存在即算）。"""
    if _run_get():
        return True
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


def trace_path() -> Optional[Path]:
    """自启脚本的执行痕迹文件。

    「开机没网」最难查的地方是**分不清两种完全不同的失败**：

    * 启动项根本没被执行（Windows 的启动队列跳过了它）；
    * 启动项执行了，但程序没能跑起来（路径没了、被拦了、秒退）。

    这两种的修法完全不同。所以在 .cmd 里留一行时间戳，程序起来后读它，
    一比对就知道中间差了多少、卡在哪一环。
    """
    try:
        from config import data_dir

        return Path(data_dir()) / "autostart.log"
    except Exception:
        return None


def trace_tail(limit: int = 3) -> list:
    """读自启痕迹的最后几行（没有就返回空列表）。"""
    p = trace_path()
    try:
        if not (p and p.is_file()):
            return []
        text = p.read_text(encoding="gbk", errors="replace")
    except OSError:
        return []
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-limit:] if lines else []


def _cmd_bytes() -> bytes:
    """自启脚本内容。

    ``.cmd`` 由 cmd.exe 读取，中文必须用 GBK（系统 ANSI 代码页）——
    但这里干脆全用 ASCII：脚本一旦编码错乱就是「静默不执行」，
    而开机自启恰恰是最没人在旁边看着的场景。

    ``start "" "<exe>"`` 的写法不能改：``autostart_stale()`` 用正则从
    内容里抠出被启动的路径，格式变了就解析不出来。
    """
    trace = trace_path()
    lines = [
        "@echo off",
        "rem GaoGaoNetAUTO startup launcher",
        "setlocal",
    ]
    if trace is not None:
        lines.append(f'set "TRACE={trace}"')
        lines.append('>>"%TRACE%" 2>nul echo [%date% %time%] launcher fired')
    lines.append(f'start "" {_entry_command()}')
    if trace is not None:
        lines.append('>>"%TRACE%" 2>nul echo [%date% %time%] launcher done')
    lines.append("endlocal")
    return ("\r\n".join(lines) + "\r\n").encode("gbk", errors="replace")


def _exists(target: Optional[Path]) -> bool:
    try:
        return bool(target and target.exists())
    except OSError:
        return False


def _same_bytes(target: Path, data: bytes) -> bool:
    """目标文件内容是否已经是我们要写的这份（读不到一律当「不是」）。"""
    try:
        return target.is_file() and target.read_bytes() == data
    except OSError:
        return False


def _write_direct(target: Path, data: bytes) -> None:
    _clear_attributes(target)
    target.write_bytes(data)


def _write_via_replace(target: Path, data: bytes) -> None:
    """临时文件 + 原子替换。

    替换只要求对**目录**有删除权限，不少拦「打开已有文件写入」的方案
    放的是后者，这条路常常能过。
    """
    tmp = target.with_name(target.name + ".tmp")
    try:
        _clear_attributes(tmp)
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        try:
            if tmp.exists():
                _clear_attributes(tmp)
                tmp.unlink()
        except OSError:
            pass


def _write_via_rename(target: Path, data: bytes) -> None:
    """把旧文件挪开再新建。

    最后一条路：有些方案不放「覆盖已有文件」，但放「改名」和「建新文件」。
    写不进去时把旧的挪回来，不留下「两个都没有」的窗口。
    """
    bak = target.with_name(target.name + ".old")
    try:
        if bak.exists():
            _clear_attributes(bak)
            bak.unlink()
    except OSError:
        pass
    moved = False
    if target.exists():
        _clear_attributes(target)
        target.rename(bak)
        moved = True
    try:
        _write_direct(target, data)
    except OSError:
        if moved:
            try:
                os.replace(bak, target)
            except OSError:
                pass
        raise
    if moved:
        try:
            _clear_attributes(bak)
            bak.unlink()
        except OSError:
            pass


def _write_cmd(target: Path, data: bytes) -> None:
    """把自启脚本写下去，失败时换着法子重试。

    三层意思：

    1. **已经一样就不写** —— 内容没变还去覆盖一次，白挨一次杀软扫描，
       还可能被拦下来报个假失败（自启其实早就生效了）。
    2. **换策略** —— 直写 / 临时文件替换 / 挪开再建，三种写法的权限要求
       不一样，一条被拦不代表全被拦。
    3. **重试** —— 拦截大多是瞬时的，隔一两百毫秒再来一次通常就过了。
    """
    if _same_bytes(target, data):
        return

    strategies = (_write_direct, _write_via_replace, _write_via_rename)
    last: Optional[BaseException] = None
    for attempt in range(_WRITE_TRIES):
        for strategy in strategies:
            try:
                strategy(target, data)
            except OSError as exc:
                last = exc
                continue
            if _same_bytes(target, data):
                return
        if attempt + 1 < _WRITE_TRIES:
            time.sleep(_RETRY_DELAY * (attempt + 1))

    if last is not None:
        raise last
    raise PermissionError(13, "Permission denied", str(target))


def _entry_target(text: str) -> str:
    """从 .cmd 内容里取出被启动的那个程序路径（取不到返回空串）。"""
    m = re.search(r'start\s+""\s+"([^"]+)"', text or "")
    return m.group(1) if m else ""


def autostart_stale() -> bool:
    """自启项在，但已经没用了（程序被移走 / 删掉 / 还是旧格式）。

    只看「文件存在」会把坏条目当成正常 —— 开机静默启动一个不存在的路径，
    用户完全看不出问题。

    ⚠️ 只把「指向的程序不在了」判为过期。仅仅是路径不同（源码运行 vs
    打包版）不算 —— 否则每换一种运行方式就重写一次，启动项会来回翻烧饼。
    """
    run_value = _run_get()
    if run_value:
        # Run 项是主入口，指向的程序没了就必须重写。
        target = _first_quoted(run_value)
        if target and not Path(target).exists():
            return True

    p = startup_cmd_path()
    if not (p and p.exists()):
        return False
    try:
        text = p.read_text(encoding="gbk", errors="replace")
    except Exception:
        return True
    if _entry_command() in text:
        # 路径对，但可能还是**旧格式**（没有执行痕迹那一版）。
        # 旧格式没法回答「启动项到底有没有被执行」，顺手升级掉。
        return _ENTRY_MARK not in text
    target = _entry_target(text)
    if not target:
        return True          # 解析不出启动项，宁可重写
    return not Path(target).exists()


def apply_autostart(enable: bool) -> tuple:
    """开关开机自启。返回 ``(ok, message)``。

    开启时**两条路都写**：Run 项是主入口（登录即启动），启动文件夹的
    .cmd 是兜底（万一 Run 项被安全软件清掉，至少还能起来）。
    单实例锁保证不会双开，晚到的那一路会安静退出。
    """
    if sys.platform != "win32":
        return False, "开机自启仅支持 Windows"
    target = startup_cmd_path()
    if not enable:
        try:
            _run_delete()
            if target is not None and target.exists():
                _clear_attributes(target)
                target.unlink()
            return True, "已关闭开机自启"
        except OSError as exc:
            return False, f"关闭开机自启失败: {exc}"

    # ---- 主入口：注册表 Run 项
    run_error: Optional[BaseException] = None
    try:
        _run_set(_entry_command())
    except OSError as exc:
        run_error = exc

    # ---- 兜底：启动文件夹 .cmd
    cmd_error: Optional[BaseException] = None
    if target is None:
        cmd_error = OSError("找不到「启动」文件夹")
    else:
        data = _cmd_bytes()
        try:
            _write_cmd(target, data)
        except OSError as exc:
            # 写失败但文件其实已经是想要的样子（比如刚被另一个实例写好，
            # 或者写的瞬间被拦了一下）—— 这就是成功，别报假失败。
            # 假失败的代价很实在：core 会把配置里的开关拨回「关」，
            # 用户以为自启没开，下次启动又白忙一遍。
            if not _same_bytes(target, data):
                cmd_error = exc
        if cmd_error is None:
            # 不设「隐藏」属性：隐藏的启动项在任务管理器「启动」选项卡里
            # 根本不显示，用户既没法确认也没法禁用它。
            _clear_attributes(target)

    if run_error is None:
        return True, "已开启开机自启（登录即启动）"
    if cmd_error is None:
        return True, (
            "已开启开机自启（启动文件夹）。\n"
            f"注册表 Run 项写入被拦（{run_error}），本次只能用启动文件夹兜底。\n"
            "注意：启动文件夹在这台机器上可能要到**解锁屏幕之后**才会执行，"
            "开机自启会慢一些。"
        )
    return False, (
        f"设置开机自启失败：\n"
        f"· 注册表 Run 项：{run_error}\n"
        f"· 启动文件夹：{cmd_error}\n"
        "常见原因是安全软件的「启动项保护」拦下了写入，可在安全软件里把本程序\n"
        "加入信任后重试。\n"
        "手动做法：按 Win+R 输入 shell:startup 打开启动文件夹，"
        "把程序 exe 的快捷方式放进去即可。"
    )
