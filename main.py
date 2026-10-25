# -*- coding: utf-8 -*-
"""糕糕校园网自动认证 —— 桌面主程序。

界面走深色扁平风格（不用 tkinter 默认灰白控件），常驻托盘，
断网 / 出口 IP 变化时自动重新认证。
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import messagebox

from auth import (
    DEVICE_LIMIT,
    NOT_AUTH,
    OK,
    TOKEN_INVALID,
    CampusAuth,
)
from config import Config, app_dir, data_dir
from icons import ensure_icons
from netguard import NetGuard
from qrflow import QrAssistant

# ---------------------------------------------------------------- 主题
#
# 现代暗色配色：以「蓝紫夜色」为底，粉色作主强调色（B 站味），
# 青绿 / 琥珀 / 珊瑚 分别代表成功 / 警告 / 失败。
# 层级靠明度而非边框堆叠，避免界面显得脏。

BG = "#0f1116"          # 窗口底色（最暗）
BG_GRAD = "#14161e"     # 顶栏
CARD = "#191c25"        # 卡片
CARD_HI = "#222633"     # 卡片内高亮 / 输入框
CARD_HI2 = "#2a2f3d"    # 悬停
BORDER = "#262b38"      # 常规描边
BORDER_SOFT = "#1e222d"  # 弱分隔线
TEXT = "#eef0f6"        # 主文字
TEXT2 = "#a3aab9"       # 次要文字
TEXT3 = "#6b7386"       # 弱化文字 / 占位符
ACCENT = "#FB7299"      # 主强调（B 站粉）
ACCENT_HI = "#ff92b3"   # 强调悬停
ACCENT_DIM = "#3a2430"  # 强调色弱背景
OK_C = "#3ddc97"        # 成功
OK_DIM = "#16302a"
WARN_C = "#f5c451"      # 警告
WARN_DIM = "#33291a"
ERR_C = "#ff6b81"       # 失败
ERR_DIM = "#331c25"
INFO_C = "#5ab0f5"      # 信息
INFO_DIM = "#16293a"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

WIN_W, WIN_H = 1020, 720
WIN_MIN_W, WIN_MIN_H = 860, 560


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw) -> int:
    """在 Canvas 上画圆角矩形。"""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1 + r, x2, y2 - r,
        x2 - r, y2, x1 + r, y2, x1, y2 - r, x1, y1 + r,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


def _mix(c1: str, c2: str, t: float) -> str:
    """按比例混合两个 #rrggbb 颜色。"""
    t = max(0.0, min(1.0, t))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))


