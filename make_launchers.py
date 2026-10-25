# -*- coding: utf-8 -*-
"""生成 便捷脚本：setup.bat / run.bat / stop.bat。

打包完成后放到 onedir 目录里，用户双击就能用，
不需要记命令行参数。

用法::

    python make_launchers.py                 # 生成到 dist/GaoGaoNetAUTO/
    python make_launchers.py --out <目录>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

SETUP_BAT = r"""@echo off
chcp 65001 >nul
rem ============================================================
rem  糕糕校园网自动认证 —— 首次安装
rem ============================================================
setlocal
cd /d "%~dp0"

echo.
echo  [1/3] 检查运行环境...
where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo.
    echo   [!] 没找到 Python。
    echo       请到 https://www.python.org/downloads/ 下载安装，
    echo       安装时务必勾选 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)
echo       [OK] Python 已就绪

echo.
echo  [2/3] 安装依赖...
python -m pip install -r requirements.txt --quiet --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo   [!] 依赖安装失败。请检查网络后重试。
    echo       国内网络建议先执行：
    echo         pip config set global.index-url https://mirrors.aliyun.com/pypi/simple
    echo.
    pause
    exit /b 1
)
echo       [OK] 依赖已安装

echo.
echo  [3/3] 创建数据目录...
if not exist "C:\GaoGaoNetAUTO" mkdir "C:\GaoGaoNetAUTO"
echo       [OK] C:\GaoGaoNetAUTO

echo.
echo ============================================================
echo   安装完成！双击 run.bat 启动程序。
echo   首次使用请在设置里填云高账号密码，或用「扫码登录」。
echo ============================================================
echo.
pause
"""

RUN_BAT = r"""@echo off
chcp 65001 >nul
rem ============================================================
rem  糕糕校园网自动认证 —— 启动
rem ============================================================
setlocal
cd /d "%~dp0"

where pythonw.exe >nul 2>nul
if errorlevel 1 (
    echo [!] 没找到 Python，请先运行 setup.bat
    pause
    exit /b 1
)

start "" pythonw.exe main.py %*
rem 后台模式：加 --silent 参数（不弹主窗口）
"""

STOP_BAT = r"""@echo off
chcp 65001 >nul
rem ============================================================
rem  糕糕校园网自动认证 —— 停止后台守护
rem ============================================================
setlocal

echo 正在停止 糕糕校园网自动认证 ...
taskkill /IM pythonw.exe /FI "WINDOWTITLE eq *糕糕*" /F >nul 2>nul
taskkill /IM GaoGaoNetAUTO.exe /F >nul 2>nul

echo 已停止。
timeout /t 2 >nul
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="生成便捷启动脚本")
    ap.add_argument("--out", default="", help="输出目录")
    args = ap.parse_args()

    out = Path(args.out) if args.out else (HERE / "dist" / "GaoGaoNetAUTO")
    out.mkdir(parents=True, exist_ok=True)

    for name, content in (("setup.bat", SETUP_BAT),
                          ("run.bat", RUN_BAT),
                          ("stop.bat", STOP_BAT)):
        p = out / name
        # .bat 由 cmd.exe 读取，中文必须用 GBK（系统 ANSI 代码页）
        p.write_bytes(content.encode("gbk", errors="replace"))
        print(f"已生成: {p}")

    # 源码包场景需要把 requirements.txt 一起带上
    req = HERE / "requirements.txt"
    if req.exists() and not (out / "requirements.txt").exists():
        (out / "requirements.txt").write_text(
            req.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"已生成: {out / 'requirements.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
