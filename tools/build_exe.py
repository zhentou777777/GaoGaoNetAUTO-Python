# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本。

默认产出 **onedir**（目录形态）而非 onefile，理由：

* onefile 启动时把整个包解压到 ``%TEMP%\\._MEIxxxx``，
  这既是杀软对「自解压到临时目录」行为规则的高危命中点，
  也让 Defender 的行为分析看到「一个进程写了大量文件」。
* onedir 不会在临时目录落盘，行为特征明显更干净。
* onedir 启动快（不用每次解压几百 MB）。

需要单文件版时加 ``--onefile``。

同时做的「降杀软误报」处理：
* ``--noupx``：UPX 壳是杀软最爱的信号之一（自解压 + 入口点跳板）。
* ``--version-file``：写入 Windows 版本资源，属性面板有发布者与版本，
  消掉「未知发布者 / 无描述」这类启发式加分项。
* ``--exclude-module``：剔掉 playwright / capture / tokenlab 等
  只在开发调试时用的模块，缩小体积也减少可疑行为面。

用法::

    python build_exe.py                 # onedir（默认）
    python build_exe.py --onefile       # 单文件
    python build_exe.py --sign          # 打包后调用自签名脚本
    python build_exe.py --skip-audit    # 跳过产物审计
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 控制台编码
# Windows 控制台 / CI runner 的默认编码可能是 cp1252（GitHub Actions 就是），
# 直接 print 中文会抛 UnicodeEncodeError 让构建在第一步就崩掉。
# 统一把 stdout/stderr 切到 UTF-8，替换不出来的字符而不是炸掉。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 脚本在 tools/ 下，项目根要向上一层
HERE = Path(__file__).resolve().parent.parent

# 从 tools/ 里跑时，``import icons`` 这类根目录模块会找不到 ——
# 把项目根加进 sys.path（以前脚本就在根目录，不需要这行）。
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

NAME = "GaoGaoNetAUTO"
# 版本号的唯一来源是 version_info.txt（--version-file 直接用它），
# 这里不再另存一份常量 —— 之前两份不同步，exe 属性里写的是旧版本。

# 开发调试专用、不该进生产包的模块
EXCLUDED_MODULES = [
    "playwright",
    "playwright.sync_api",
    "capture",
    "tokenlab",
    "login_check",
    "PyQt5",
    "PyQt6",
    "pytest",
    "IPython",
    "notebook",
]


def _check_runtime() -> bool:
    """打包前的环境自检 —— 尤其看 tkinter。

    **tkinter 缺失是最容易踩的坑**：有些 Python 发行版（精简版、
    conda 的 base 环境、CI 容器）不带 tkinter，而本项目是纯 GUI 程序。
    缺 tkinter 时 PyInstaller **不会报错**，能打出 exe，但一运行就
    `ModuleNotFoundError: No module named 'tkinter'` —— 只有真正跑起来
    才会发现，白白浪费一轮打包。

    所以这里在打包前就拦下来。
    """
    ok = True
    print("=== 环境自检 ===")
    print(f"  Python: {sys.version.split()[0]}  ({sys.executable})")

    try:
        import tkinter  # noqa: F401

        import tkinter as _tk

        print(f"  [OK] tkinter {_tk.TkVersion}")
    except Exception as exc:
        print(f"  [!!] 缺少 tkinter —— 本项目是 GUI 程序，无法打包！")
        print(f"       {exc}")
        print("       解决办法：换用带 tkinter 的 Python，或安装官方版 Python")
        print("       （https://www.python.org/downloads/ 安装时勾选 tcl/tk）")
        ok = False

    try:
        import PyInstaller  # noqa: F401

        print(f"  [OK] PyInstaller {PyInstaller.__version__}")
    except Exception:
        print("  [!!] 未安装 PyInstaller：pip install pyinstaller")
        ok = False

    for mod, why in (("requests", "HTTP 请求"), ("segno", "二维码生成"),
                     ("pystray", "托盘图标"), ("PIL", "图标渲染")):
        try:
            __import__(mod)
            print(f"  [OK] {mod}（{why}）")
        except Exception:
            print(f"  [--] {mod} 缺失（{why}）—— 该功能会降级")
    print()
    return ok


def _assets_dir() -> Path:
    """准备图标资源（打包用的 ico 需要在仓库内，供 --icon 使用）。"""
    assets = HERE / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    try:
        import icons

        icons.ensure_icons(force=True, out_dir=assets)
    except Exception as exc:
        print(f"[warn] 图标生成失败：{exc}")
    return assets


