# -*- coding: utf-8 -*-
"""本地 Web 界面 —— 用浏览器替代 Tk 窗口。

设计要点：

* **只监听 127.0.0.1**，不对外网开放。
* **随机 token 鉴权**：URL 里带一次性 token，所有 API 都要带上。
  防止同机的其它程序/网页（尤其是浏览器里的恶意脚本）随手调用
  你的认证接口，或读到 Token。
* **零第三方依赖**：只用标准库 ``http.server``，不引入 Web 框架。
  打包体积和杀软误报率都不受影响。
* **SSE 推送日志**：实时日志用 Server-Sent Events，比轮询省事。

页面资源在 ``webui/`` 目录（HTML / CSS / JS），通过
``--add-data`` 打进 exe。
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import queue
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from core import (
    STATE_ERROR,
    STATE_IDLE,
    STATE_ONLINE,
    STATE_UNKNOWN,
    STATE_WORKING,
    Controller,
)

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE / "webui"

# 状态 → 颜色（与旧版 Tk 保持同一套语义色）
STATE_COLORS = {
    STATE_ONLINE: "#3ddc97",
    STATE_WORKING: "#f5c451",
    STATE_ERROR: "#ff6b81",
    STATE_IDLE: "#a3aab9",
    STATE_UNKNOWN: "#6b7386",
}


def _web_assets_dir() -> Path:
    """定位网页资源目录（兼容 PyInstaller 打包后的 _MEIPASS）。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", HERE))
        cand = base / "webui"
        if cand.is_dir():
            return cand
    return WEB_DIR


class _EventBus:
    """事件广播 —— SSE 订阅者各自一条队列。"""

    def __init__(self):
        self._subs: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=1000)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    def publish(self, event: dict):
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(event)
            except queue.Full:
                pass  # 慢客户端，丢弃而不是拖住业务线程


class WebApp:
    """把 Controller 包装成 HTTP 服务。"""

    def __init__(self, controller: Optional[Controller] = None,
                 port: int = 0, host: str = "127.0.0.1"):
        self.controller = controller or Controller()
        self.host = host
        self.port = port
        # 一次性 token：URL 里带，凭它才能访问
        self.token = secrets.token_urlsafe(16)
        self.bus = _EventBus()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # 把 Controller 的事件转发到 SSE 总线
        self.controller.emit = self._emit  # type: ignore[method-assign]

    # ------------------------------------------------------------ 事件

    def _emit(self, kind: str, **payload):
        ev = {"kind": kind}
        ev.update(payload)
        self.bus.publish(ev)

    def log(self, msg: str):
        """Controller 的日志出口（同时落盘）。"""
        try:
            import logs

            logs.info(msg)
        except Exception:
            pass
        self.bus.publish({"kind": "log",
                          "text": f"[{time.strftime('%H:%M:%S')}] {msg}"})

    # ------------------------------------------------------------ 服务

    def start(self) -> int:
        """启动服务，返回实际端口。"""
        handler = _make_handler(self)
        try:
            self._httpd = _QuietServer((self.host, self.port), handler)
        except OSError:
            # 端口被占用就换一个
            self._httpd = _QuietServer((self.host, 0), handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        kwargs={"poll_interval": 0.3},
                                        daemon=True)
        self._thread.start()
        return self.port

    def stop(self):
        self._stop.set()
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            except Exception:
                pass

    def url(self) -> str:
        return f"http://{self.host}:{self.port}/?t={self.token}"

    def write_url_file(self) -> Optional[Path]:
        """把访问地址写进数据目录，供「找不到页面时怎么办」用。

        打包版是 ``--windowed``（没有控制台），用户看不到启动时打印的地址。
        这个文件里含令牌，所以：
        * 放在配置目录里（目录 ACL 已收紧到仅当前用户可读）
        * 权限再收一次（0600 语义：仅当前用户）
        """
        try:
            from config import data_dir

            d = data_dir()
            d.mkdir(parents=True, exist_ok=True)
            p = d / "webui.url"
            p.write_text(self.url() + "\n", encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except Exception:
                pass
            return p
        except Exception:
            return None

    def open_browser(self) -> bool:
        """用默认浏览器打开界面。"""
        try:
            return webbrowser.open(self.url())
        except Exception:
            return False

    def start_tray(self):
        """起一个托盘图标（打包版没有控制台，托盘是唯一的入口）。

        菜单：打开界面 / 立即认证 / 退出。
        托盘不可用（缺 pystray/Pillow）时静默跳过。
        """
        try:
            import pystray
            from PIL import Image
        except Exception:
            return None

        try:
            from icons import ensure_icons
            ensure_icons()
            from icons import bundled_assets_dir

            icon_path = bundled_assets_dir() / "icon.ico"
        except Exception:
            icon_path = None

        def _open(_icon, _item):
            self.open_browser()

        def _login(_icon, _item):
            self.controller.login(takeover=False)

        def _quit(_icon, _item):
            self.stop()
            try:
                _icon.stop()
            except Exception:
                pass

        try:
            img = None
            if icon_path and icon_path.exists():
                try:
                    img = Image.open(icon_path)
                except Exception:
                    img = None
            if img is None:
                img = Image.new("RGB", (64, 64), (251, 114, 153))
            menu = pystray.Menu(
                pystray.MenuItem("打开界面", _open, default=True),
                pystray.MenuItem("立即认证", _login),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("退出", _quit),
            )
            icon = pystray.Icon("gaogao", img, "糕糕校园网自动认证", menu)
            threading.Thread(target=icon.run, daemon=True).start()
            return icon
        except Exception:
            return None

    def serve_forever(self):
        """阻塞运行（Ctrl+C 退出）。"""
        try:
            while not self._stop.is_set():
                time.sleep(0.3)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


# ---------------------------------------------------------------- HTTP

class _QuietServer(ThreadingHTTPServer):
    """不往 stderr 刷异常 traceback 的 HTTP 服务器。

    标准库默认的 ``handle_error`` 会把每个连接异常都打成一整段
    traceback。浏览器刷新、切换标签、SSE 长连接被回收 —— 这些
    都是**正常行为**，不该让用户看到「程序出错」。

    真正的意外异常也不能吞掉 —— 记进日志文件，便于排查。
    """

    daemon_threads = True
    allow_reuse_address = True
    # 关服务时**不等**残留的处理线程。
    # 默认值 True 会让 server_close() 去 join 所有 handler 线程 ——
    # 而 SSE 长连接线程、被浏览器挂着的 keep-alive 连接都要等好几秒，
    # 表现为「点退出后程序卡住不关」。
    block_on_close = False

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError,
                            BrokenPipeError, TimeoutError)):
            return  # 对端断开，正常
        try:
            import logs

            logs.warn(f"Web 服务异常：{exc}")
        except Exception:
            pass


