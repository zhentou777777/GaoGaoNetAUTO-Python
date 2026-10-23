# -*- coding: utf-8 -*-
"""图标生成：用纯 Python 画一个 Wi-Fi 风格的 PNG / ICO。

不依赖 Pillow —— PyInstaller 打包后体积更小，生成逻辑也完全可控。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Sequence, Tuple

RGBA = Tuple[int, int, int, int]

BG = (20, 20, 26, 255)
ACCENT = (251, 114, 153, 255)
WHITE = (255, 255, 255, 245)

ASSETS = Path(__file__).resolve().parent / "assets"
PNG_PATH = ASSETS / "icon.png"
ICO_PATH = ASSETS / "icon.ico"

_SIZE = 256
_SS = 2  # 超采样倍数，用于抗锯齿


def _rounded_rect_alpha(x: float, y: float, size: int, radius: int) -> bool:
    """判断点是否在圆角方形内。"""
    half = size / 2.0
    dx = abs(x - half)
    dy = abs(y - half)
    limit = half - radius
    if dx <= limit or dy <= limit:
        return abs(x - half) <= half and abs(y - half) <= half
    return (dx - limit) ** 2 + (dy - limit) ** 2 <= radius * radius


def _render(size: int) -> List[List[RGBA]]:
    s = size * _SS
    half = s / 2.0
    radius = int(s * 0.22)
    # Wi-Fi 弧线：以 (center_x, center_y) 为圆心，只画上半部分
    cx = half
    cy = s * 0.78
    rings = [(0.34, 0.42), (0.52, 0.60), (0.70, 0.78)]
    dot_r = s * 0.075

    rows: List[List[RGBA]] = []
    for y in range(size):
        row: List[RGBA] = []
        for x in range(size):
            covered = 0
            sr = sg = sb = 0.0
            for sy in range(_SS):
                for sx in range(_SS):
                    px = x * _SS + sx + 0.5
                    py = y * _SS + sy + 0.5
                    if not _rounded_rect_alpha(px, py, s, radius):
                        continue
                    color = BG
                    d = ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
                    if d <= dot_r:
                        color = ACCENT
                    elif py <= cy:
                        for lo, hi in rings:
                            if s * lo <= d <= s * hi:
                                color = WHITE
                                break
                    covered += 1
                    sr += color[0]
                    sg += color[1]
                    sb += color[2]
            n = _SS * _SS
            if covered == 0:
                row.append((0, 0, 0, 0))
            else:
                alpha = int(round(255 * covered / n))
                row.append(
                    (
                        max(0, min(255, int(round(sr / covered)))),
                        max(0, min(255, int(round(sg / covered)))),
                        max(0, min(255, int(round(sb / covered)))),
                        alpha,
                    )
                )
        rows.append(row)
    return rows


def _png_bytes(rows: Sequence[Sequence[RGBA]]) -> bytes:
    h = len(rows)
    w = len(rows[0])
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0
        for px in row:
            raw.extend(px)

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def _ico_bytes(png: bytes, size: int) -> bytes:
    header = struct.pack("<HHH", 0, 1, 1)
    entry = struct.pack(
        "<BBBBHHII",
        0 if size >= 256 else size,
        0 if size >= 256 else size,
        0,
        0,
        1,
        32,
        len(png),
        6 + 16,
    )
    return header + entry + png


def ensure_icons(force: bool = False) -> Tuple[Path, Path]:
    """确保 assets/icon.png 与 assets/icon.ico 存在，返回它们的路径。"""
    ASSETS.mkdir(parents=True, exist_ok=True)
    if not force and PNG_PATH.exists() and ICO_PATH.exists():
        return PNG_PATH, ICO_PATH

    rows = _render(_SIZE)
    png = _png_bytes(rows)
    PNG_PATH.write_bytes(png)
    ICO_PATH.write_bytes(_ico_bytes(png, _SIZE))
    return PNG_PATH, ICO_PATH


if __name__ == "__main__":
    p, i = ensure_icons(force=True)
    print(f"已生成: {p}\n已生成: {i}")
