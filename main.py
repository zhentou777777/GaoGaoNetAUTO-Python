# -*- coding: utf-8 -*-
"""糕糕校园网自动认证 —— 桌面主程序。

界面走深色扁平风格（不用 tkinter 默认灰白控件），常驻托盘，
断网 / 出口 IP 变化时自动重新认证。
"""
from __future__ import annotations

import queue
import subprocess
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
from config import Config, app_dir
from icons import ensure_icons
from netguard import NetGuard
from qrflow import QrAssistant

# ---------------------------------------------------------------- 主题

BG = "#14141a"
CARD = "#1c1c25"
CARD_HI = "#23232e"
BORDER = "#2c2c38"
TEXT = "#ecedf2"
TEXT2 = "#9b9baa"
TEXT3 = "#6b6b7b"
ACCENT = "#FB7299"
ACCENT_HI = "#ff8fae"
OK_C = "#4ade80"
WARN_C = "#fbbf24"
ERR_C = "#f87171"

FONT = "Microsoft YaHei UI"
MONO = "Consolas"

WIN_W, WIN_H = 940, 660


def _round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw) -> int:
    """在 Canvas 上画圆角矩形。"""
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1 + r, x2, y2 - r,
        x2 - r, y2, x1 + r, y2, x1, y2 - r, x1, y1 + r,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=24, **kw)


