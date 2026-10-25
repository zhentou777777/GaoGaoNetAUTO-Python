# -*- coding: utf-8 -*-
"""图标生成：用纯 Python 画一个 Wi-Fi 风格的 PNG / ICO。

不依赖 Pillow —— PyInstaller 打包后体积更小，生成逻辑也完全可控。

输出目录默认是数据目录 ``C:\\GaoGaoNetAUTO\\assets\\``（见 :func:`config.data_dir`），
不再往 exe 旁边写文件：Program Files 下写入需要管理员权限，
而打包成 onedir 后 exe 目录也不一定是可写位置。

ICO 为多尺寸（16/24/32/48/64/128/256），避免资源管理器在缩放时糊掉。
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

RGBA = Tuple[int, int, int, int]

BG = (20, 20, 26, 255)
ACCENT = (251, 114, 153, 255)
WHITE = (255, 255, 255, 245)

# ICO 内嵌的尺寸列表（从小到大）
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

_SIZE = 256
_SS = 2  # 超采样倍数，用于抗锯齿


def default_assets_dir() -> Path:
    """默认图标输出目录：数据目录下的 assets/。

    运行时用（托盘图标需要读 PNG）；打包时由 :mod:`build_exe` 显式指定
    仓库内 ``assets/``，避免污染用户的 C 盘数据目录。
    """
    try:
        from config import data_dir

        return data_dir() / "assets"
    except Exception:
        return Path(__file__).resolve().parent / "assets"


def bundled_assets_dir() -> Path:
    """打包内置的 assets 目录。

    PyInstaller onefile 解包到 ``sys._MEIPASS``；onedir 则在 exe 同级。
    """
    import sys

    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base) / "assets"
        return Path(sys.executable).resolve().parent / "assets"
    return Path(__file__).resolve().parent / "assets"


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


def _ico_bytes(entries: Sequence[Tuple[int, bytes]]) -> bytes:
    """组装多尺寸 ICO（每个尺寸用 PNG 压缩存储，Vista+ 原生支持）。"""
    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    dir_entries = bytearray()
    data = bytearray()
    for size, png in entries:
        dir_entries += struct.pack(
            "<BBBBHHII",
            0 if size >= 256 else size,
            0 if size >= 256 else size,
            0,
            0,
            1,
            32,
            len(png),
            offset,
        )
        data += png
        offset += len(png)
    return bytes(header) + bytes(dir_entries) + bytes(data)


def ensure_icons(force: bool = False,
                 out_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    """确保 icon.png 与 icon.ico 存在，返回它们的路径。

    :param force:  已存在也强制重新生成。
    :param out_dir: 输出目录，默认 :func:`default_assets_dir`。
    """
    # 打包后优先用内置资源（只读，避免往 C 盘反复写）
    if out_dir is None:
        bundled = bundled_assets_dir()
        if not force and (bundled / "icon.png").exists() and \
                (bundled / "icon.ico").exists():
            return bundled / "icon.png", bundled / "icon.ico"

    assets = Path(out_dir) if out_dir else default_assets_dir()
    png_path = assets / "icon.png"
    ico_path = assets / "icon.ico"
    assets.mkdir(parents=True, exist_ok=True)
    if not force and png_path.exists() and ico_path.exists():
        return png_path, ico_path

    # 主 PNG 固定 256（托盘用），ICO 内嵌多尺寸
    png_path.write_bytes(_png_bytes(_render(_SIZE)))

    entries: List[Tuple[int, bytes]] = []
    for s in ICO_SIZES:
        entries.append((s, _png_bytes(_render(s))))
    ico_path.write_bytes(_ico_bytes(entries))
    return png_path, ico_path


if __name__ == "__main__":
    p, i = ensure_icons(force=True)
    print(f"已生成: {p}\n已生成: {i}")