def build(onefile: bool = False, clean: bool = True) -> int:
    if not _check_runtime():
        print("环境自检未通过，已中止打包。")
        return 2

    assets = _assets_dir()
    icon = assets / "icon.ico"
    version_file = HERE / "version_info.txt"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--name", NAME,
        "--noupx",                       # 不加 UPX 壳
        "--hidden-import", "pystray._win32",
        "--hidden-import", "encodings.idna",
    ]
    if clean:
        cmd.append("--clean")
    cmd.append("--onefile" if onefile else "--onedir")

    for mod in EXCLUDED_MODULES:
        cmd += ["--exclude-module", mod]

    if icon.exists():
        cmd += ["--icon", str(icon)]
    if version_file.exists():
        cmd += ["--version-file", str(version_file)]
    if assets.exists():
        cmd += ["--add-data", f"{assets}{os.pathsep}assets"]
    # Web 界面的静态资源（HTML / CSS / JS）—— 打包后要从 _MEIPASS 里读
    webui_dir = HERE / "webui"
    if webui_dir.exists():
        cmd += ["--add-data", f"{webui_dir}{os.pathsep}webui"]

    # 打包 onefile 前，先把上一轮的 onedir 产物挪走。
    #
    # PyInstaller 的 --noconfirm 会删掉同名输出目录，而 onefile 的输出是
    # dist\NAME.exe、onedir 的是 dist\NAME\ —— 两者同名不同型，Windows
    # 路径匹配很容易撞车，删目录时更会触发「批量删除」保护。
    #
    # 挪到**同一个盘**下的临时目录（跨盘 rename 会失败）。
    # 打包成功后再挪回来：否则紧随其后的 make_release.py 找不到 onedir
    # 产物，portable 包会被静默跳过（CI 就是这么打出「三缺一」的）。
    stash = None
    if onefile:
        stale = HERE / "dist" / NAME
        if stale.is_dir():
            # ⚠️ 暂存目录重名时**换个名字**，千万别去 rmtree 旧的：
            # onedir 是上千个文件，`shutil.rmtree` 会触发批量删除保护，
            # `ignore_errors=True` 把它吞掉之后，`rename` 又因为目标已存在
            # 而失败 —— 于是旧目录既没删掉也没挪走，PyInstaller 的
            # `--noconfirm` 自己去删又被拦，整个打包就死在这一步
            # （日志里只有一句「打包失败」，看不出原因）。
            base = HERE / "dist" / f"_stash_{NAME}"
            stash = base
            n = 1
            while stash.exists():
                stash = base.with_name(f"{base.name}_{n}")
                n += 1
            try:
                stale.rename(stash)
                print(f"  （已暂存旧的 onedir 产物 → {stash}）")
            except Exception as exc:
                stash = None
                print(f"  （暂存旧 onedir 失败，将让 PyInstaller 自行处理：{exc}）")

    cmd.append(str(HERE / "main.py"))

    print("执行：", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(HERE))
    if proc.returncode != 0:
        print("打包失败")
        return proc.returncode

    dist = HERE / "dist"
    exe = _built_exe(onefile)
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        print()
        print(f"✓ 打包完成: {exe}  ({size_mb:.1f} MB)")
    else:
        print(f"! 未找到预期产物: {exe}")
        return 1

    # 把暂存的 onedir 产物挪回原位，保证 make_release.py --all 两种形态都在
    if stash is not None:
        target = dist / NAME
        try:
            if stash.is_dir() and not target.exists():
                stash.rename(target)
                print(f"  （已恢复 onedir 产物 → {target}）")
        except Exception as exc:
            print(f"  （恢复 onedir 产物失败：{exc}）")
    return 0


def _built_exe(onefile: bool) -> Path:
    """本次构建产物的准确路径（onedir 与 onefile 位置不同）。"""
    dist = HERE / "dist"
    return dist / f"{NAME}.exe" if onefile else dist / NAME / f"{NAME}.exe"


def audit(dist_dir: Path) -> int:
    """对产物跑一遍「高危特征」审计。"""
    script = HERE / "tools" / "audit_features.py"
    if not script.exists():
        print("[warn] 找不到 tools/audit_features.py，跳过审计")
        return 0
    print()
    print("=== 产物特征审计 ===")
    proc = subprocess.run([sys.executable, str(script), str(dist_dir)],
                          cwd=str(HERE))
    return proc.returncode


def sign(exe: Path) -> int:
    """调用自签名脚本（只签本次构建出来的那个 exe）。"""
    script = HERE / "tools" / "sign_release.ps1"
    if not script.exists():
        print("[warn] 找不到 tools/sign_release.ps1，跳过签名")
        return 0
    if not exe.exists():
        print(f"[warn] 找不到待签名文件 {exe}，跳过签名")
        return 0
    print()
    print("=== 自签名 ===")
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-File", str(script), "-Path", str(exe)]
    proc = subprocess.run(cmd, cwd=str(HERE))
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 糕糕校园网自动认证")
    ap.add_argument("--onefile", action="store_true",
                    help="产出单文件 exe（默认 onedir 目录形态）")
    ap.add_argument("--no-clean", action="store_true",
                    help="不清理 PyInstaller 缓存")
    ap.add_argument("--sign", action="store_true",
                    help="打包后执行自签名")
    ap.add_argument("--skip-audit", action="store_true",
                    help="跳过产物特征审计")
    args = ap.parse_args()

    rc = build(onefile=args.onefile, clean=not args.no_clean)
    if rc != 0:
        return rc

    dist = HERE / "dist"
    if not args.skip_audit:
        # ⚠️ 审计失败必须让构建失败。
        # 历史实现忽略了返回值，本地构建命中高危项也能"成功"退出，
        # 门禁形同虚设。
        arc = audit(dist)
        if arc != 0:
            print()
            print(f"✗ 产物审计未通过（退出码 {arc}）—— 已中止发布。")
            print("  请按上面的提示处理后重新构建；")
            print("  确实需要跳过请显式加 --skip-audit。")
            return arc

    if args.sign:
        return sign(_built_exe(args.onefile))
    return 0


if __name__ == "__main__":
    sys.exit(main())