class Pill(tk.Canvas):
    """扁平圆角按钮。"""

    def __init__(
        self,
        master,
        text: str,
        command: Optional[Callable[[], None]] = None,
        accent: bool = False,
        height: int = 38,
        width: int = 150,
        font=(),
    ):
        bg = master.cget("bg")
        super().__init__(
            master, height=height, width=width, bg=bg,
            highlightthickness=0, bd=0, cursor="hand2",
        )
        self._text = text
        self._command = command
        self._accent = accent
        self._font = font or (FONT, 10)
        self._hover = False
        self._enabled = True
        self.bind("<Configure>", lambda e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self._redraw()

    def _colors(self):
        if not self._enabled:
            return CARD_HI, TEXT3
        if self._accent:
            return (ACCENT_HI if self._hover else ACCENT), "#ffffff"
        return (CARD_HI if self._hover else CARD), TEXT

    def _redraw(self):
        self.delete("all")
        w = self.winfo_width() or int(self["width"])
        h = self.winfo_height() or int(self["height"])
        if w <= 1 or h <= 1:
            return
        fill, fg = self._colors()
        _round_rect(self, 1, 1, w - 1, h - 1, min(10, h // 3), fill=fill, outline="")
        self.create_text(
            w / 2, h / 2, text=self._text, fill=fg, font=self._font, anchor="center"
        )

    def _on_enter(self, _e):
        self._hover = True
        self._redraw()

    def _on_leave(self, _e):
        self._hover = False
        self._redraw()

    def _on_click(self, _e):
        if self._enabled and self._command:
            self._command()

    def set_enabled(self, flag: bool):
        self._enabled = flag
        self.configure(cursor="hand2" if flag else "arrow")
        self._redraw()

    def set_text(self, text: str):
        self._text = text
        self._redraw()


class Dot(tk.Canvas):
    """状态指示灯。"""

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
        self.create_oval(2, 2, s - 2, s - 2, fill=self._color, outline="")

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

        self._build()
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
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 顶部标题栏
        header = tk.Frame(root, bg=CARD, height=52)
        header.pack(fill="x")
        header.pack_propagate(False)
        header.bind("<ButtonPress-1>", self._drag_start)
        header.bind("<B1-Motion>", self._drag_move)

        tk.Label(
            header, text="糕糕校园网自动认证", bg=CARD, fg=TEXT,
            font=(FONT, 12, "bold"), anchor="w",
        ).pack(side="left", padx=18)

        self.btn_min = Pill(header, "—", self.root.iconify, height=26, width=34,
                            font=(FONT, 11))
        self.btn_min.pack(side="right", padx=(0, 8), pady=12)
        self.btn_close = Pill(header, "✕", self._on_close, accent=True, height=26, width=34,
                              font=(FONT, 10))
        self.btn_close.pack(side="right", padx=(0, 4), pady=12)

        # 主体
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(14, 16))

        left = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(body, bg=BG, width=330)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        self._build_status(left)
        self._build_actions(left)
        self._build_log(left)
        self._build_settings(right)

    def _build_status(self, parent):
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=20, pady=18)

        top = tk.Frame(inner, bg=CARD)
        top.pack(fill="x")
        self.dot = Dot(top, TEXT3, 16)
        self.dot.pack(side="left", pady=(2, 0))
        self.lbl_status = tk.Label(
            top, text="尚未认证", bg=CARD, fg=TEXT, font=(FONT, 16, "bold"), anchor="w"
        )
        self.lbl_status.pack(side="left", padx=(12, 0))
        self.lbl_badge = tk.Label(
            top, text="", bg=CARD, fg=TEXT3, font=(FONT, 9), anchor="e"
        )
        self.lbl_badge.pack(side="right")

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=(14, 12))

        self.lbl_info = tk.Label(
            inner, text="", bg=CARD, fg=TEXT2, font=(FONT, 9),
            justify="left", anchor="w",
        )
        self.lbl_info.pack(fill="x")

    def _build_actions(self, parent):
        bar = tk.Frame(parent, bg=BG)
        bar.pack(fill="x", pady=(14, 0))
        self.btn_login = Pill(bar, "立即认证", self._do_login, accent=True,
                              height=40, width=132)
        self.btn_login.pack(side="left")
        Pill(bar, "扫码登录", self._open_qr, height=40, width=120).pack(side="left", padx=(10, 0))
        Pill(bar, "归还名额", self._do_logout, height=40, width=110).pack(side="left", padx=(10, 0))
        Pill(bar, "检查 Token", self._do_check_token, height=40, width=118).pack(side="left", padx=(10, 0))

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

    def _entry(self, parent, text="", show=""):
        var = tk.StringVar(value=text)
        e = tk.Entry(
            parent, textvariable=var, bg=CARD_HI, fg=TEXT, insertbackground=TEXT,
            relief="flat", font=(MONO, 9), highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=ACCENT,
            show=show, bd=0,
        )
        e.pack(fill="x", pady=(4, 0), ipady=6)
        return var, e

    def _build_settings(self, parent):
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="both", expand=True, padx=16, pady=16)

        tk.Label(inner, text="设置", bg=CARD, fg=TEXT, font=(FONT, 11, "bold"),
                 anchor="w").pack(fill="x")

        # Token
        self._label(inner, "Bearer Token（来自糕糕 APP）")
        self.var_token, self.ent_token = self._entry(inner, "", show="•")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x", pady=(8, 0))
        Pill(row, "校验并保存", self._do_save_token, accent=True, height=32,
             width=106).pack(side="left")
        Pill(row, "怎么获取？", self._show_token_help, height=32, width=92).pack(side="left", padx=(8, 0))
        self.lbl_token_state = tk.Label(inner, text="", bg=CARD, fg=TEXT3,
                                         font=(FONT, 9), anchor="w", justify="left")
        self.lbl_token_state.pack(fill="x", pady=(8, 0))

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=14)

        # 手机 IP
        self._label(inner, "手机 IP（单设备限制下用于自动顶替）")
        self.var_phone_ip, _ = self._entry(inner, "")
        tk.Label(inner, text="填一次即可；手机连校园网时在手机 Wi-Fi 详情里看 IP",
                 bg=CARD, fg=TEXT3, font=(FONT, 8), anchor="w",
                 justify="left").pack(fill="x", pady=(4, 0))

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=14)

        # 认证网关
        self._label(inner, "认证网关")
        self.var_host, _ = self._entry(inner, "")
        self.var_port, _ = self._entry(inner, "")
        tk.Label(inner, text="默认 hgs.auth.yungao-tech.com:1110（不同学校请改）",
                 bg=CARD, fg=TEXT3, font=(FONT, 8), anchor="w").pack(fill="x", pady=(4, 0))

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=14)

        # 账号密码（用于 Token 失效时自动换新 Token）
        self._label(inner, "账号密码（自动续期，可选）")
        self.var_mobile, _ = self._entry(inner, "")
        self.var_pwd, _ = self._entry(inner, "", show="•")
        tk.Label(inner, text="填了就能在 Token 失效时自动换新，彻底免抓包；密码加密存储",
                 bg=CARD, fg=TEXT3, font=(FONT, 8), anchor="w",
                 justify="left").pack(fill="x", pady=(4, 0))
        arow = tk.Frame(inner, bg=CARD)
        arow.pack(fill="x", pady=(8, 0))
        Pill(arow, "登录并保存", self._do_save_account, accent=True,
             height=32, width=100).pack(side="left")

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=14)

        # 开关
        self.var_autostart = tk.BooleanVar(value=False)
        self.var_kick = tk.BooleanVar(value=True)
        self._check(inner, "开机自动认证", self.var_autostart)
        self._check(inner, "名额被占时自动顶替手机", self.var_kick)

        tk.Frame(inner, bg=BORDER, height=1).pack(fill="x", pady=14)

        row2 = tk.Frame(inner, bg=CARD)
        row2.pack(fill="x")
        Pill(row2, "保存设置", self._do_save_settings, height=32, width=100).pack(side="left")
        Pill(row2, "Token 寿命实验", self._run_tokenlab, height=32, width=118).pack(side="left", padx=(8, 0))

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
                f"本机出口   {ip or '未探测到（可能未连校园网）'}      MAC  {mac or '未探测到'}\n"
                f"占用名额   {self.cfg.get('last_ip') or ip or '未知'}      "
                f"Token  {self._token_brief()}"
            )
        )

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
        self.var_autostart.set(bool(self.cfg.get("autostart")))
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
        self.log("设置已保存")
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

    def _do_login(self):
        self.btn_login.set_enabled(False)
        self.set_status("working", "正在认证…", WARN_C)
        threading.Thread(target=self._login_bg, daemon=True).start()

    def _reenable(self):
        self.btn_login.set_enabled(True)

    def _login_bg(self):
        ip = self.auth.detect_local_ip()
        if not ip:
            self.log("未探测到本机出口 IP，请确认已连接校园网")
            self.q.put(("status", ("error", "未连接校园网", ERR_C, "")))
            self.q.put(("call", self._reenable))
            return
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
            bool(self.cfg.get("auto_kick_phone", True)),
            self.cfg.get_account(),
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
                self.q.put(("status", ("error", "名额被占用", ERR_C, "可填手机 IP 自动顶替")))
            elif res.status == NOT_AUTH:
                self.q.put(("status", ("error", "认证接口不可达", ERR_C, "请确认已连校园网")))
            else:
                self.q.put(("status", ("error", "认证失败", ERR_C, "")))
        self.q.put(("info", None))
        self.q.put(("call", self._reenable))

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
        png = assistant.render_png(str(app_dir() / "qr_tmp.png"))
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
            self._do_login()
            # 启动持续重连循环，直到网络真的恢复
            self.guard.start_reconnect()
        elif ev.kind == "reconnect":
            self.log(ev.detail or "重连尝试")
            self._do_login()
        elif ev.kind == "ip_changed":
            self.log(f"出口 IP 变化，重新认证以免掉线：{ev.ip}")
            self._do_login()
        elif ev.kind == "online":
            self.guard.stop_reconnect()
            self.q.put(("info", None))

    def _startup_checks(self):
        self.q.put(("info", None))
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

    def _delayed_login(self, seconds: int):
        """延迟若干秒后自动认证（开机时网络尚未就绪）。"""
        def _worker():
            time.sleep(seconds)
            self.q.put(("call", self._do_login))

        threading.Thread(target=_worker, daemon=True).start()

    # ------------------------------------------------------------ 开机自启

    def _apply_autostart(self, enable: bool):
        if sys.platform != "win32":
            return
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
                if enable:
                    if getattr(sys, "frozen", False):
                        cmd = f'"{sys.executable}" --silent'
                    else:
                        pythonw = Path(sys.executable).with_name("pythonw.exe")
                        exe = pythonw if pythonw.exists() else Path(sys.executable)
                        cmd = f'"{exe}" "{Path(__file__).resolve()}" --silent'
                    winreg.SetValueEx(key, "GaoGaoNetAUTO", 0, winreg.REG_SZ, cmd)
                    self.log("已开启开机自启")
                else:
                    try:
                        winreg.DeleteValue(key, "GaoGaoNetAUTO")
                        self.log("已关闭开机自启")
                    except FileNotFoundError:
                        pass
        except Exception as exc:
            self.log(f"设置开机自启失败: {exc}")

    # ------------------------------------------------------------ 托盘/窗口

    def _setup_tray(self):
        """系统托盘图标（依赖 pystray + Pillow，缺失时静默跳过）。"""
        try:
            import pystray
            from PIL import Image
        except Exception:
            self.log("未安装 pystray/Pillow，跳过托盘图标（关闭主窗口即隐藏）")
            return
        try:
            png, _ = ensure_icons()
            image = Image.open(png)

            def show(_icon, _item):
                self.q.put(("call", self._show_window))

            def do_login(_icon, _item):
                self.q.put(("call", self._do_login))

            def quit_app(_icon, _item):
                self.q.put(("call", self._quit))

            menu = pystray.Menu(
                pystray.MenuItem("显示主窗口", show, default=True),
                pystray.MenuItem("立即认证", do_login),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", quit_app),
            )
            self._tray = pystray.Icon("gaogao", image, "糕糕校园网自动认证", menu)
            threading.Thread(target=self._tray.run, daemon=True).start()
            self.log("托盘图标已就绪")
        except Exception as exc:
            self.log(f"托盘图标初始化失败: {exc}")

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
        """点右上角关闭：隐藏到托盘，后台继续守护。"""
        if self._quitting:
            self._quit()
            return
        self.root.withdraw()
        self.log("已隐藏到托盘，右键托盘图标可退出")

    # ------------------------------------------------------------ 帮助

    def _show_token_help(self):
        win = tk.Toplevel(self.root)
        win.title("如何获取 Token")
        win.configure(bg=CARD)
        win.geometry("560x520")
        win.resizable(False, False)
        tk.Label(win, text="获取 Bearer Token", bg=CARD, fg=TEXT,
                 font=(FONT, 13, "bold")).pack(pady=(20, 4))
        tk.Label(win, text="云高网页登录流程本身不带 Token，Token 只存在于糕糕 APP 的流量里",
                 bg=CARD, fg=TEXT3, font=(FONT, 9)).pack()

        body = tk.Frame(win, bg=CARD)
        body.pack(fill="both", expand=True, padx=26, pady=12)
        steps = [
            ("方式一 · 安卓 SSL 抓包（推荐）", [
                "1. 安卓手机安装「抓包精灵」或 HttpCanary",
                "2. 打开它 → 允许安装 CA 证书（设置里选「安装」/「信任」系统证书）",
                "3. 开始抓包 → 打开「糕糕」APP，等待几秒产生网络请求",
                "4. 在抓包列表里筛选域名含 auth.yungao 的那条请求",
                "5. 点开 → 请求头 → Authorization → 复制 Bearer 后面的那段字符串",
                "6. 粘贴到左边输入框，点「校验并保存」",
            ]),
            ("方式二 · 从浏览器复制（不一定可行）", [
                "网页扫码流程通常不带 Authorization 头，所以这条路多半取不到。",
                "但如果你在浏览器里看到发往 auth.yungao 的请求带 Bearer，就可以直接复制。",
            ]),
            ("注意", [
                "· 不要在 APP 里退出账号，否则 Token 立即失效。",
                "· Token 可能随「设备登录」轮换；脚本会自动续存，但偶尔仍需重抓。",
                "· 实在拿不到也没关系：用「扫码登录」按钮，全程不需要 Token。",
            ]),
        ]
        for head, lines in steps:
            tk.Label(body, text=head, bg=CARD, fg=ACCENT,
                     font=(FONT, 10, "bold"), anchor="w",
                     justify="left").pack(fill="x", pady=(10, 4))
            for ln in lines:
                tk.Label(body, text=ln, bg=CARD, fg=TEXT2, font=(FONT, 9),
                         anchor="w", justify="left").pack(fill="x")

        Pill(win, "知道了", win.destroy, accent=True, height=34, width=100).pack(pady=(0, 20))

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