def _make_handler(app: WebApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GaoGaoWebUI"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        # ---------------------------------------------------------- 工具

        def handle_one_request(self):
            """把「连接被对端断开」当正常情况，别刷 traceback。

            浏览器刷新页面、切换标签、SSE 长连接被回收，都会让服务端
            读到半截请求。标准库默认会往 stderr 打一整段
            ``Exception occurred during processing of request``，
            打包版虽然没有控制台，但这些噪音会混进日志文件，
            也会让用户误以为程序出错。
            """
            try:
                super().handle_one_request()
            except (ConnectionResetError, ConnectionAbortedError,
                    BrokenPipeError, TimeoutError):
                self.close_connection = True
            except OSError:
                self.close_connection = True

        def log_message(self, fmt, *args):  # noqa: A003
            """不往 stderr 刷访问日志（会污染控制台和日志文件）。"""
            return

        def log_error(self, fmt, *args):
            """同样不往 stderr 写 —— 标准库在这里会直接 write(2)。

            残留连接、半截请求都会触发 log_error；写 stderr 在多线程下
            还会和日志重定向/测试捕获抢锁，进而把退出流程卡住。
            """
            return

        def _authorized(self) -> bool:
            """校验 token。

            允许两种携带方式：URL 查询参数（首屏）或 ``X-Auth-Token`` 头
            （后续 AJAX 请求）。
            """
            header = self.headers.get("X-Auth-Token", "")
            if header and secrets.compare_digest(header, app.token):
                return True
            qs = parse_qs(urlparse(self.path).query)
            t = (qs.get("t") or [""])[0]
            if t and secrets.compare_digest(t, app.token):
                return True
            # 页面里注入的 token（供 fetch 用）
            cookie = self.headers.get("Cookie", "")
            if f"gg_token={app.token}" in cookie:
                return True
            return False

        def _json(self, data, status: int = 200):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, msg: str, status: int = 400):
            self._json({"ok": False, "message": msg}, status)

        def _read_json(self) -> dict:
            """读请求体（上限 1 MB）。

            ⚠️ 调用点必须在鉴权**之前**：如果先判 401 再读 body，
            请求体留在 socket 里，keep-alive 连接上的下一个请求会被
            这些残留字节污染（表现为莫名其妙的 400）。
            """
            try:
                n = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                return {}
            if n <= 0:
                return {}
            if n > 1024 * 1024:
                return {}
            try:
                raw = self.rfile.read(n)
                return json.loads(raw.decode("utf-8"))
            except Exception:
                return {}

        def _redirect(self, url: str):
            self.send_response(302)
            self.send_header("Location", url)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _serve_file(self, name: str):
            """输出静态资源。"""
            base = _web_assets_dir()
            # 防目录穿越
            safe = os.path.normpath(name).lstrip("./\\")
            path = (base / safe).resolve()
            try:
                path.relative_to(base.resolve())
            except ValueError:
                self._error("非法路径", 403)
                return
            if not path.is_file():
                self._error("找不到资源", 404)
                return
            data = path.read_bytes()
            ctype, _ = mimetypes.guess_type(str(path))
            if ctype is None:
                ctype = "application/octet-stream"
            if ctype.startswith("text/") or ctype in (
                    "application/javascript", "application/json"):
                ctype += "; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        # ---------------------------------------------------------- 路由

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            qs = parse_qs(parsed.query)

            if path == "/":
                # 首屏：校验 token，通过则下发页面并种 cookie
                t = (qs.get("t") or [""])[0]
                if not (t and secrets.compare_digest(t, app.token)):
                    self._error("访问令牌无效 —— 请通过程序启动时给出的链接打开",
                                403)
                    return
                html = (_web_assets_dir() / "index.html").read_bytes()
                self.send_response(200)
                self.send_header(
                    "Set-Cookie",
                    f"gg_token={app.token}; Path=/; SameSite=Strict; HttpOnly")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(html)
                return

            if path == "/api/events":
                self._serve_sse()
                return

            if path == "/api/state":
                if not self._authorized():
                    self._error("未授权", 401)
                    return
                self._json(app.controller.info_snapshot())
                return

            if path == "/api/qr.png":
                # 二维码图片：直接读临时文件
                if not self._authorized():
                    self._error("未授权", 401)
                    return
                from config import data_dir

                p = data_dir() / "qr_tmp.png"
                if not p.exists():
                    self._error("没有二维码", 404)
                    return
                data = p.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return

            # 静态资源
            if path.startswith("/static/"):
                self._serve_file(path[len("/static/"):])
                return

            self._error("没有这个接口", 404)

        def do_POST(self):
            parsed = urlparse(self.path)
            path = parsed.path

            # 先读完请求体再鉴权 —— 见 _read_json 的说明
            data = self._read_json()

            if not self._authorized():
                self._error("未授权", 401)
                return

            c = app.controller

            if path == "/api/login":
                c.login(takeover=bool(data.get("takeover")))
                self._json({"ok": True})
                return

            if path == "/api/takeover-preview":
                self._json(c.takeover_preview())
                return

            if path == "/api/takeover":
                c.takeover()
                self._json({"ok": True})
                return

            if path == "/api/qr":
                c.start_qr()
                self._json({"ok": True})
                return

            if path == "/api/qr/cancel":
                c.cancel_qr()
                self._json({"ok": True})
                return

            if path == "/api/devices":
                c.refresh_devices()
                self._json({"ok": True})
                return

            if path == "/api/device/kick":
                ip = str(data.get("ip", "") or "")
                if not ip:
                    self._error("缺少 IP", 400)
                    return
                c.kick_device(ip)
                self._json({"ok": True})
                return

            if path == "/api/logout":
                c.logout_self()
                self._json({"ok": True})
                return

            if path == "/api/token/check":
                c.check_token()
                self._json({"ok": True})
                return

            if path == "/api/token/save":
                ok, msg = c.save_token(str(data.get("token", "") or ""))
                if ok:
                    c.emit("token_state", text="已保存，正在校验…")
                    c.check_token()
                self._json({"ok": ok, "message": msg})
                return

            if path == "/api/settings/save":
                ok, msg = c.save_settings(data)
                if ok:
                    c.apply_autostart(bool(data.get("autostart")))
                    c.emit("info")
                self._json({"ok": ok, "message": msg})
                return

            if path == "/api/settings/clear":
                ok, msg = c.clear_credentials()
                if ok:
                    c.emit("info")
                self._json({"ok": ok, "message": msg})
                return

            if path == "/api/account/save":
                mobile = str(data.get("mobile", "") or "").strip()
                pwd = str(data.get("password", "") or "")
                if not mobile or not pwd:
                    self._error("手机号或密码为空", 400)
                    return
                c.login_with_account(mobile, pwd)
                self._json({"ok": True})
                return

            if path == "/api/refresh":
                c.refresh_info()
                self._json({"ok": True})
                return

            self._error("没有这个接口", 404)

        # ---------------------------------------------------------- SSE

        def _serve_sse(self):
            """SSE 长连接：持续推送事件。"""
            if not self._authorized():
                self._error("未授权", 401)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            sub = app.bus.subscribe()
            try:
                # 先推一次当前状态
                self._sse_write({"kind": "state",
                                 "state": app.controller.info_snapshot()})
                while not app._stop.is_set():
                    try:
                        ev = sub.get(timeout=5.0)
                    except queue.Empty:
                        # 心跳，防止代理掐断连接
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    self._sse_write(ev)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # 客户端关掉了页面
            finally:
                app.bus.unsubscribe(sub)

        def _sse_write(self, ev: dict):
            data = json.dumps(ev, ensure_ascii=False)
            self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
            self.wfile.flush()

    return Handler


def pick_port(preferred: int = 8765) -> int:
    """挑一个可用端口（优先 preferred，被占就随机）。"""
    for port in (preferred, 0):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
        except OSError:
            continue
    return 0


def main() -> int:
    """命令行入口：起服务并打开浏览器。"""
    app = WebApp()
    port = app.start()
    url = app.url()
    print("=" * 62)
    print(" 糕糕校园网自动认证 · Web 界面已启动")
    print("=" * 62)
    print(f"  地址：{url}")
    print("  （这个地址带一次性令牌，请勿分享）")
    print("  关闭本窗口即退出程序")
    print("=" * 62)
    app.open_browser()
    app.serve_forever()
    return 0
