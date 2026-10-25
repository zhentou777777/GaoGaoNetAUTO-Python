# -*- coding: utf-8 -*-
"""统一日志：文件落盘 + 轮转 + 脱敏。

为什么需要
----------
评审指出项目里散落着 70+ 个 ``except Exception: pass``，出问题只能靠猜；
而 GUI 里的日志框在关掉程序后就没了。这里补一个**结构化日志文件**：

* 落盘到 ``<数据目录>/logs/app.log``；
* 按大小轮转，最多保留 3 份（``app.log`` / ``app.1.log`` / ``app.2.log``）；
* **自动脱敏** —— Token、手机号、密码一律不落原文。

脱敏规则
--------
* ``Authorization: Bearer xxx`` → ``Bearer ***``
* JSON 里的 ``"token": "xxx"`` / ``"access_token": "xxx"`` → ``"***"``
* 11 位手机号 → ``138****8000``
* 任何长串（≥24 位、字母数字混合）→ 前 6 后 4，中间 ``***``
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Optional

MAX_BYTES = 512 * 1024      # 单文件上限 512 KB
KEEP_FILES = 3              # 保留份数（含当前文件）

_lock = threading.Lock()
_sink: Optional["FileSink"] = None


# ---------------------------------------------------------------- 脱敏

_PATTERNS = [
    # Authorization 头
    (re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?[^\s,;\"'}]+"),
     r"\1***"),
    # JSON / query 里的 token 字段
    (re.compile(r"(?i)([\"']?(?:access_)?token[\"']?\s*[:=]\s*[\"']?)([^\"'\s,&}]+)"),
     r"\1***"),
    # 密码字段
    (re.compile(r"(?i)([\"']?(?:password|passwd|pwd|secret)[\"']?\s*[:=]\s*[\"']?)([^\"'\s,&}]+)"),
     r"\1***"),
    # 手机号（11 位中国大陆号码）
    (re.compile(r"(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)"),
     r"\1****\3"),
]


def _mask_long_strings(text: str) -> str:
    """把长得像凭据的长串替换成「前 6 后 4」。

    只处理**混合大小写字母 + 数字**且长度 ≥ 24 的串 ——
    英文句子、普通路径不会被误伤。
    """

    def repl(m: re.Match) -> str:
        s = m.group(0)
        has_upper = any(c.isupper() for c in s)
        has_lower = any(c.islower() for c in s)
        has_digit = any(c.isdigit() for c in s)
        if sum([has_upper, has_lower, has_digit]) < 2:
            return s
        return f"{s[:6]}***{s[-4:]}"

    return re.sub(r"[A-Za-z0-9_\-.]{24,}", repl, text)


def redact(text: str) -> str:
    """对一段文本做脱敏。"""
    if not text:
        return ""
    out = str(text)
    for pat, repl in _PATTERNS:
        out = pat.sub(repl, out)
    out = _mask_long_strings(out)
    return out


# ---------------------------------------------------------------- 文件 sink


class FileSink:
    """带轮转的文件日志。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _rotate_if_needed(self) -> None:
        try:
            if not self.path.exists():
                return
            if self.path.stat().st_size < MAX_BYTES:
                return
            # app.log → app.1.log → app.2.log → 丢弃
            for i in range(KEEP_FILES - 1, 0, -1):
                src = self.path.with_suffix(self.path.suffix + f".{i}.log")
                dst = self.path.with_suffix(self.path.suffix + f".{i + 1}.log")
                if src.exists():
                    if i + 1 >= KEEP_FILES:
                        try:
                            src.unlink()
                        except Exception:
                            pass
                    else:
                        try:
                            src.replace(dst)
                        except Exception:
                            pass
            self.path.replace(self.path.with_suffix(self.path.suffix + ".1.log"))
        except Exception:
            pass

    def write(self, level: str, message: str) -> None:
        try:
            with _lock:
                self._rotate_if_needed()
                stamp = time.strftime("%Y-%m-%d %H:%M:%S")
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(f"[{stamp}] {level:<5} {redact(message)}\n")
        except Exception:
            pass


# ---------------------------------------------------------------- 对外 API


def init(logs_dir_path: Path) -> None:
    """初始化日志文件（程序启动时调用一次）。"""
    global _sink
    try:
        _sink = FileSink(Path(logs_dir_path) / "app.log")
    except Exception:
        _sink = None


def log(level: str, message: str) -> None:
    """写一条日志。无 sink 时静默忽略（不拖慢主流程）。"""
    if _sink is None:
        return
    try:
        _sink.write(level, message)
    except Exception:
        pass


def info(message: str) -> None:
    log("INFO", message)


def warn(message: str) -> None:
    log("WARN", message)


def error(message: str) -> None:
    log("ERROR", message)


def path() -> Optional[Path]:
    """当前日志文件路径（未初始化时返回 None）。"""
    return _sink.path if _sink else None
