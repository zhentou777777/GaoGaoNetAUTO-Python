# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本。

默认产出 **onedir**（目录形态）而非 onefile，理由：

* onefile 启动时把整个包解压到 ``%TEMP%\\._MEIxxxx``，
  这既是杀软���「自解压到临时目录」行为规则的高危命中点，
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

HERE = Path(__file__).resolve().parent
NAME = "GaoGaoNetAUTO"
VERSION = "1.0.0.0"

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
    """打包前的环境自检 —— 尤其�� tkinter。

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

    cmd.append(str(HERE / "main.py"))

    print("执行：", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(HERE))
    if proc.returncode != 0:
        print("打包失败")
        return proc.returncode

    dist = HERE / "dist"
    if onefile:
        exe = dist / f"{NAME}.exe"
    else:
        exe = dist / NAME / f"{NAME}.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        print()
        print(f"✓ 打包完成: {exe}  ({size_mb:.1f} MB)")
    else:
        print(f"! 未找到预期产物: {exe}")
        return 1
    return 0


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


def sign(dist_dir: Path) -> int:
    """调用自签名脚本。"""
    script = HERE / "tools" / "sign_release.ps1"
    if not script.exists():
        print("[warn] 找不到 tools/sign_release.ps1，跳过签名")
        return 0
    exe = dist_dir / NAME / f"{NAME}.exe"
    if not exe.exists():
        exe = dist_dir / f"{NAME}.exe"
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
        audit(dist)

    if args.sign:
        return sign(dist)
    return 0


if __name__ == "__main__":
    sys.exit(main())
