# -*- coding: utf-8 -*-
"""把 .ps1 脚本转成带 BOM 的 UTF-8。

Windows PowerShell 5.1 读取无 BOM 的 .ps1 时按系统 ANSI 代码页（中文
Windows 是 GBK）解析，脚本里的中文注释和字符串会变成乱码，
轻则输出难看，重则引号被破坏导致语法错误。

**带 BOM 的 UTF-8** 才能让 5.1 正确按 UTF-8 解析。
（PowerShell 7+ 默认就是 UTF-8，不受此影响，但加 BOM 无害。）

用法::

    python tools/fix_ps1_bom.py            # 处理 tools/ 下所有 .ps1
    python tools/fix_ps1_bom.py <文件> ...
"""
from __future__ import annotations

import sys
from pathlib import Path


def fix(path: Path) -> bool:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        print(f"  已带 BOM：{path}")
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        print(f"  跳过（非 UTF-8）：{path}")
        return False
    path.write_text(text, encoding="utf-8-sig")
    print(f"  已加 BOM：{path}")
    return True


def main() -> int:
    if len(sys.argv) > 1:
        targets = [Path(a) for a in sys.argv[1:]]
    else:
        here = Path(__file__).resolve().parent
        targets = sorted(here.rglob("*.ps1"))
        root = here.parent
        targets += sorted(root.glob("*.ps1"))
    if not targets:
        print("没有找到 .ps1 文件")
        return 0
    print("处理 PowerShell 脚本编码：")
    changed = sum(1 for t in targets if t.exists() and fix(t))
    print(f"共处理 {len([t for t in targets if t.exists()])} 个，修改 {changed} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
