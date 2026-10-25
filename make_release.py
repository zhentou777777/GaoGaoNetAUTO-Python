# -*- coding: utf-8 -*-
"""打包交付物：三种形态 + zip 归档。

产出形态
--------
1. **onedir 免安装包**（推荐给终端用户）
   ``dist/GaoGaoNetAUTO/`` 整个目录压成 zip。解压即用，不需要装 Python。
2. **单文件 exe**
   ``python build_exe.py --onefile`` 的产物，压成 zip。
3. **源码绿色包**
   源码 + requirements.txt + setup.bat/run.bat/stop.bat，
   给需要改代码或没有 exe 环境的场合。

用法::

    python make_release.py                # 只做 onedir 包
    python make_release.py --onefile      # 追加单文件包
    python make_release.py --source       # 追加源码包
    python make_release.py --all
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = "GaoGaoNetAUTO"
VERSION = "1.0.0"

# 源码包要带的文件
SOURCE_FILES = [
    "main.py", "auth.py", "config.py", "icons.py", "netguard.py",
    "netprobe_win.py", "qrflow.py", "acclogin.py",
    "requirements.txt", "README.md", "LICENSE",
    "build_exe.py", "version_info.txt",
    "make_launchers.py", "make_release.py",
]
SOURCE_DIRS = ["assets", "docs", "tools"]


def desktop_dir() -> Path:
    """定位真实桌面（本机桌面被重定向到 D:\\zhuomian）。"""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_byte * 8),
        ]

    buf = ctypes.create_unicode_buffer(260)
    try:
        ctypes.windll.shell32.SHGetFolderPathW(
            None, 0x0010, None, 0, buf)
        p = Path(buf.value)
        if p.is_dir():
            return p
    except Exception:
        pass
    try:
        import winreg

        key_path = (r"Software\Microsoft\Windows\CurrentVersion"
                    r"\Explorer\User Shell Folders")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            raw, _ = winreg.QueryValueEx(key, "Desktop")
        import os

        p = Path(os.path.expandvars(str(raw)))
        if p.is_dir():
            return p
    except Exception:
        pass
    for cand in (Path.home() / "Desktop", Path("D:/zhuomian"),
                 Path("C:/Users") / __import__("getpass").getuser() / "Desktop"):
        if cand.is_dir():
            return cand
    return Path.home()


def md5(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _zip_dir(src: Path, dst: Path, arc_root: str,
             exclude: tuple = ()) -> Path:
    """把目录打包成 zip。

    :param exclude: 排除的文件名（精确匹配，不含路径）。
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(src.rglob("*")):
            if p.is_dir():
                continue
            if p.name in exclude:
                continue
            rel = p.relative_to(src)
            z.write(p, arc_root + "/" + rel.as_posix())
    return dst


# 绝对不能进发布包的：PFX 私钥 + 密码文件
NEVER_SHIP = (
    "GaoGaoNetAUTO-sign.pfx",
    "pfx-password.txt",
)


def build_onedir_zip(out_dir: Path) -> Path | None:
    src = HERE / "dist" / NAME
    if not (src / f"{NAME}.exe").exists():
        print("[skip] 没有 onedir 产物，先跑 python build_exe.py")
        return None
    dst = out_dir / f"{NAME}-v{VERSION}-免安装.zip"
    print(f"[1/3] 打包 onedir 免安装包 …")
    _zip_dir(src, dst, NAME, exclude=NEVER_SHIP)
    return dst


def build_onefile_zip(out_dir: Path) -> Path | None:
    src = HERE / "dist" / f"{NAME}.exe"
    if not src.exists():
        print("[skip] 没有 onefile 产物，先跑 python build_exe.py --onefile")
        return None
    dst = out_dir / f"{NAME}-v{VERSION}-单文件.zip"
    print(f"[2/3] 打包单文件 exe …")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.write(src, src.name)
        # 把签名证书一并放进去（目标机导入它才能显示「已信任」）。
        # 只放 .cer（公钥），绝不放 .pfx（私钥）。
        cer = HERE / "dist" / NAME / f"{NAME}-sign.cer"
        if cer.exists():
            z.write(cer, cer.name)
    return dst


def build_source_zip(out_dir: Path) -> Path | None:
    stage = HERE / "dist" / f"{NAME}-源码-v{VERSION}"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, exist_ok=True)
    for rel in SOURCE_FILES:
        p = HERE / rel
        if p.exists():
            dst = stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
    for d in SOURCE_DIRS:
        p = HERE / d
        if p.is_dir():
            shutil.copytree(p, stage / d,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    # 便捷脚本
    try:
        subprocess.run([sys.executable, str(HERE / "make_launchers.py"),
                        "--out", str(stage)], check=False)
    except Exception:
        pass
    # 文档包
    docpkg = HERE / "dist" / "文档包"
    if docpkg.is_dir():
        for z in docpkg.glob("*.zip"):
            shutil.copy2(z, stage / z.name)
    dst = out_dir / f"{NAME}-v{VERSION}-源码绿色包.zip"
    print(f"[3/3] 打包源码绿色包 …")
    _zip_dir(stage, dst, f"{NAME}-源码-v{VERSION}")
    shutil.rmtree(stage, ignore_errors=True)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser(description="生成交付包")
    ap.add_argument("--onefile", action="store_true", help="追加单文件包")
    ap.add_argument("--source", action="store_true", help="追加源码绿色包")
    ap.add_argument("--all", action="store_true", help="全部形态")
    ap.add_argument("--out", default="", help="输出目录，默认桌面")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else desktop_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{out_dir}")

    results = []
    r = build_onedir_zip(out_dir)
    if r:
        results.append(r)
    if args.onefile or args.all:
        r = build_onefile_zip(out_dir)
        if r:
            results.append(r)
    if args.source or args.all:
        r = build_source_zip(out_dir)
        if r:
            results.append(r)

    print()
    print("=" * 62)
    print(" 交付包清单")
    print("=" * 62)
    for p in results:
        size = p.stat().st_size / 1024 / 1024
        print(f"  {p.name}")
        print(f"    {size:8.2f} MB   MD5: {md5(p)}")
    print("=" * 62)
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
