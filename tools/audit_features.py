# -*- coding: utf-8 -*-
"""产物高危特征审计。

Windows Defender 的判定分两块：

1. **静态特征** —— 可执行文件里能扫到的字符串 / PE 结构 / 导入表。
2. **行为特征** —— 运行时干了什么（起子进程、写注册表自启动、读设备指纹）。

本脚本做静态侧自查，把「杀软容易盯上的点」列出来并给出处置建议。
用法::

    python tools/audit_features.py                 # 审计 dist/
    python tools/audit_features.py dist/GaoGaoNetAUTO
    python tools/audit_features.py --source-only   # 只审源码

退出码：0 = 没有高危项；1 = 存在高危项。
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path
from typing import List, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# ------------------------------------------------------------ 静态特征库
# 每条：(标签, 严重级, 命中关键字, 说明, 处置建议)
# 严重级：HIGH=直接触发杀软规则；MED=启发式加分项；LOW=可接受但值得知道

STATIC_RULES: List[Tuple[str, str, bytes, str, str]] = [
    ("UPX 壳", "HIGH", b"UPX!", "UPX 加壳是杀软最爱的信号之一（自解压 + 入口跳板）",
     "构建时加 --noupx（本项目已默认开启）"),
    ("注册表 Run 键引用", "LOW",
     b"CurrentVersion\\Run", "二进制里出现 Run 键字符串",
     "本版本仅用于**清理**旧版遗留的自启项（读+删），不再写入；"
     "源码规则会精确校验是否存在写入动作"),
    ("读取设备指纹", "MED", b"MachineGuid",
     "二进制里出现 MachineGuid 字符串",
     "本版本仅在**解密历史配置**时回退使用，新配置用不含设备标识的轻量种子"),
    ("注入类 API", "HIGH", b"WriteProcessMemory",
     "跨进程写入，远程注入类行为",
     "本项目不应出现，出现即为误引入"),
    ("注入类 API", "HIGH", b"VirtualAllocEx",
     "跨进程分配内存，远程注入类行为",
     "本项目不应出现，出现即为误引入"),
    ("键盘钩子", "HIGH", b"SetWindowsHookEx",
     "全局钩子常被用于键盘记录",
     "本项目不应出现，出现即为误引入"),
]

# 已知无害的命中：PyInstaller 的 bootloader 自带这些字符串，
# onedir / onefile 都会出现，不构成差异化特征。
BENIGN_NOTES = {
    b"_MEI": "PyInstaller bootloader 自带字符串，onedir 同样存在，不作为差异特征",
    b"CreateProcess": "PyInstaller bootloader 启动子解释器用，正常行为",
    b"ShellExecute": "ShellExecute 会带出可见的外部程序窗口",
}

# 需要在源码里查的关键字（相对源码根）
SOURCE_RULES: List[Tuple[str, str, str, str]] = [
    ("写注册表 Run 自启动", "HIGH", r"SetValueEx\s*\([^)]*Run",
     "写 HKCU\\...\\CurrentVersion\\Run（持久化高危行为）"),
    ("读取设备指纹", "MED", r"MachineGuid",
     "读注册表 MachineGuid"),
]

# 源码扫描时要跳过的文件（审计工具自身、文档目录、构建脚本）
SOURCE_SKIP_FILES = {
    "tools/audit_features.py",
    "docs/build_html.py",
    "make_docs_package.py",
}
SOURCE_SKIP_DIRS = {
    ".git", "build", "dist", "__pycache__", ".workbuddy", "docs", "release",
    "tools",
}


def _read_all(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except Exception:
        return b""


def _iter_targets(target: Path):
    """展开目标：目录则收集其中的 .exe/.pyd/.dll/.py 文件。

    **只保留主 exe 与自带的 .pyd/.py**：``_internal/`` 下的系统 DLL
    （尤其是 ``api-ms-win-*`` 这类 API set 转发桩）里必然出现
    ``VirtualAllocEx`` / ``WriteProcessMemory`` 等名字——因为它们转发
    整个 kernel32 API 集。扫这些是纯误报。
    """
    if target.is_file():
        yield target
        return
    for p in sorted(target.rglob("*")):
        if not p.is_file():
            continue
        name = p.name.lower()
        suffix = p.suffix.lower()
        if suffix == ".exe":
            yield p
        elif suffix in (".pyd", ".py"):
            yield p
        elif suffix == ".dll":
            # 排除 API set 转发桩与系统运行库
            if name.startswith("api-ms-win-") or name.startswith("ext-ms-"):
                continue
            if name.startswith(("vcruntime", "ucrtbase", "msvcp", "python3")):
                continue
            # Tcl/Tk 运行时：含 SetWindowsHookEx 等字符串是 Tk 自身的
            # 窗口事件机制，与「键盘记录」无关
            if name.startswith(("tk8", "tcl8", "tk86", "tcl86")):
                continue
            yield p


def audit_binary(path: Path) -> List[Tuple[str, str, str]]:
    """扫描单个二进制，返回命中的规则。"""
    data = _read_all(path)
    if not data:
        return []
    hits: List[Tuple[str, str, str]] = []
    for label, level, key, desc, fix in STATIC_RULES:
        if not key:
            continue
        if key in data:
            hits.append((label, level, f"{desc}\n      处置：{fix}"))
    return hits


def benign_hits(path: Path) -> List[str]:
    """返回「命中但已知无害」的说明。"""
    data = _read_all(path)
    out: List[str] = []
    for key, note in BENIGN_NOTES.items():
        if key in data:
            out.append(note)
    return out


def audit_source(root: Path) -> List[Tuple[str, str, str]]:
    """扫描源码目录。"""
    hits: List[Tuple[str, str, str]] = []
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if rel in SOURCE_SKIP_FILES:
            continue
        if any(part in SOURCE_SKIP_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for label, level, pattern, desc in SOURCE_RULES:
            if re.search(pattern, text):
                hits.append((label, level, f"{rel} —— {desc}"))
    return hits


def _pe_info(data: bytes) -> str:
    """取 PE 头部的几个关键信息（是否 64 位、是否带签名目录）。"""
    try:
        if data[:2] != b"MZ":
            return "非 PE 文件"
        pe_off = struct.unpack("<I", data[0x3C:0x40])[0]
        if data[pe_off:pe_off + 4] != b"PE\x00\x00":
            return "PE 头损坏"
        machine = struct.unpack("<H", data[pe_off + 4:pe_off + 6])[0]
        opt_off = pe_off + 24
        magic = struct.unpack("<H", data[opt_off:opt_off + 2])[0]
        bits = 64 if magic == 0x20B else 32
        # 数据目录起点：PE32+ 在可选头 +112，PE32 在 +96
        dd_off = opt_off + (112 if magic == 0x20B else 96)
        # 证书表是数据目录第 4 项（索引 4），每项 8 字节
        cert_size = struct.unpack("<I", data[dd_off + 4 * 8 + 4:dd_off + 4 * 8 + 8])[0]
        signed = "已签名" if cert_size else "未签名"
        return f"{bits} 位 | machine=0x{machine:04X} | 证书目录: {signed}"
    except Exception as exc:
        return f"解析失败: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser(description="产物高危特征审计")
    ap.add_argument("target", nargs="?",
                    default=str(ROOT / "dist"), help="要审计的产物目录或文件")
    ap.add_argument("--source-only", action="store_true", help="只审源码")
    args = ap.parse_args()

    problems = 0

    print("=" * 68)
    print(" 产物高危特征审计 · 糕糕校园网自动认证")
    print("=" * 68)

    if not args.source_only:
        target = Path(args.target)
        if not target.exists():
            print(f"[warn] 目标不存在：{target}")
        else:
            print(f"\n[1] 二进制静态特征 —— {target}")
            found_any = False
            for p in _iter_targets(target):
                hits = audit_binary(p)
                data = _read_all(p)
                pe = _pe_info(data) if p.suffix.lower() == ".exe" else ""
                rel = p.relative_to(ROOT) if str(p).startswith(str(ROOT)) else p
                print(f"\n  · {rel}")
                if pe:
                    print(f"      PE: {pe}")
                if not hits:
                    print("      未命中高危特征")
                else:
                    found_any = True
                    for label, level, desc in hits:
                        mark = "!!" if level == "HIGH" else ("~ " if level == "MED" else "  ")
                        print(f"    {mark} [{level}] {label}")
                        print(f"      {desc}")
                        if level == "HIGH":
                            problems += 1
                # 良性命中只在主 exe 上提一次
                if p.suffix.lower() == ".exe":
                    for note in benign_hits(p):
                        print(f"      · 良性命中（不计分）：{note}")
            if not found_any:
                print("\n  未发现高危静态特征。")

    print(f"\n[2] 源码侧检查 —— {ROOT}")
    src_hits = audit_source(ROOT)
    if not src_hits:
        print("  未发现问题模式。")
    else:
        for label, level, desc in src_hits:
            mark = "!!" if level == "HIGH" else "~ "
            print(f"  {mark} [{level}] {label}：{desc}")
            if level == "HIGH":
                problems += 1

    print("\n" + "=" * 68)
    if problems:
        print(f"审计结论：发现 {problems} 项高危项，需要处理。")
        print("=" * 68)
        return 1
    print("审计结论：未发现高危项。")
    print("提示：自签名只消除「未知发布者」外观；SmartScreen 信誉需要时间积累，")
    print("      根治要靠 OV / Azure Trusted Signing 等受信任证书。")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