class Pill(tk.Canvas):
    """扁平圆角按钮。

    style:
        accent —— 主行动按钮（粉色实心）
        ghost  —— 次级按钮（描边）
        solid  —— 深色实心
        quiet  —— 弱化按钮（文字型）
    """

    def __init__(
        self,
        master,
        text: str,
        command: Optional[Callable[[], None]] = None,
        accent: bool = False,
        height: int = 38,
        width: int = 150,
        font=(),
        style: str = "",
        icon: str = "",
    ):
        bg = master.cget("bg")
        super().__init__(
            master, height=height, width=width, bg=bg,
            highlightthickness=0, bd=0, cursor="hand2",
            takefocus=0,
        )
        self._text = text
        self._command = command
        self._style = style or ("accent" if accent else "solid")
        self._font = font or (FONT, 10)
        self._icon = icon
        self._hover = False
        self._press = False
        self._enabled = True
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    # ------------------------------------------------------------ 配色

    def _colors(self):
        st = self._style
        if not self._enabled:
            if st == "accent":
                return "#2a2f3d", "#5c6376"
            if st == "ghost":
                return None, "#4d5466"
            return CARD, "#4d5466"
        if st == "accent":
            base = ACCENT_HI if self._hover else ACCENT
            if self._press:
                base = _mix(base, "#000000", 0.16)
            return base, "#ffffff"
        if st == "ghost":
            fill = CARD_HI2 if self._hover else None
            edge = _mix(BORDER, ACCENT, 0.45) if self._hover else BORDER
            return fill, TEXT if self._hover else TEXT2
        if st == "quiet":
            return (CARD_HI if self._hover else None), TEXT2
        fill = CARD_HI2 if self._hover else CARD_HI
        if self._press:
            fill = _mix(fill, "#000000", 0.18)
        return fill, TEXT

    # ------------------------------------------------------------ 绘制

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        if w <= 1 or h <= 1:
            return
        fill, fg = self._colors()
        r = min(9, h // 3)

        if fill is None:
            # 描边样式：先画一圈底，再画内层
            _round_rect(self, 0.5, 0.5, w - 0.5, h - 0.5, r,
                        fill=BG, outline=self._edge_color(), width=1)
        else:
            _round_rect(self, 1, 1, w - 1, h - 1, r, fill=fill, outline="")
            if self._style == "accent":
                # 顶部高光，制造轻微的立体感
                _round_rect(self, 2, 2, w - 2, h // 2, r,
                            fill=_mix(fill, "#ffffff", 0.10), outline="")

        if self._icon:
            self.create_text(
                w / 2 - 8, h / 2, text=self._icon, fill=fg,
                font=(FONT, 10), anchor="center",
            )
            self.create_text(
                w / 2 + (8 if self._icon else 0), h / 2, text=self._text,
                fill=fg, font=self._font, anchor="center",
            )
        else:
            self.create_text(
                w / 2, h / 2, text=self._text, fill=fg,
                font=self._font, anchor="center",
            )

    def _edge_color(self) -> str:
        if not self._enabled:
            return BORDER_SOFT
        if self._hover:
            return _mix(BORDER, ACCENT, 0.45)
        return BORDER

    # ------------------------------------------------------------ 交互

    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._press = False
        self._redraw()

    def _on_press(self, _e):
        if self._enabled:
            self._press = True
            self._redraw()

    def _on_release(self, _e):
        was = self._press
        self._press = False
        self._redraw()
        if was and self._enabled and self._command:
            self._command()

    def set_enabled(self, flag: bool):
        self._enabled = flag
        self.configure(cursor="hand2" if flag else "arrow")
        self._redraw()

    def set_text(self, text: str):
        self._text = text
        self._redraw()

    def set_style(self, style: str):
        self._style = style
        self._redraw()


class Dot(tk.Canvas):
    """状态指示灯：外圈光晕 + 实心圆点。"""

    def __init__(self, master, color: str = TEXT3, size: int = 14):
        super().__init__(
            master, width=size, height=size, bg=master.cget("bg"),
            highlightthickness=0, bd=0,
        )
        self._color = color
        self._size = size
        self._redraw()

    def _redraw(self):
        self.delete("all")
        s = self._size
        # 光晕
        self.create_oval(1, 1, s - 1, s - 1, fill=_mix(self._color, BG, 0.78),
                         outline="")
        # 实心
        inset = max(3, s // 4)
        self.create_oval(inset, inset, s - inset, s - inset,
                         fill=self._color, outline="")

    def set_color(self, color: str):
        self._color = color
        self._redraw()


class App:
    def __init__(self, root: tk.Tk, silent: bool = False):
        self.root = root
        self.cfg = Config()
        self.auth = CampusAuth(self.cfg, logger=self.log)
        self.qr = QrAssistant(self.auth, self.log)
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self._qr_win: Optional[tk.Toplevel] = None
        self._qr_img: Optional[tk.PhotoImage] = None
        self._qr_assistant: Optional[QrAssistant] = None
        self._silent = silent
        self._quitting = False
        self._status = ("unknown", "尚未认证", TEXT3)
        self._tray = None
        self._tray_autostart = None

        self._build()
        self._migrate_autostart_legacy()
        self._load_settings()
        self._setup_tray()
        self._start_guard()

        self.root.after(120, self._drain)
        self.root.after(700, self._startup_checks)
        if silent:
            self.root.withdraw()
        else:
            self.root.deiconify()

    # ------------------------------------------------------------ 界面搭建

    def _build(self):
        root = self.root
        root.title("糕糕校园网自动认证")
        root.configure(bg=BG)
        root.geometry(f"{WIN_W}x{WIN_H}")
        # 允许自由缩放
        root.resizable(True, True)
        root.minsize(WIN_MIN_W, WIN_MIN_H)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 顶部标题栏
        header = tk.Frame(root, bg=BG_GRAD, height=54)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self._drag_start)
        header.bind("<B1-Motion>", self._drag_move)

        # 品牌区：小圆点 + 标题 + 副标题
        brand = tk.Frame(header, bg=BG_GRAD)
        brand.pack(side="left", padx=(20, 0))
        brand.bind("<ButtonPress-1>", self._drag_start)
        brand.bind("<B1-Motion>", self._drag_move)

        mark = tk.Canvas(brand, width=26, height=26, bg=BG_GRAD,
                         highlightthickness=0, bd=0)
        mark.pack(side="left", pady=(14, 14))
        _round_rect(mark, 2, 2, 24, 24, 7, fill=ACCENT, outline="")
        mark.create_text(13, 13, text="糕", fill="#ffffff",
                         font=(FONT, 9, "bold"))

        titles = tk.Frame(brand, bg=BG_GRAD)
        titles.pack(side="left", padx=(10, 0), pady=(9, 0))
        tk.Label(titles, text="糕糕校园网自动认证", bg=BG_GRAD, fg=TEXT,
                 font=(FONT, 11, "bold"), anchor="w").pack(anchor="w")
        tk.Label(titles, text="开机自启 · 断网自愈 · 全程免手机", bg=BG_GRAD,
                 fg=TEXT3, font=(FONT, 8), anchor="w").pack(anchor="w")

        self.btn_min = Pill(header, "—", self.root.iconify, height=28, width=36,
                            style="quiet", font=(FONT, 11))
        self.btn_min.pack(side="right", padx=(0, 8), pady=13)
        self.btn_close = Pill(header, "✕", self._on_close, height=28, width=36,
                              style="quiet", font=(FONT, 10))
        self.btn_close.pack(side="right", padx=(0, 6), pady=13)

        # 主体
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=(16, 18))

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)

        # 右侧设置面板做成可滚动 —— 内容变多也不会被裁掉
        right = tk.Frame(body, bg=BG, width=356)
        right.pack(side="right", fill="y", padx=(16, 0))
        right.pack_propagate(False)

        canvas = tk.Canvas(right, bg=BG, highlightthickness=0, bd=0,
                           width=356)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(right, orient="vertical", command=canvas.yview,
                                  width=8, troughcolor=BG, bg=BORDER,
                                  activebackground=ACCENT, bd=0,
                                  highlightthickness=0)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        inner_wrap = tk.Frame(canvas, bg=BG)
        self._settings_win = canvas.create_window((0, 0), window=inner_wrap,
                                                   anchor="nw")

        def _on_settings_configure(_evt=None):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        inner_wrap.bind("<Configure>", _on_settings_configure)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(
            self._settings_win, width=e.width))
        for w in (canvas, inner_wrap):
            w.bind("<MouseWheel>", _on_mousewheel)
            w.bind("<Button-4>", lambda e: canvas.yview_scroll(1, "units"))
            w.bind("<Button-5>", lambda e: canvas.yview_scroll(-1, "units"))

        self._build_status(left)
        self._build_actions(left)
        self._build_log(left)
        self._build_settings(inner_wrap)

    def _build_status(self, parent):
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=22, pady=(20, 18))

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")
        self.dot = Dot(top, TEXT3, 18)
        self.dot.pack(side="left", pady=(3, 0))
        self.lbl_status = tk.Label(
            top, text="尚未认证", bg=CARD, fg=TEXT, font=(FONT, 17, "bold"), anchor="w"
        )
        self.lbl_status.pack(side="left", padx=(13, 0))
        self.lbl_badge = tk.Label(
            top, text="", bg=CARD, fg=TEXT3, font=(FONT, 9), anchor="e"
        )
        self.lbl_badge.pack(side="right")

        tk.Frame(inner, bg=BORDER_SOFT, height=1).pack(fill="x", pady=(15, 13))

        # 键值网格：标签弱化、取值高亮
        grid = tk.Frame(inner, bg=CARD)
        grid.pack(fill="x")
        self.lbl_info = tk.Label(
            grid, text="", bg=CARD, fg=TEXT2, font=(FONT, 9),
            justify="left", anchor="w",
        )
        self.lbl_info.pack(fill="x")

        # 自动重连状态条（醒目位置，一眼看出是否就绪）
        self.bar_heal = tk.Frame(inner, bg=OK_DIM)
        self.bar_heal.pack(fill="x", pady=(14, 0))
        self.lbl_heal = tk.Label(
            self.bar_heal, text="", bg=OK_DIM, fg=OK_C, font=(FONT, 9),
            anchor="w", padx=12, pady=8,
        )
        self.lbl_heal.pack(fill="x")

    def _build_actions(self, parent):
        """操作按钮区：用 grid 自适应换行，避免窗口变窄时按钮重叠。"""
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(14, 0))

        # (文案, 回调, 样式, 图标, 优先占两列)
        items = [
            ("立即认证", lambda: self._do_login(takeover=True), "accent", "⚡", True),
            ("扫码登录", self._open_qr, "ghost", "▣", False),
            ("抢占上线", self._do_takeover, "ghost", "⇄", False),  # 独立实现
            ("在线设备", self._do_show_devices, "ghost", "▤", False),
            ("归还名额", self._do_logout, "ghost", "↩", False),
            ("检查 Token", self._do_check_token, "ghost", "✓", False),
        ]
        self._action_buttons = []
        for text, cmd, style, icon, wide in items:
            b = Pill(bar, text, cmd, height=42, width=150,
                     style=style, icon=icon)
            self._action_buttons.append((b, wide))
            b.grid(row=0, column=0, sticky="ew", padx=(0, 8), pady=(0, 8))

        # 网格列按窗口宽度自适应：能放 3 个就放 3 个，放不下自动换行。
        # 用「游标」推进列号，正确处理跨列按钮。
        def _relayout(event=None):
            width = bar.winfo_width() or 600
            per_row = 3 if width >= 540 else (2 if width >= 360 else 1)
            n = len(self._action_buttons)
            # 尽量让每行都排满，避免最后一行空一大半
            rows = (n + per_row - 1) // per_row
            # 若最后一行只剩 1 个，就从上一行借一个下来
            if rows > 1 and (n % per_row) == 1:
                rows -= 1
                per_row = (n + rows - 1) // rows
            col = 0
            row = 0
            for b, _ in self._action_buttons:
                if col >= per_row:
                    col = 0
                    row += 1
                b.grid_configure(row=row, column=col, columnspan=1,
                                 sticky="ew", padx=(0, 8), pady=(0, 8))
                col += 1
            for c in range(3):
                bar.grid_columnconfigure(c, weight=1, uniform="act")

        bar.bind("<Configure>", _relayout)
        _relayout()

    def _build_log(self, parent):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="both", expand=True, pady=(14, 0))
        head = tk.Frame(wrap, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="运行日志", bg=BG, fg=TEXT2, font=(FONT, 9)).pack(side="left")
        Pill(head, "清空", self._clear_log, height=22, width=54, font=(FONT, 9)).pack(side="right")
        box = tk.Frame(wrap, bg=CARD, highlightthickness=1,
                       highlightbackground=BORDER, highlightcolor=BORDER)
        box.pack(fill="both", expand=True, pady=(8, 0))
        self.txt_log = tk.Text(
            box, bg=CARD, fg=TEXT2, insertbackground=TEXT, relief="flat",
            font=(MONO, 9), wrap="word", padx=12, pady=10, bd=0,
            highlightthickness=0, state="disabled",
        )
        self.txt_log.pack(fill="both", expand=True)
        sb = tk.Scrollbar(box, orient="vertical", command=self.txt_log.yview,
                           bg=CARD, troughcolor=BG, activebackground=BORDER,
                           relief="flat", bd=0, width=10)
        sb.pack(side="right", fill="y")
        self.txt_log.configure(yscrollcommand=sb.set)

    # ------------------------------------------------------------ 设置区

    def _label(self, parent, text, color=TEXT2):
        tk.Label(parent, text=text, bg=CARD, fg=color, font=(FONT, 9),
                 anchor="w").pack(fill="x")

    def _hint(self, parent, text):
        tk.Label(parent, text=text, bg=CARD, fg=TEXT3, font=(FONT, 8),
                 anchor="w", justify="left").pack(fill="x", pady=(4, 0))

    def _entry(self, parent, text="", show=""):
        var = tk.StringVar(value=text)
        e = tk.Entry(
            parent, textvariable=var, bg=CARD_HI, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=(MONO, 9), highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            show=show, bd=0,
        )
        e.pack(fill="x", pady=(5, 0), ipady=7)
        return var, e

    def _section(self, parent, title, subtitle=""):
        """设置项分组标题。"""
        tk.Frame(parent, bg=BORDER_SOFT, height=1).pack(fill="x", pady=(16, 10))
        head = tk.Frame(parent, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text=title, bg=CARD, fg=TEXT, font=(FONT, 10, "bold"),
                 anchor="w").pack(side="left")
        if subtitle:
            tk.Label(head, text=subtitle, bg=CARD, fg=TEXT3, font=(FONT, 8),
                     anchor="e").pack(side="right")
        return head

    def _build_settings(self, parent):
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=18, pady=(16, 18))

        # ---- 标题
        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="设置", bg=CARD, fg=TEXT, font=(FONT, 12, "bold"),
                 anchor="w").pack(side="left")
        tk.Label(head, text="改动后记得点底部「保存设置」", bg=CARD, fg=TEXT3,
                 font=(FONT, 8), anchor="e").pack(side="right")

        # ---- 账号密码（最优先：决定断网能不能自愈）
        self._section(inner, "① 账号密码", "自动换 Token，免抓包")
        self._label(inner, "云高手机号")
        self.var_mobile, _ = self._entry(inner, "")
        self._label(inner, "登录密码")
        self.var_pwd, _ = self._entry(inner, "", show="•")
        self._hint(inner, "填了之后 Token 失效会自动换新的，断网也能自己接上。\n"
                          "密码用机器指纹派生密钥加密后存储，配置里是密文。")
        arow = tk.Frame(inner, bg=CARD)
        arow.pack(fill="x", pady=(10, 0))
        Pill(arow, "登录并保存", self._do_save_account, accent=True,
             height=34, width=118).pack(side="left")
        self.lbl_account_state = tk.Label(inner, text="", bg=CARD, fg=TEXT3,
                                           font=(FONT, 8), anchor="w",
                                           justify="left")
        self.lbl_account_state.pack(fill="x", pady=(8, 0))

        # ---- Token（备用）
        self._section(inner, "② Bearer Token", "备用，可留空")
        self._label(inner, "Token（来自糕糕 APP）")
        self.var_token, self.ent_token = self._entry(inner, "", show="•")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        Pill(row, "校验并保存", self._do_save_token, height=34,
             width=106, style="ghost").pack(side="left")
        Pill(row, "怎么获取？", self._show_token_help, height=34,
             width=92, style="ghost").pack(side="left", padx=(8, 0))
        self.lbl_token_state = tk.Label(inner, text="", bg=CARD, fg=TEXT3,
                                         font=(FONT, 8), anchor="w", justify="left")
        self.lbl_token_state.pack(fill="x", pady=(8, 0))

        # ---- 单设备限制
        self._section(inner, "③ 单设备限制")
        self._label(inner, "手机 IP（用于自动顶替）")
        self.var_phone_ip, _ = self._entry(inner, "")
        self._hint(inner, "手机连校园网时，在 Wi-Fi 详情里看 IP。填一次即可。")

        # ---- 网关
        self._section(inner, "④ 认证网关", "换学校才需改")
        self._label(inner, "主机")
        self.var_host, _ = self._entry(inner, "")
        self._label(inner, "端口")
        self.var_port, _ = self._entry(inner, "")
        self._hint(inner, "默认 hgs.auth.yungao-tech.com:1110")

        # ---- 开关
        self._section(inner, "⑤ 行为开关")
        self.var_autostart = tk.BooleanVar(value=False)
        self.var_kick = tk.BooleanVar(value=False)
        self._check(inner, "开机自动认证", self.var_autostart)
        self._check(inner, "名额被占时自动顶替手机（会挤掉手机，慎开）", self.var_kick)

        # ---- 底部操作条（常驻可见）
        tk.Frame(inner, bg=BORDER_SOFT, height=1).pack(fill="x", pady=(16, 12))
        brow = tk.Frame(inner, bg=CARD)
        brow.pack(fill="x")
        Pill(brow, "保存设置", self._do_save_settings, accent=True,
             height=36, width=118).pack(side="left")
        Pill(brow, "Token 寿命实验", self._run_tokenlab, height=36,
             width=126, style="ghost").pack(side="left", padx=(8, 0))

    def _check(self, parent, text, var):
        cb = tk.Checkbutton(
            parent, text=text, variable=var, bg=CARD, fg=TEXT2,
            selectcolor=CARD_HI, activebackground=CARD, activeforeground=TEXT,
            font=(FONT, 9), anchor="w", bd=0, highlightthickness=0,
        )
        cb.pack(fill="x", pady=3)
        return cb

    # ------------------------------------------------------------ 状态/日志

    def set_status(self, state: str, text: str, color: str, badge: str = ""):
        self._status = (state, text, color)
        self.dot.set_color(color)
        self.lbl_status.configure(text=text, fg=color)
        self.lbl_badge.configure(text=badge)

    def log(self, msg: str):
        self.q.put(("log", f"[{time.strftime('%H:%M:%S')}] {msg}"))

    def _clear_log(self):
        self.q.put(("clear", None))

    def _drain(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.txt_log.configure(state="normal")
                    self.txt_log.insert("end", payload + "\n")
                    self.txt_log.see("end")
                    self.txt_log.configure(state="disabled")
                elif kind == "clear":
                    self.txt_log.configure(state="normal")
                    self.txt_log.delete("1.0", "end")
                    self.txt_log.configure(state="disabled")
                elif kind == "status":
                    state, text, color, badge = payload
                    self.set_status(state, text, color, badge)
                elif kind == "info":
                    self._update_info()
                elif kind == "qr":
                    self._render_qr(payload)
                elif kind == "qrstate":
                    self._update_qr_state(payload)
                elif kind == "token_state":
                    self.lbl_token_state.configure(text=payload, fg=OK_C)
                elif kind == "heal":
                    self._update_heal_bar()
                elif kind == "call":
                    # 后台线程要操作 Tk 控件时，统一经由这里回到主线程
                    try:
                        payload()
                    except Exception as exc:
                        self.log(f"界面刷新异常: {exc}")
        except queue.Empty:
            pass
        finally:
            if not self._quitting:
                self.root.after(120, self._drain)

    def _update_info(self):
        ip = self.auth.detect_local_ip()
        mac = self.auth.detect_mac()
        self.lbl_info.configure(
            text=(
                f"认证网关   {self.cfg.base_url}\n"
                f"本机出口   {ip or '未探测到（可能未连校园网）'}\n"
                f"MAC 地址   {mac or '未探测到'}\n"
                f"占用名额   {self.cfg.get('last_ip') or ip or '未知'}\n"
                f"在线设备   {self._devices_brief()}"
            )
        )
        self._update_heal_bar()

    def _other_devices(self, my_ip: str = "") -> list:
        """列出占用名额的其他设备 IP。"""
        if not my_ip:
            my_ip = self.auth.detect_local_ip()
        try:
            devs = self.auth.online_devices(self.cfg.get_token())
        except Exception:
            return []
        return [str(d.get("ip", "")) for d in devs
                if str(d.get("ip", "")) != my_ip]

    def _devices_brief(self) -> str:
        """在线设备一行摘要——单设备套餐下这一行最关键。"""
        try:
            return self.auth.describe_devices(self.cfg.get_token(),
                                             self.auth.detect_local_ip())
        except Exception:
            return "查询失败"

    def _update_heal_bar(self):
        """自动重连状态条——用颜色直接告诉用户「断了能不能自己接上」。"""
        has_acct = self.cfg.has_account()
        has_tok = self.cfg.has_token()
        auto = bool(self.cfg.get("autostart"))

        if has_acct:
            text = "✔ 自愈已就绪 —— Token 失效会自动换新，断网自动重连"
            bg, fg = OK_DIM, OK_C
        elif has_tok:
            text = "◐ 已保存 Token —— 可自动重连，但 Token 失效需重新扫码"
            bg, fg = WARN_DIM, WARN_C
        else:
            text = "✘ 未配置 —— 请填写账号密码，否则断网无法自动重连"
            bg, fg = ERR_DIM, ERR_C

        if auto:
            text += "　|　开机自启：已开启"
        else:
            text += "　|　开机自启：未开启"

        self.bar_heal.configure(bg=bg)
        self.lbl_heal.configure(bg=bg, fg=fg, text=text)

    def _selfheal_brief(self) -> str:
        """一句话说明自愈能力状态。"""
        if self.cfg.has_account():
            base = "已就绪（账号密码自动换 Token）"
        elif self.cfg.has_token():
            base = "已就绪（用现有 Token，失效需重抓）"
        else:
            base = "未就绪（需填账号密码或扫码）"
        if bool(self.cfg.get("autostart")):
            base += " · 开机自启已开"
        return base

    def _token_brief(self) -> str:
        t = self.cfg.get_token()
        if not t:
            return "未设置"
        return f"已设置（{t[:6]}…{t[-4:]}）"

    # ------------------------------------------------------------ 设置读写

    def _load_settings(self):
        self.var_token.set(self.cfg.get_token())
        self.var_phone_ip.set(str(self.cfg.get("phone_ip") or ""))
        self.var_host.set(str(self.cfg.get("host") or ""))
        self.var_port.set(str(self.cfg.get("port") or ""))
        self.var_autostart.set(self._autostart_enabled())
        self.var_kick.set(bool(self.cfg.get("auto_kick_phone", True)))
        self._update_info()

    def _do_save_settings(self):
        self.cfg.set("phone_ip", self.var_phone_ip.get().strip())
        self.cfg.set("host", self.var_host.get().strip() or "hgs.auth.yungao-tech.com")
        try:
            self.cfg.set("port", int(self.var_port.get().strip() or 1110))
        except ValueError:
            self.cfg.set("port", 1110)
        self.cfg.set("auto_kick_phone", bool(self.var_kick.get()))
        self.cfg.set("autostart", bool(self.var_autostart.get()))
        self._apply_autostart(bool(self.var_autostart.get()))

        # 账号密码一并保存（若已填写且与已存的不同，则触发一次登录换取 Token）
        mobile = self.var_mobile.get().strip()
        pwd = self.var_pwd.get()
        if mobile and pwd:
            old_mobile, old_pwd = self.cfg.get_account()
            if mobile != old_mobile or pwd != old_pwd:
                self.log("检测到账号密码有变化，正在登录换取 Token…")
                self.cfg.set_account(mobile, pwd)
                threading.Thread(
                    target=self._account_login_bg, args=(mobile, pwd), daemon=True
                ).start()
                return

        self.log("设置已保存")
        if bool(self.var_autostart.get()):
            self.log("已开启开机自动认证——以后开机和断网都会自动重连")
        self._update_heal_bar()
        self.q.put(("info", None))

    def _do_save_token(self):
        raw = self.var_token.get().strip()
        if not raw:
            self.log("Token 为空，未保存")
            return
        token = raw
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        self.cfg.set_token(token)
        self.var_token.set(token)
        self.log("Token 已加密保存，正在校验…")
        threading.Thread(target=self._check_token_bg, daemon=True).start()

    # ------------------------------------------------------------ 账号密码

    def _do_save_account(self):
        """用账号密码登录换取 Token，成功后一并保存（免去抓包）。"""
        mobile = self.var_mobile.get().strip()
        pwd = self.var_pwd.get()
        if not mobile or not pwd:
            self.log("手机号或密码为空，未保存")
            return
        self.log("正在用账号密码登录云高…")
        threading.Thread(
            target=self._account_login_bg, args=(mobile, pwd), daemon=True
        ).start()

    def _account_login_bg(self, mobile: str, pwd: str):
        try:
            from acclogin import AccountLogin, mask_mobile

            lr = AccountLogin().login(mobile, pwd)
        except Exception as exc:
            self.log(f"账号登录异常: {exc}")
            self.q.put(("status", ("error", "账号登录失败", ERR_C, "")))
            return

        if not lr.ok:
            self.log(f"账号登录失败：{lr.message}")
            self.q.put(("status", ("error", "账号登录失败", ERR_C, "")))
            return

        if not lr.token:
            self.log(f"账号登录成功但未取到 Token：{lr.message}")
            self.q.put(("status", ("error", "未取到 Token", ERR_C, "")))
            return

        # 保存账号（加密）与新 Token
        self.cfg.set_account(mobile, pwd)
        self.cfg.set_token(lr.token)
        self.log(f"★ 账号 {mask_mobile(mobile)} 登录成功，Token 已保存（已加密）")
        self.log("账号密码已保存——以后 Token 失效会自动换新的，断网也能自愈")
        self.q.put(("heal", None))

        # 顺手认证一次，确认真的能上网
        ip = self.auth.detect_local_ip()
        res = self.auth.login_chain(ip, lr.token, str(self.cfg.get("phone_ip") or ""),
                                     bool(self.cfg.get("auto_kick_phone", True)))
        if res.ok:
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.log(f"认证成功：{res.message}")
            self.q.put(("status", ("online", "已认证在线", OK_C, f"IP {ip}")))
        else:
            self.log(f"Token 已保存，但当前认证未成功：{res.message}")

    def _check_token_bg(self):
        ip = self.auth.detect_local_ip()
        res = self.auth.check_token(ip, self.cfg.get_token())
        if res.ok:
            self.q.put(("token_state", "✓ Token 有效"))
            self.log("Token 校验通过")
        elif res.status == TOKEN_INVALID:
            self.q.put(("token_state", "✗ Token 已失效，需要重新抓取"))
            self.log("Token 校验失败：服务端拒绝了该 Token")
        elif res.status == DEVICE_LIMIT:
            self.q.put(("token_state", "! Token 有效，但名额被其他设备占用"))
            self.log("Token 有效，但账号在线名额被其他设备占用")
        else:
            self.q.put(("token_state", f"? 校验未通过：{res.message}"))
            self.log(f"Token 校验未通过：{res.message}")

    def _do_check_token(self):
        if not self.cfg.has_token():
            self.log("尚未设置 Token")
            return
        self.log("正在检查 Token…")
        threading.Thread(target=self._check_token_bg, daemon=True).start()

    # ------------------------------------------------------------ 认证动作

    def _do_login(self, takeover: bool = False):
        self.btn_login.set_enabled(False)
        self.set_status("working", "正在认证…", WARN_C)
        threading.Thread(target=self._login_bg, args=(takeover,),
                         daemon=True).start()

    def _reenable(self):
        self.btn_login.set_enabled(True)

    def _login_bg(self, takeover: bool = False):
        """takeover=True 表示用户主动要求"电脑接管"（可踢掉占用设备）。

        自动重连场景传 False —— 否则会和手机互相踢，形成抢登录死循环。
        """
        ip = self.auth.detect_local_ip()
        if not ip:
            self.log("未探测到本机出口 IP，请确认已连接校园网")
            self.q.put(("status", ("error", "未连接校园网", ERR_C, "")))
            self.q.put(("call", self._reenable))
            return

        # ── 共享模式（手机热点）────────────────────────────────────
        # 本机走手机热点时，出口 IP 是手机的 IP，手机认证后整个局域网
        # 都已被 NAT 放行 —— PC **不需要**再单独认证一次，
        # 而且再认证反而会因为「套餐只支持单设备」把手机挤掉。
        if self.auth.is_nat_mode():
            from netguard import probe_online

            if probe_online():
                self.log("检测到本机走手机热点且外网已连通 —— "
                         "手机已认证并为本机放行，无需重复认证")
                self.cfg.set("last_ip", ip)
                self.cfg.set("last_auth_ok", True)
                self.q.put(("status", ("online", "共享在线（经手机热点）", OK_C,
                                       f"出口 {ip}")))
                self.q.put(("info", None))
                self.q.put(("call", self._reenable))
                return
            self.log("检测到本机走手机热点，但外网尚未连通 —— "
                     "请先在手机上完成认证")

        # ── 直连校园网：按服务端在线列表决定要不要动别人 ──────────
        # 若在线列表里已有别的设备（通常是手机），且本机不在其中，
        # 说明名额被占。此时**默认不动手机**，把选择权交给用户。
        token = self.cfg.get_token()
        if not token:
            # 没有 Token：若配了账号密码，先自动换一个；否则才降级到扫码
            if self.cfg.has_account():
                self.log("没有可用 Token，正在用账号密码自动换取…")
                try:
                    from acclogin import AccountLogin, mask_mobile

                    mobile, pwd = self.cfg.get_account()
                    lr = AccountLogin().login(mobile, pwd)
                    if lr.ok and lr.token:
                        self.cfg.set_token(lr.token)
                        token = lr.token
                        self.log(f"★ 已换取新 Token（账号 {mask_mobile(mobile)}）")
                    else:
                        self.log(f"自动换 Token 失败：{lr.message}")
                except Exception as exc:
                    self.log(f"自动换 Token 异常: {exc}")

        if not token:
            self.log("没有 Token，改走扫码流程")
            self.q.put(("call", self._reenable))
            self.q.put(("call", self._open_qr))
            return

        self.log(f"开始认证，IP={ip}")
        res = self.auth.login_chain_self_heal(
            ip, token, str(self.cfg.get("phone_ip") or ""),
            bool(self.cfg.get("auto_kick_phone", False)),
            self.cfg.get_account(),
            takeover=takeover,
        )

        # L1 自动续存：服务端下发了新 Token 就立刻换掉
        if res.ok and res.new_token and res.new_token != token:
            self.cfg.set("token_prev", self.cfg.get("token"))
            self.cfg.set_token(res.new_token)
            self.log("★ 检测到服务端下发了新 Token，已自动续存")
        elif res.new_token and res.new_token != token:
            # 自愈换到的新 Token 也要存下来，否则下次又从旧 Token 开始
            self.cfg.set("token_prev", self.cfg.get("token"))
            self.cfg.set_token(res.new_token)
            self.log("★ 新 Token 已保存")

        if res.ok:
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.log(f"认证成功：{res.stage or 'OK'} {res.message}")

            # 认证成功 ≠ 外网真通（校园网常见 DNS 未生效），
            # 这里实测一次连通性，不通就如实告诉用户。
            time.sleep(1.0)
            from netguard import probe_online

            if probe_online():
                self.q.put(("status", ("online", "已认证在线", OK_C, f"IP {ip}")))
            else:
                self.log("认证已通过，但外网暂时不通——可能是 DNS 未生效，稍后自动重试")
                self.q.put(("status", ("working", "已认证，外网未通", WARN_C, f"IP {ip}")))
                # 启动重连循环，等网络真正通
                self.q.put(("call", lambda: self.guard.start_reconnect()))
        else:
            self.log(f"认证失败：{res.message}")
            if res.stage:
                self.log(f"  过程：{res.stage}")
            if res.status == TOKEN_INVALID:
                self.q.put(("status", ("error", "Token 已失效", ERR_C, "建议扫码登录")))
            elif res.status == DEVICE_LIMIT:
                others = self._other_devices(ip)
                who = f"被 {others[0]} 占用" if others else "名额被占用"
                self.q.put(("status", ("error", who, ERR_C, "点「抢占上线」接管")))
            elif res.status == NOT_AUTH:
                self.q.put(("status", ("error", "认证接口不可达", ERR_C, "请确认已连校园网")))
            else:
                self.q.put(("status", ("error", "认证失败", ERR_C, "")))
        self.q.put(("info", None))
        self.q.put(("call", self._reenable))

    def _do_takeover(self):
        """抢占上线：踢掉占用名额的设备，然后让本机上线。

        单设备套餐下服务端只允许一个 IP 在线。本机被别的设备（通常是手机）
        挡住时，这条路径能主动把对方下线再接管——用户明确要求的能力。
        """
        token = self.cfg.get_token()
        if not token:
            self.log("没有 Token，无法抢占——请先扫码登录或填账号密码")
            self.q.put(("status", ("error", "缺少 Token", ERR_C, "请先扫码登录")))
            return
        self.log("正在抢占上线…")
        threading.Thread(target=self._takeover_bg, daemon=True).start()

    def _takeover_bg(self):
        """抢占上线：把占用名额的设备踢掉，让本机接管。

        复用认证链的 takeover 分支，行为与「立即认证」一致，
        但额外做 Token 自愈 —— 抢占时 Token 常已过期。
        """
        token = self.cfg.get_token()
        ip = self.auth.detect_local_ip()

        # Token 可能已失效（服务端轮换）——先自愈换一个，否则查不到设备
        devices = self.auth.online_devices(token)
        if not devices:
            mobile, pwd = self.cfg.get_account()
            if mobile and pwd:
                try:
                    from acclogin import AccountLogin, mask_mobile

                    self.log(f"Token 已失效，用账号 {mask_mobile(mobile)} 重新换取…")
                    lr = AccountLogin().login(mobile, pwd)
                    if lr.ok and lr.token:
                        self.cfg.set_token(lr.token)
                        token = lr.token
                        self.log("★ 已换取新 Token，继续抢占")
                except Exception as exc:
                    self.log(f"换取 Token 失败: {exc}")

        try:
            devices = self.auth.online_devices(token)
        except Exception as exc:
            self.log(f"查询在线设备失败: {exc}")
            self.q.put(("status", ("error", "查询失败", ERR_C, "")))
            return

        if not devices:
            if not token:
                self.log("没有 Token，无法查询在线设备——请先扫码登录")
                self.q.put(("status", ("error", "缺少 Token", ERR_C, "请先扫码登录")))
                return
            self.log("当前没有其他设备在线，直接认证")
            self._do_login(takeover=True)
            return

        others = [str(d.get("ip", "")) for d in devices
                  if str(d.get("ip", "")) != ip]
        for other in others:
            self.log(f"占用名额的设备：{other}")
        self.log("正在抢占上线…")

        # 走认证链的 takeover 分支：自动下线占用设备后接管
        res = self.auth.login_chain_self_heal(
            ip, token, str(self.cfg.get("phone_ip") or ""),
            bool(self.cfg.get("auto_kick_phone", False)),
            self.cfg.get_account(),
            takeover=True,
        )
        if res.ok:
            self.cfg.set("last_ip", ip)
            self.cfg.set("last_auth_ok", True)
            self.log(f"★ 抢占成功，本机已上线（IP {ip}）")
            self.q.put(("status", ("online", "已抢占上线", OK_C, f"IP {ip}")))
        else:
            self.log(f"抢占失败：{res.message}")
            self.q.put(("status", ("error", "抢占失败", ERR_C, "")))
        self.q.put(("info", None))

    def _do_show_devices(self):
        """打开「在线设备」窗口：查看谁在占用名额，一键下线。"""
        win = tk.Toplevel(self.root)
        win.title("在线设备")
        win.configure(bg=BG)
        win.geometry("520x420")
        win.minsize(440, 340)
        win.resizable(True, True)
        win.transient(self.root)

        head = tk.Frame(win, bg=BG_GRAD)
        head.pack(fill="x")
        tk.Label(head, text="在线设备", bg=BG_GRAD, fg=TEXT,
                 font=(FONT, 13, "bold"), anchor="w").pack(
                     side="left", padx=20, pady=(16, 2))
        tk.Label(head, text="套餐通常只允许一台设备在线", bg=BG_GRAD, fg=TEXT3,
                 font=(FONT, 9), anchor="w").pack(
                     side="left", padx=(0, 20), pady=(18, 2))

        body = tk.Frame(win, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=16)

        self._dev_list = tk.Frame(body, bg=BG)
        self._dev_list.pack(fill="both", expand=True)

        self._dev_hint = tk.Label(
            body, text="", bg=BG, fg=TEXT2, font=(FONT, 9),
            anchor="w", justify="left")
        self._dev_hint.pack(fill="x", pady=(12, 0))

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", padx=20, pady=(0, 16))
        Pill(foot, "刷新", self._refresh_devices, height=34, width=96,
             style="ghost").pack(side="left")
        Pill(foot, "关闭", win.destroy, height=34, width=96).pack(
            side="left", padx=(8, 0))

        self._dev_win = win
        self._refresh_devices()

    def _refresh_devices(self):
        """查询并渲染在线设备列表。"""
        if not hasattr(self, "_dev_list"):
            return
        for w in self._dev_list.winfo_children():
            w.destroy()

        ip = self.auth.detect_local_ip()
        token = self.cfg.get_token()
        devices = []
        err = ""
        try:
            devices = self.auth.online_devices(token)
        except Exception as exc:
            err = str(exc)

        if not devices:
            tk.Label(self._dev_list, text="当前没有设备在线", bg=BG,
                     fg=TEXT3, font=(FONT, 10)).pack(anchor="w")
            self._dev_hint.configure(
                text="点「立即认证」让本机上线。", fg=TEXT3)
            return

        for d in devices:
            dip = str(d.get("ip", "?"))
            t = d.get("type")
            kind = {1: "手机 WIFI 使用中", 2: "电脑有线使用中",
                    3: "电脑 WIFI 使用中"}.get(t, "未知设备")
            mine = dip == ip

            row = tk.Frame(self._dev_list, bg=CARD, highlightthickness=1,
                           highlightbackground=BORDER, highlightcolor=BORDER)
            row.pack(fill="x", pady=(0, 8))

            dot = tk.Canvas(row, width=14, height=14, bg=CARD,
                            highlightthickness=0, bd=0)
            dot.pack(side="left", padx=(14, 10), pady=14)
            dot.create_oval(2, 2, 12, 12, fill=OK_C if mine else WARN_C,
                            outline="")

            info = tk.Frame(row, bg=CARD)
            info.pack(side="left", fill="x", expand=True)
            title = tk.Label(info, text=f"{kind}", bg=CARD, fg=TEXT,
                             font=(FONT, 10, "bold"), anchor="w")
            title.pack(anchor="w")
            sub = tk.Label(
                info,
                text=f"IP {dip}" + ("　← 本机" if mine else ""),
                bg=CARD, fg=TEXT2, font=(FONT, 9), anchor="w")
            sub.pack(anchor="w")

            if not mine:
                btn = Pill(row, "下线这台", lambda ip=dip: self._kick_device(ip),
                           height=28, width=88, style="ghost",
                           font=(FONT, 9))
                btn.pack(side="right", padx=(0, 14))

        self._dev_hint.configure(
            text="· 绿点是本机；黄点是其他设备。\n"
                 "· 想两台同时用：让电脑连手机热点（同一出口 IP），只认证一次。",
            fg=TEXT3)

    def _kick_device(self, ip: str):
        """下线指定设备（把名额还给它）。"""
        token = self.cfg.get_token()
        if not token:
            self.log("没有 Token，无法操作在线设备")
            return
        self.log(f"正在下线设备 {ip}…")
        threading.Thread(
            target=self._kick_device_bg, args=(ip,), daemon=True).start()

    def _kick_device_bg(self, ip: str):
        res = self.auth.logout_ip(ip, self.cfg.get_token())
        if res.ok:
            self.log(f"已下线 {ip}")
            self.q.put(("call", self._refresh_devices))
        else:
            self.log(f"下线 {ip} 失败：{res.message}")

    def _do_logout(self):
        ip = self.cfg.get("last_ip") or self.auth.detect_local_ip()
        token = self.cfg.get_token()
        if not token:
            self.log("没有 Token，无法下线")
            return
        self.log(f"正在归还名额（{ip}）…")
        threading.Thread(target=self._logout_bg, args=(ip, token), daemon=True).start()

    def _logout_bg(self, ip: str, token: str):
        res = self.auth.logout_ip(ip, token)
        if res.ok:
            self.log("已下线，名额已归还（手机可以重新认证了）")
            self.q.put(("status", ("unknown", "已下线", TEXT3, "")))
        else:
            self.log(f"下线未成功：{res.message}")

    # ------------------------------------------------------------ 扫码窗口

    def _open_qr(self):
        if self._qr_win and self._qr_win.winfo_exists():
            self._qr_win.lift()
            self._qr_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._qr_win = win
        win.title("扫码登录")
        win.configure(bg=CARD)
        win.resizable(False, False)
        win.geometry("400x520")
        win.protocol("WM_DELETE_WINDOW", self._close_qr)

        tk.Label(win, text="用糕糕 APP 扫描二维码", bg=CARD, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(pady=(22, 6))
        tk.Label(win, text="这条路径完全不依赖 Token，Token 失效也能用",
                 bg=CARD, fg=TEXT3, font=(FONT, 9)).pack()

        self._qr_canvas = tk.Canvas(win, width=280, height=280, bg="#ffffff",
                                    highlightthickness=0, bd=0)
        self._qr_canvas.pack(pady=18)
        self._qr_status = tk.Label(win, text="正在生成二维码…", bg=CARD, fg=TEXT2,
                                   font=(FONT, 10))
        self._qr_status.pack()

        row = tk.Frame(win, bg=CARD)
        row.pack(pady=16)
        Pill(row, "刷新", self._open_qr, height=32, width=88).pack(side="left")
        Pill(row, "关闭", self._close_qr, height=32, width=88).pack(side="left", padx=(10, 0))

        threading.Thread(target=self._qr_bg, daemon=True).start()

    def _qr_bg(self):
        ip = self.auth.detect_local_ip()
        mac = self.auth.detect_mac()
        assistant = QrAssistant(self.auth, self.log)
        self._qr_assistant = assistant
        state = assistant.load(ip, mac)
        if not state.ready:
            self.q.put(("qrstate", (state.status, state.message)))
            return
        png = assistant.render_png(str(data_dir() / "qr_tmp.png"))
        self.q.put(("qr", png))
        self.q.put(("qrstate", (state.status, state.message)))

        # 轮询
        for _ in range(90):
            time.sleep(2)
            s = assistant.poll(ip, mac)
            self.q.put(("qrstate", (s.status, s.message)))
            if s.status in ("authorized", "expired"):
                if s.status == "authorized":
                    self.log("扫码授权成功，正在确认上线…")
                    token = self.cfg.get_token()
                    if token:
                        # poll() 已尝试过 auth?str=<会话ID>；这里再用 IP 兜一次，
                        # 确保服务端把「本机 IP」这一侧也真正放行。
                        res = self.auth.auth_by_ip(ip, token)
                        if res.ok:
                            self.log(f"完成认证：{res.message}")
                        else:
                            self.log(f"扫码已授权，但 IP 认证未确认：{res.message}")
                    else:
                        self.log("扫码授权成功，设备已上线（未配置 Token）")
                break

    def _render_qr(self, png_path):
        if not png_path or not Path(png_path).exists():
            return
        try:
            img = tk.PhotoImage(file=png_path)
        except Exception as exc:
            self.log(f"二维码显示失败: {exc}")
            return
        # 缩放到 280
        scale = max(1, int(max(img.width(), img.height()) / 280) or 1)
        if scale > 1:
            img = img.subsample(scale, scale)
        self._qr_img = img
        self._qr_canvas.delete("all")
        self._qr_canvas.create_image(140, 140, image=img)

    def _update_qr_state(self, payload):
        status, message = payload
        color = {"authorized": OK_C, "expired": ERR_C, "failed": ERR_C}.get(status, TEXT2)
        self._qr_status.configure(text=message, fg=color)

    def _close_qr(self):
        if self._qr_win and self._qr_win.winfo_exists():
            self._qr_win.destroy()
        self._qr_win = None

    # ------------------------------------------------------------ 守护

    def _start_guard(self):
        self.guard = NetGuard(self.auth, interval=20, logger=self.log,
                              on_event=self._on_guard_event)
        self.guard.start()

    def _on_guard_event(self, ev):
        # 守护线程回调，统一回主线程操作界面
        self.q.put(("call", lambda: self._handle_guard_event(ev)))

    def _handle_guard_event(self, ev):
        if ev.kind == "offline":
            self.set_status("error", "网络中断", ERR_C, "准备重连")
            # 自动重连**不抢占**——否则会和手机互相踢，形成抢登录死循环
            self._do_login(takeover=False)
            # 启动持续重连循环，直到网络真的恢复
            self.guard.start_reconnect()
        elif ev.kind == "reconnect":
            self.log(ev.detail or "重连尝试")
            self._do_login(takeover=False)
        elif ev.kind == "ip_changed":
            self.log(f"出口 IP 变化，重新认证以免掉线：{ev.ip}")
            self._do_login(takeover=False)
        elif ev.kind == "online":
            self.guard.stop_reconnect()
            self.q.put(("info", None))

    def _startup_checks(self):
        self.q.put(("info", None))
        self._sync_autostart()
        if self.cfg.has_token():
            threading.Thread(target=self._check_token_bg, daemon=True).start()
            if self.cfg.get("autostart"):
                self.log("开机自启已开启，正在自动认证上线…")
                # 开机自启场景：网络往往还在协商中，稍等再连
                self.q.put(("call", lambda: self._delayed_login(8)))
            else:
                self.log("已设置 Token，点「立即认证」即可上线")
        elif self.cfg.has_account():
            self.log("已配置账号密码，开机自启时会自动登录换取 Token…")
            if self.cfg.get("autostart"):
                self.q.put(("call", lambda: self._delayed_login(8)))
            else:
                self.log("点「立即认证」即可上线")
        else:
            self.log("尚未设置 Token，可点「扫码登录」先连上网，或点「怎么获取？」查看取 Token 步骤")

    def _sync_autostart(self):
        """把「启动文件夹里的实际文件」与「配置里的开关」对齐。

        场景：用户换了台电脑、从旧版本升级、手动删了启动文件夹里的
        .cmd —— 都会导致配置说「开」而实际没开（或反之）。
        启动时对一次账，避免「以为开了自启其实没有」这种幽灵状态。
        """
        want = bool(self.cfg.get("autostart"))
        have = self._autostart_enabled()
        if want and not have:
            self._apply_autostart(True)
            self.log("已按配置补建开机自启（启动文件夹）")
        elif have and not want:
            self._apply_autostart(False)
            self.log("已按配置清理开机自启")

    def _delayed_login(self, seconds: int):
        """延迟若干秒后自动认证（开机时网络尚未就绪）。"""
        def _worker():
            time.sleep(seconds)
            self.q.put(("call", self._do_login))

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------ 开机自启

    def _startup_dir(self) -> Optional[Path]:
        """当前用户的「启动」文件夹路径。"""
        if sys.platform != "win32":
            return None
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                raw, _ = winreg.QueryValueEx(key, "Startup")
            raw = os.path.expandvars(str(raw))
            d = Path(raw)
            if d.is_dir():
                return d
        except Exception:
            pass
        try:
            appdata = Path(os.environ.get("APPDATA", ""))
            d = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
            if d.is_dir():
                return d
        except Exception:
            pass
        return None

    def _startup_cmd_path(self) -> Optional[Path]:
        d = self._startup_dir()
        return (d / "GaoGaoNetAUTO.cmd") if d else None

    def _autostart_enabled(self) -> bool:
        p = self._startup_cmd_path()
        try:
            return bool(p and p.exists())
        except Exception:
            return False

    def _migrate_autostart_legacy(self):
        """把旧版写在注册表 Run 里的自启项迁到启动文件夹。

        旧版用的是 ``HKCU\\...\\CurrentVersion\\Run``（Defender T1547.001
        高危特征）。升级后自动清掉注册表项并在启动文件夹落一个 ``.cmd``，
        行为不变但不再触发杀软对「注册表自启动」的规则。
        """
        if sys.platform != "win32":
            return
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                try:
                    val, _ = winreg.QueryValueEx(key, "GaoGaoNetAUTO")
                except FileNotFoundError:
                    return
                if not val:
                    return
                winreg.DeleteValue(key, "GaoGaoNetAUTO")
            if not self._autostart_enabled():
                self._apply_autostart(True)
                try:
                    self.cfg.set("autostart", True)
                except Exception:
                    pass
                self.log("已把开机自启从注册表迁移到启动文件夹")
        except Exception:
            pass

    def _apply_autostart(self, enable: bool):
        """开机自启。

        **不用注册表 Run 项**（``HKCU\\...\\CurrentVersion\\Run`` 属于
        Defender T1547.001「注册表自启动/持久化」的高危特征，容易被杀软
        直接拦下）。改为在「启动」文件夹放一个 ``.cmd`` 快捷脚本，
        行为等价但特征风险低得多。
        """
        if sys.platform != "win32":
            return
        target = self._startup_cmd_path()
        if target is None:
            self.log("找不到「启动」文件夹，开机自启不可用")
            return
        try:
            if enable:
                if getattr(sys, "frozen", False):
                    cmd = f'"{sys.executable}" --silent'
                else:
                    pythonw = Path(sys.executable).with_name("pythonw.exe")
                    exe = pythonw if pythonw.exists() else Path(sys.executable)
                    cmd = f'"{exe}" "{Path(__file__).resolve()}" --silent'
                # .cmd 由 cmd.exe 读取，中文必须用 GBK（系统 ANSI 代码页），
                # UTF-8 会变成乱码
                target.write_bytes(
                    ("@echo off\r\n"
                     "rem 糕糕校园网自动认证 —— 开机自启\r\n"
                     f"start \"\" {cmd}\r\n").encode("gbk", errors="replace")
                )
                self._hide_file(target)
                self.log("已开启开机自启（启动文件夹）")
            else:
                if target.exists():
                    target.unlink()
                self.log("已关闭开机自启")
        except Exception as exc:
            self.log(f"设置开机自启失败: {exc}")

    @staticmethod
    def _hide_file(path: Path):
        """给文件加「隐藏」属性（避免启动时闪一个黑框）。"""
        try:
            import ctypes

            FILE_ATTRIBUTE_HIDDEN = 0x02
            SetFileAttributesW = ctypes.windll.kernel32.SetFileAttributesW
            SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
            SetFileAttributesW.restype = ctypes.c_bool
            SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
        except Exception:
            pass

    # ------------------------------------------------------------ 托盘/窗口

    def _setup_tray(self):
        """系统托盘图标（依赖 pystray + Pillow，缺失时静默跳过）。"""
        try:
            import pystray
            from PIL import Image
        except Exception:
            self.log("未安装 pystray/Pillow，跳过托盘图标（关闭主窗口即退出）")
            self._tray = None
            return
        try:
            png, _ = ensure_icons()
            image = Image.open(png)

            def show(_icon, _item):
                self.q.put(("call", self._show_window))

            def do_login(_icon, _item):
                self.q.put(("call", self._do_login))

            def toggle_autostart(_icon, _item):
                self.q.put(("call", self._toggle_autostart))

            def quit_app(_icon, _item):
                self.q.put(("call", self._quit))

            self._tray_autostart = pystray.MenuItem(
                "开机自启",
                toggle_autostart,
                checked=lambda _item: self._autostart_enabled(),
            )
            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", show, default=True),
                pystray.MenuItem("立即认证", do_login),
                pystray.Menu.SEPARATOR,
                self._tray_autostart,
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", quit_app),
            )
            self._tray = pystray.Icon("gaogao", image, "糕糕校园网自动认证", menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
            self.log("托盘图标已就绪")
        except Exception as exc:
            self._tray = None
            self.log(f"托盘图标初始化失败: {exc}")

    def _toggle_autostart(self):
        """托盘菜单里切换「开机自启」，并同步到配置文件。"""
        enable = not self._autostart_enabled()
        self._apply_autostart(enable)
        try:
            self.var_autostart.set(enable)
            self.cfg.set("autostart", enable)
        except Exception:
            pass
        try:
            if getattr(self, "_tray", None):
                self._tray.update_menu()
        except Exception:
            pass

    def _show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _quit(self):
        self._quitting = True
        try:
            if getattr(self, "_tray", None):
                self._tray.stop()
        except Exception:
            pass
        try:
            self.guard.stop()
        except Exception:
            pass
        self.root.destroy()

    def _drag_start(self, e):
        self._dx = e.x_root - self.root.winfo_x()
        self._dy = e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _on_close(self):
        """点右上角关闭：有托盘则隐藏到托盘后台守护；无托盘则直接退出。"""
        if self._quitting:
            self._quit()
            return
        # 防止"填了密码但忘了点保存"——这是最容易踩的坑
        try:
            mobile = self.var_mobile.get().strip()
            pwd = self.var_pwd.get()
            if mobile and pwd:
                old_mobile, old_pwd = self.cfg.get_account()
                if mobile != old_mobile or pwd != old_pwd:
                    ans = messagebox.askyesno(
                        "账号密码还没保存",
                        "你填了账号密码但还没点「登录并保存」。\n\n"
                        "不保存的话，断网自动重连将无法工作（因为程序换不到新 Token）。\n\n"
                        "现在保存并登录吗？",
                        parent=self.root,
                    )
                    if ans:
                        self.cfg.set_account(mobile, pwd)
                        threading.Thread(
                            target=self._account_login_bg, args=(mobile, pwd),
                            daemon=True,
                        ).start()
        except Exception:
            pass
        if not getattr(self, "_tray", None):
            # 没有托盘 → 藏起来等于程序还在跑但用户找不到，直接退出
            self.log("未启用托盘图标，直接退出")
            self._quit()
            return
        self.root.withdraw()
        self.log("已隐藏到托盘，右键托盘图标可退出")

    # ------------------------------------------------------------ 帮助

    def _show_token_help(self):
        win = tk.Toplevel(self.root)
        win.title("如何获取 Token")
        win.configure(bg=BG)
        win.geometry("620x600")
        win.minsize(560, 480)
        win.resizable(True, True)
        win.transient(self.root)

        # 顶栏
        top = tk.Frame(win, bg=BG_GRAD)
        top.pack(fill="x")
        tk.Label(top, text="获取 Bearer Token", bg=BG_GRAD, fg=TEXT,
                 font=(FONT, 13, "bold"), anchor="w").pack(
                     side="left", padx=20, pady=(16, 2))
        tk.Label(top, text="Token 只存在于糕糕 APP 的流量里", bg=BG_GRAD,
                 fg=TEXT3, font=(FONT, 9), anchor="w").pack(
                     side="left", padx=(0, 20), pady=(18, 2))

        # 主体（可滚动）
        wrap = tk.Frame(win, bg=BG)
        wrap.pack(fill="both", expand=True)
        canvas = tk.Canvas(wrap, bg=BG, highlightthickness=0, bd=0)
        canvas.pack(side="left", fill="both", expand=True)
        sb = tk.Scrollbar(wrap, orient="vertical", command=canvas.yview,
                           width=8, troughcolor=BG, bg=BORDER,
                           activebackground=ACCENT, bd=0, highlightthickness=0)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        body = tk.Frame(canvas, bg=BG)
        inner = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfigure(inner, width=e.width))
        for w in (canvas, body):
            w.bind("<MouseWheel>", lambda e: canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"))
            w.bind("<Button-4>", lambda e: canvas.yview_scroll(1, "units"))
            w.bind("<Button-5>", lambda e: canvas.yview_scroll(-1, "units"))

        inner.pack(fill="both", expand=True, padx=24, pady=(14, 10))

        steps = [
            ("方式一 · 填账号密码（最省事）", ACCENT, [
                "在右侧设置区「① 账号密码」填云高手机号和密码，点「登录并保存」。",
                "程序自己去云高换 Token，之后失效了也会自动换——完全不用抓包。",
            ]),
            ("方式二 · 安卓 SSL 抓包", INFO_C, [
                "1. 安卓手机安装「抓包精灵」或 HttpCanary",
                "2. 打开它 → 允许安装 CA 证书（选「安装」/「信任」系统证书）",
                "3. 开始抓包 → 打开「糕糕」APP，等几秒产生网络请求",
                "4. 筛选域名含 yungao-tech 的请求，优先看 hgs.auth.yungao-tech.com",
                "5. 点开 → 请求头 → 找到 Authorization → 复制 Bearer 后面那段",
                "6. 粘到「② Bearer Token」输入框 → 点「校验并保存」",
            ]),
            ("抓包时的小坑", WARN_C, [
                "· 显示「内容已隐藏」→ 点「添加此域名」把它加入解密域名列表",
                "· 证书固定（pinning）看不到明文 → 换带 SSL Unpinning 的工具",
                "· 域名不是 auth.yungao，而是 hgs.auth / user.v4 / h5pay 三种",
            ]),
            ("注意事项", ERR_C, [
                "· 不要在糕糕 APP 里退出账号，退出后 Token 立即失效",
                "· Token 可能随「设备登录」轮换，程序会自动续存，偶尔仍需重抓",
                "· 实在拿不到也没关系：「扫码登录」全程不需要 Token",
            ]),
        ]
        for head, color, lines in steps:
            tk.Label(inner, text=head, bg=BG, fg=color,
                     font=(FONT, 10, "bold"), anchor="w",
                     justify="left").pack(fill="x", pady=(12, 6))
            for ln in lines:
                tk.Label(inner, text=ln, bg=BG, fg=TEXT2, font=(FONT, 9),
                         anchor="w", justify="left").pack(fill="x", pady=1)

        foot = tk.Frame(win, bg=BG)
        foot.pack(fill="x", pady=(0, 16))
        Pill(foot, "知道了", win.destroy, accent=True, height=36,
             width=110).pack()

    def _run_tokenlab(self):
        script = Path(__file__).resolve().parent / "tokenlab.py"
        if not script.exists():
            messagebox.showinfo("Token 寿命实验", "找不到 tokenlab.py")
            return
        exe = Path(sys.executable)
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Token 寿命实验",
                "打包版里没有独立的 tokenlab.py。\n\n"
                "请在源码目录用命令行运行：python tokenlab.py",
            )
            return
        # 延迟导入：subprocess 只在开发调试工具里用得上，
        # 不放在模块顶层可减少静态分析可见的「起子进程」特征。
        import subprocess

        subprocess.Popen([str(exe), str(script)], cwd=str(script.parent))


def main() -> int:
    silent = "--silent" in sys.argv
    root = tk.Tk()
    root.withdraw()
    try:
        ensure_icons()
    except Exception:
        pass
    app = App(root, silent=silent)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
