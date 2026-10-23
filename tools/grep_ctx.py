# -*- coding: utf-8 -*-
"""在压缩后的 JS 里定位关键字上下文。

用法: python grep_ctx.py <file> <keyword> [上下文长度]
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def find_contexts(text: str, kw: str, ctx: int = 260, limit: int = 20) -> int:
    hits = 0
    for m in re.finditer(re.escape(kw), text):
        hits += 1
        if hits > limit:
            print(f"  ... (超过 {limit} 处，仅显示前 {limit} 处)")
            break
        s = max(0, m.start() - ctx)
        e = min(len(text), m.end() + ctx)
        seg = text[s:e].replace("\n", " ⏎ ")
        print(f"[{hits}] …{seg}…")
        print("-" * 70)
    return hits


def main() -> int:
    if len(sys.argv) < 3:
        print("用法: python grep_ctx.py <file> <keyword> [ctx] [limit]")
        return 1
    path = Path(sys.argv[1])
    kw = sys.argv[2]
    ctx = int(sys.argv[3]) if len(sys.argv) > 3 else 260
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 20
    text = path.read_text(encoding="utf-8", errors="ignore")
    print(f"=== {path.name} 中 '{kw}' 的上下文 ===")
    n = find_contexts(text, kw, ctx, limit)
    print(f"共命中 {n} 处")
    return 0


if __name__ == "__main__":
    sys.exit(main())
