# -*- coding: utf-8 -*-
"""PyInstaller 打包脚本：生成单文件、无控制台窗口的 exe。

用法：
    pip install -r requirements.txt pyinstaller
    python build_exe.py

产物：
    dist/GaoGaoNetAUTO.exe
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "GaoGaoNetAUTO"


def main() -> int:
    icon = HERE / "assets" / "icon.ico"
    assets = HERE / "assets"

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile", "--windowed",
        "--name", NAME,
        "--hidden-import", "pystray._win32",
        "--exclude-module", "playwright",
    ]
    if icon.exists():
        cmd += ["--icon", str(icon)]
    if assets.exists():
        cmd += ["--add-data", f"{assets}{os.pathsep}assets"]

    cmd.append(str(HERE / "main.py"))

    print("执行：", " ".join(cmd))
    proc = subprocess.run(cmd, cwd=str(HERE))
    if proc.returncode != 0:
        print("打包失败")
        return proc.returncode

    exe = HERE / "dist" / f"{NAME}.exe"
    if exe.exists():
        size_mb = exe.stat().st_size / 1024 / 1024
        print()
        print(f"✓ 打包完成: {exe}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
