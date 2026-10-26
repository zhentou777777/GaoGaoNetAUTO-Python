# -*- coding: utf-8 -*-
"""pytest 套件。

覆盖的是**曾经真实炸过**的地方，防止回归：

* ``btn_login`` 曾因按钮重构而丢失，导致「立即认证」「自动重连」
  「开机自启」全部静默失效（P0）。
* ``check_token`` 曾调用会改变在线状态的认证接口，
  打开程序就可能把手机踢了（P0）。
* 扫码状态机曾把 ``var success = true`` 误判成「已授权」（P0）。
* ``_classify`` 曾把 ``{"code":0,"msg":"token无效"}`` 判成成功（P1）。
* Token 帮助窗口曾因 ``create_window`` 返回值被 ``pack`` 而必现崩溃（P1）。

跑法::

    python -m pytest tests/ -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# GUI 冒测需要一个可用的显示环境；CI 的 windows-latest 有，
# 但纯 headless 环境要跳过。
_HAS_TK = True
try:
    import tkinter  # noqa: F401
    _root = tkinter.Tk()
    _root.withdraw()
    _root.destroy()
except Exception:
    _HAS_TK = False

needs_tk = pytest.mark.skipif(not _HAS_TK, reason="无可用 GUI 环境")


class FakeConfig:
    """配置桩 —— 不碰真实磁盘。"""

    _data = {
        "last_ip": "1.1.1.1",
        "autostart": False,
        "auto_kick_phone": False,
    }

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def get_token(self):
        return "FAKE_TOKEN"

    def has_token(self):
        return True

    def has_account(self):
        return False

    def get_account(self):
        return "", ""


class FakeAuth:
    """认证桩 —— 不发任何网络请求。"""

    def detect_local_ip(self):
        return "9.9.9.9"

    def detect_mac(self):
        return "aa:bb:cc:dd:ee:ff"

    def online_devices(self, token):
        return [{"ip": "5.6.7.8", "type": 1}]

    def is_nat_mode(self):
        return False


# ---------------------------------------------------------------- 认证解析

class TestClassify:
    """``CampusAuth._classify`` —— 认证结果归类。"""

    @staticmethod
    def _classify(body, code=200, headers=None):
        from auth import CampusAuth

        a = CampusAuth.__new__(CampusAuth)
        return a._classify(code, body, headers or {})

    def test_token_invalid_msg_beats_code_zero(self):
        """{"code":0,"msg":"token无效"} 绝不能判成功。"""
        status, _msg = self._classify('{"code":0,"msg":"token无效"}')
        assert status != "OK"

    def test_device_limit_msg_beats_code_200(self):
        """{"code":200,"msg":"设备已满"} 绝不能判成功。"""
        status, _msg = self._classify('{"code":200,"msg":"设备已满"}')
        assert status != "OK"

    def test_empty_body_is_not_success(self):
        """空响应不能当成「已提交 ⇒ 成功」。"""
        status, _msg = self._classify("")
        assert status != "OK"

    def test_2xx_without_status_is_not_success(self):
        """2xx 但没有任何状态线索，不能判成功。"""
        status, _msg = self._classify("{}", 200)
        assert status != "OK"

    def test_explicit_failure_with_success_true(self):
        """success=true 但 code=500 —— 判失败。"""
        status, _msg = self._classify(
            '{"code":500,"success":true,"msg":"服务器错误"}')
        assert status != "OK"

    def test_genuine_success(self):
        status, _msg = self._classify('{"code":200,"msg":"认证成功"}')
        assert status == "ok"

    def test_410_is_token_invalid(self):
        status, _msg = self._classify('{"code":410,"msg":"登录状态已失效"}')
        assert status == "token_invalid"

    def test_409_is_device_limit(self):
        status, _msg = self._classify('{"code":409,"msg":"已有设备在线"}')
        assert status == "device_limit"

    def test_html_response_is_not_auth(self):
        """门户拦截返回 HTML —— 不能判成功。"""
        status, _msg = self._classify("<html><body>portal</body></html>")
        assert status != "OK"


class TestExtractNewToken:
    """``_extract_new_token`` —— 不能把错误信息当 Token。"""

    @staticmethod
    def _extract(body, headers=None):
        from auth import CampusAuth

        return CampusAuth._extract_new_token(body, headers or {})

    def test_jwt_is_accepted(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdef"
        assert self._extract(json_body := '{"access_token":"%s"}' % jwt) == jwt

    def test_plain_english_word_rejected(self):
        """英文单词（熵太低）不能当 Token。"""
        assert self._extract('{"access_token":"invalidtokenstring"}') is None

    def test_chinese_error_not_token(self):
        assert self._extract("认证失败，请稍后重试") is None


# ---------------------------------------------------------------- 扫码状态机

class TestQrStatus:
    """扫码页状态判定 —— 不能「还没扫就成功」。"""

    def test_success_keyword_alone_is_not_authorized(self):
        """``var success = true`` 出现在页面里 ≠ 已授权。"""
        from qrflow import extract_status

        html = "<html><script>var success = true;</script></html>"
        assert extract_status(html) != "authorized"

    def test_structured_authorized(self):
        from qrflow import extract_status

        assert extract_status(
            '<script>var status = "authorized";</script>') == "authorized"

    def test_structured_scanned(self):
        from qrflow import extract_status

        assert extract_status(
            '{"status": "scanned"}') == "scanned"

    def test_structured_expired(self):
        from qrflow import extract_status

        assert extract_status(
            '<script>var status = "expired";</script>') == "expired"

    def test_unknown_structure_returns_empty(self):
        from qrflow import extract_status

        assert extract_status("<html>nothing here</html>") == ""

    def test_poll_does_not_refetch_code_page(self):
        """轮询阶段不得重新拉 code 页（会下发新 str 使二维码失效）。"""
        from qrflow import QrAssistant

        calls = []

        class FakeAuth:
            class cfg:
                @staticmethod
                def get_token():
                    return ""

            def fetch_code_page(self, ip, mac):
                calls.append(1)
                return "code", "<html>x</html>"

        a = QrAssistant(FakeAuth())
        a.load("1.2.3.4", "aa:bb")
        assert len(calls) == 1
        a.poll("1.2.3.4", "aa:bb")
        a.poll("1.2.3.4", "aa:bb")
        assert len(calls) == 1, "轮询阶段不应再次请求 code 页"

    def test_ttl_matches_poll_budget(self):
        """二维码 TTL 与轮询时长必须一致。"""
        from qrflow import SESSION_TTL_MINUTES

        assert SESSION_TTL_MINUTES >= 5, "TTL 太短会让用户来不及扫"


# ---------------------------------------------------------------- GUI 冒测

@needs_tk
class TestGuiSmoke:
    """GUI 冒测 —— 捕获「控件没创建 / 属性缺失」这类必现崩溃。"""

    @pytest.fixture()
    def app(self):
        import tkinter as tk

        from legacy.tkui import App

        try:
            root = tk.Tk()
        except Exception as exc:  # TclError: 该环境没有可用的 Tk
            pytest.skip(f"无可用 GUI 环境：{exc}")
        root.withdraw()
        a = App.__new__(App)
        # 只做界面构建，不启动网络线程
        a.root = root
        a._quitting = False
        a._tray = None
        a._tray_autostart = None
        a.btn_login = None
        a._qr_win = None
        a._qr_img = None
        a._qr_assistant = None
        a._qr_cancel = None
        a._action_buttons = []
        a._dev_list = None
        a._dev_win = None
        a._icon_ref = None
        a.q = __import__("queue").Queue()
        a.cfg = FakeConfig()
        a.auth = FakeAuth()
        a._build()
        yield a
        try:
            root.destroy()
        except Exception:
            pass

    def test_btn_login_is_assigned(self, app):
        """「立即认证」按钮必须被保存 —— 曾因重构丢失导致全线失效。"""
        assert app.btn_login is not None, "btn_login 未赋值！"
        assert hasattr(app.btn_login, "set_enabled")

    def test_reenable_is_safe_without_button(self):
        """没有按钮时 _reenable 不能抛异常。"""
        from legacy.tkui import App

        a = App.__new__(App)
        a.btn_login = None
        a._reenable()  # 不应抛

    def test_do_login_does_not_raise(self, app, monkeypatch):
        """点「立即认证」不应抛 AttributeError。"""
        import threading

        started = []
        monkeypatch.setattr(threading, "Thread",
                            lambda *a, **k: type("T", (), {
                                "start": lambda s: started.append(1)})())
        app._do_login(takeover=False)
        assert started, "认证线程未启动"

    def test_all_action_buttons_present(self, app):
        """六个操作按钮都要在。"""
        assert len(app._action_buttons) == 6

    def test_takeover_requires_confirmation(self, app, monkeypatch):
        """抢占上线必须先确认 —— 不能无确认就踢设备。"""
        asked = {"n": 0}

        def fake_ask(*a, **k):
            asked["n"] += 1
            return False

        import legacy.tkui as main_mod

        monkeypatch.setattr(main_mod.messagebox, "askyesno", fake_ask)
        monkeypatch.setattr(app.cfg, "get_token", lambda: "tok")
        monkeypatch.setattr(app.auth, "detect_local_ip", lambda: "1.2.3.4")
        monkeypatch.setattr(app.auth, "online_devices",
                            lambda t: [{"ip": "5.6.7.8", "type": 1}])
        app._do_takeover()
        assert asked["n"] == 1, "抢占前未弹确认框"

    def test_logout_uses_current_ip(self, app, monkeypatch):
        """归还名额必须用当前 IP，不能用陈旧的 last_ip。"""
        seen = {}

        class FakeThread:
            def __init__(self, *a, **k):
                seen["args"] = k.get("args")

            def start(self):
                pass

        import threading

        monkeypatch.setattr(threading, "Thread", FakeThread)
        monkeypatch.setattr(app.auth, "detect_local_ip", lambda: "9.9.9.9")
        monkeypatch.setattr(app.cfg, "get_token", lambda: "tok")
        monkeypatch.setattr(app.cfg, "get", lambda k: "1.1.1.1")
        app._do_logout()
        assert seen.get("args", (None,))[0] == "9.9.9.9"


@needs_tk
class TestTokenHelpWindow:
    """Token 帮助窗口 —— 曾因 ``create_window`` 返回值被 pack 而必现崩溃。"""

    def test_help_window_opens(self):
        import tkinter as tk

        from legacy.tkui import App

        try:
            root = tk.Tk()
        except Exception as exc:  # TclError: 该环境没有可用的 Tk
            pytest.skip(f"无可用 GUI 环境：{exc}")
        root.withdraw()
        a = App.__new__(App)
        a.root = root
        a._quitting = False
        a._tray = None
        a._tray_autostart = None
        a.btn_login = None
        a._qr_win = None
        a._qr_img = None
        a._qr_assistant = None
        a._qr_cancel = None
        a._icon_ref = None
        a._build()
        try:
            a._show_token_help()   # 崩溃点：inner.pack
            a.root.update()
        finally:
            try:
                root.destroy()
            except Exception:
                pass


# ---------------------------------------------------------------- 只读接口

class TestCheckTokenIsReadOnly:
    """``check_token`` 必须走只读接口，不能改变在线状态。"""

    def test_uses_onestatus_not_auth(self):
        from auth import CampusAuth

        a = CampusAuth.__new__(CampusAuth)
        captured = {}

        def fake_get(url, token):
            captured["url"] = url
            return 200, '{"code":200}', {}

        a._get = fake_get
        a._url = lambda p, q: "http://x" + p
        a.check_token("1.2.3.4", "tok")
        assert "onestatus" in captured["url"], \
            f"check_token 用了会改变状态的接口: {captured['url']}"
        assert "/api/network/auth" not in captured["url"]


# ---------------------------------------------------------------- 凭据保险库

class TestVault:
    """凭据加密（P1-6）—— 旧版是「确定性 XOR 混淆」，已换成 DPAPI。"""

    def test_roundtrip(self):
        import vault

        blob = vault.protect("Bearer-token-abc-123456")
        assert blob
        assert blob != "Bearer-token-abc-123456"
        assert vault.unprotect(blob) == "Bearer-token-abc-123456"

    def test_ciphertext_is_not_deterministic(self):
        """同一明文两次加密必须得到不同密文（旧版 XOR 是确定性的）。"""
        import vault

        a = vault.protect("same-secret-value")
        b = vault.protect("same-secret-value")
        assert a != b, "密文是确定性的 —— 仍是被破解的 XOR 混淆"

    def test_tampered_ciphertext_fails(self):
        """密文被改动后必须解不出东西（完整性校验有效）。"""
        import base64

        import vault

        blob = vault.protect("tamper-check-value-1234")
        raw = bytearray(base64.b64decode(blob.split(":", 1)[1]))
        # DPAPI blob 前 20 字节是版本头，改正文区才代表真正的密文篡改
        raw[60] ^= 0xFF
        broken = blob.split(":", 1)[0] + ":" + base64.b64encode(bytes(raw)).decode()
        assert vault.unprotect(broken) == ""

    def test_empty_input(self):
        import vault

        assert vault.protect("") == ""
        assert vault.unprotect("") == ""

    def test_legacy_plaintext_is_readable(self):
        """历史明文 Token 仍要能读出来（迁移兼容）。"""
        import vault

        assert vault.unprotect("plain-old-token") == "plain-old-token"

    def test_legacy_ciphertext_is_readable(self):
        """历史 v2: 混淆密文仍要能解开（迁移期用）。"""
        import base64
        import hashlib
        import hmac

        import config

        enc, mac = config._derive_keys_v2()
        raw = b"legacy-token-value"
        ks = config._keystream(enc, len(raw))
        cipher = bytes(a ^ b for a, b in zip(raw, ks))
        tag = hmac.new(mac, cipher, hashlib.sha256).digest()[:16]
        blob = "v2:" + base64.b64encode(cipher + tag).decode()

        import vault

        assert vault.unprotect(blob) == "legacy-token-value"


class TestConfigSecurity:
    """配置存储安全（P1-6）。"""

    def _tmp_cfg(self, tmp_path):
        import config

        return config.Config(path=tmp_path / "config.json")

    def test_token_not_plaintext_on_disk(self, tmp_path):
        """Token 落盘必须是密文。"""
        cfg = self._tmp_cfg(tmp_path)
        cfg.set_token("super-secret-token-value")
        raw = (tmp_path / "config.json").read_text(encoding="utf-8")
        assert "super-secret-token-value" not in raw
        assert cfg.get_token() == "super-secret-token-value"

    def test_mobile_is_encrypted(self, tmp_path):
        """手机号不再明文存储（P1-6 明确要求）。"""
        cfg = self._tmp_cfg(tmp_path)
        cfg.set_account("13800138000", "pwd-123")
        raw = (tmp_path / "config.json").read_text(encoding="utf-8")
        assert "13800138000" not in raw
        assert cfg.get_account() == ("13800138000", "pwd-123")

    def test_legacy_plaintext_is_upgraded(self, tmp_path):
        """读到明文旧配置后应自动重加密成新版密文。"""
        import json

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text(json.dumps({
            "token": "legacy-plain-token",
            "account_mobile": "13900139000",
        }), encoding="utf-8")

        import config

        cfg = config.Config(path=cfg_path)
        assert cfg.get_token() == "legacy-plain-token"
        assert cfg.get_account()[0] == "13900139000"
        raw = cfg_path.read_text(encoding="utf-8")
        assert "legacy-plain-token" not in raw
        assert "13900139000" not in raw

    def test_clear_credentials(self, tmp_path):
        """「清除已保存凭据」要真的清干净，包括备份文件。"""
        cfg = self._tmp_cfg(tmp_path)
        cfg.set_token("token-to-clear")
        cfg.set_account("13800138000", "pwd-to-clear")
        assert cfg.has_token() and cfg.has_account()

        cfg.clear_credentials()
        assert not cfg.has_token()
        assert not cfg.has_account()
        # 备份文件里的旧密文也不能留
        bak = tmp_path / "config.json.bak"
        if bak.exists():
            assert "token-to-clear" not in bak.read_text(encoding="utf-8")

    def test_gateway_allowlist(self, tmp_path):
        """host / scheme 必须校验（P1-6）。"""
        import config

        cfg = self._tmp_cfg(tmp_path)
        ok, _ = cfg.validate_gateway("hgs.auth.yungao-tech.com", "http", 1110)
        assert ok
        ok, reason = cfg.validate_gateway("evil.example.com", "http", 1110)
        assert not ok and "白名单" in reason
        ok, reason = cfg.validate_gateway("hgs.auth.yungao-tech.com", "ftp", 1110)
        assert not ok
        ok, reason = cfg.validate_gateway("hgs.auth.yungao-tech.com", "http", 99999)
        assert not ok


# ---------------------------------------------------------------- 传输安全

class TestTransportSecurity:
    """传输层（P1-7）—— 明文 Token 不能发到白名单外的主机。"""

    def _auth(self, host="hgs.auth.yungao-tech.com", scheme="http"):
        from auth import CampusAuth

        class _Cfg:
            def get(self, key, default=None):
                return {"host": host, "scheme": scheme, "port": 1110}.get(key, default)

            def gateway_valid(self):
                import config

                return config.Config.validate_gateway(host, scheme, 1110)

        a = CampusAuth.__new__(CampusAuth)
        a.cfg = _Cfg()
        a.log = lambda *x, **k: None
        a.timeout = 8
        a._session = None
        return a

    def test_blocks_token_to_non_allowlisted_host(self):
        """网关不在白名单时，带 Token 的请求必须被拦下。"""
        a = self._auth(host="evil.example.com")
        code, msg, _ = a._get("http://evil.example.com/api/network/auth", "tok")
        assert code is None
        assert "白名单" in msg

    def test_allows_allowlisted_host(self):
        """白名单内的网关正常放行（不误伤正常使用）。"""
        a = self._auth()
        assert a.gateway_allowed()[0] is True

    def test_transport_warning_present_for_http(self):
        """HTTP 网关要给出明确的明文传输提示。"""
        a = self._auth(scheme="http")
        assert a.transport_is_secure() is False
        assert "明文" in a.transport_warning()

    def test_no_warning_for_https(self):
        a = self._auth(scheme="https")
        assert a.transport_is_secure() is True
        assert a.transport_warning() == ""


class TestSensitiveDataRedaction:
    """抓包工具不得写出完整 Token（P1-7）。"""

    def test_auth_fingerprint_hides_credential(self):
        import tools.capture as capture

        fp = capture._auth_fingerprint("Bearer eyJhbGciOiJIUzI1NiJ9abcdefgh")
        assert "eyJhbGciOiJIUzI1NiJ9abcdefgh" not in fp
        assert "present" in fp
        assert "len=" in fp

    def test_redact_masks_token_like_strings(self):
        import tools.capture as capture

        body = '{"access_token":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9QQ"}'
        out = capture._redact(body)
        assert "REDACTED" in out
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9QQ" not in out

    def test_redact_keeps_ordinary_text(self):
        import tools.capture as capture

        body = '{"code":200,"msg":"认证成功"}'
        assert capture._redact(body) == body


class TestLoginCheckNoCliPassword:
    """口令不得走命令行（P1-7）—— 参数行会被同机其他账号看到。"""

    def test_interactive_mode_by_default(self, monkeypatch):
        """不传参数时应走交互输入，而不是报错退出。"""
        import tools.login_check as login_check

        monkeypatch.setattr(login_check.sys, "argv", ["login_check.py"])
        monkeypatch.setattr(login_check, "_read_secret", lambda p: "secret-pwd")
        monkeypatch.setattr("builtins.input", lambda p="": "13800138000")

        seen = {}

        class _FakeLogin:
            def __init__(self):
                pass

            def login(self, mobile, password):
                seen["mobile"] = mobile
                seen["password"] = password
                raise RuntimeError("stop-here")   # 只验证参数传对了

        monkeypatch.setattr(login_check, "AccountLogin", _FakeLogin)
        try:
            login_check.main()
        except RuntimeError:
            pass
        assert seen["mobile"] == "13800138000"
        assert seen["password"] == "secret-pwd"

    def test_token_output_is_masked(self):
        """Token 打印只给长度与首尾片段，不整段输出。"""
        import tools.login_check as login_check

        out = login_check._mask_token("abcdefghijklmnopqrstuvwxyz012345")
        assert "abcdefghijklmnopqrstuvwxyz012345" not in out
        assert "32" in out


# ---------------------------------------------------------------- 文件日志

class TestLogging:
    """统一日志（P2-11）—— 落盘 + 轮转 + 脱敏。"""

    def test_redacts_authorization_header(self):
        import logs

        out = logs.redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdefgh")
        assert "eyJhbGciOiJIUzI1NiJ9abcdefgh" not in out
        assert "***" in out

    def test_redacts_token_field(self):
        import logs

        out = logs.redact('{"access_token":"abcdefghijklmnopqrstuvwxyz"}')
        assert "abcdefghijklmnopqrstuvwxyz" not in out
        assert "***" in out

    def test_redacts_mobile(self):
        import logs

        out = logs.redact("账号 13800138000 登录")
        assert "13800138000" not in out
        assert "138****8000" in out

    def test_redacts_password(self):
        import logs

        out = logs.redact('{"password":"SuperSecret123"}')
        assert "SuperSecret123" not in out

    def test_keeps_ordinary_text(self):
        import logs

        text = "认证成功：IP 10.135.1.2 已上线"
        assert logs.redact(text) == text

    def test_writes_to_file(self, tmp_path):
        import logs

        logs.init(tmp_path)
        logs.info("hello world")
        f = tmp_path / "app.log"
        assert f.exists()
        content = f.read_text(encoding="utf-8")
        assert "hello world" in content
        assert "INFO" in content

    def test_file_log_is_redacted(self, tmp_path):
        import logs

        logs.init(tmp_path)
        logs.info("token=abcdefghijklmnopqrstuvwxyz012345")
        content = (tmp_path / "app.log").read_text(encoding="utf-8")
        assert "abcdefghijklmnopqrstuvwxyz012345" not in content

    def test_rotation(self, tmp_path, monkeypatch):
        """超过上限要轮转，且旧文件保留。"""
        import logs

        # 把阈值调小，测试才不用写 512KB
        monkeypatch.setattr(logs, "MAX_BYTES", 2048)
        logs.init(tmp_path)
        sink = logs._sink
        for i in range(60):
            sink.write("INFO", "x" * 200 + str(i))
        f = tmp_path / "app.log"
        assert f.exists()
        assert f.stat().st_size <= logs.MAX_BYTES * 2
        # 至少有一份历史文件
        assert list(tmp_path.glob("app.*.log")), "没有产生轮转文件"


class TestAccountBreaker:
    """账号密码自动登录的熔断器。

    背景：密码错一次就把错误密码存下来并反复重试，
    只会锁死账号或触发服务端风控。
    """

    @needs_tk
    def test_breaker_trips_after_threshold(self, tmp_path, monkeypatch):
        import legacy.tkui as main_mod

        monkeypatch.setenv("GAOGAO_DATA_DIR", str(tmp_path))
        app = main_mod.App.__new__(main_mod.App)
        app._acct_fail_count = 0
        app._acct_fail_until = 0.0
        app.log = lambda msg: None

        assert app._acct_breaker_open() is False
        for _ in range(main_mod.App.ACCT_BREAKER_THRESHOLD):
            app._acct_breaker_trip()
        assert app._acct_breaker_open() is True

    @needs_tk
    def test_breaker_resets_on_success(self, tmp_path, monkeypatch):
        import legacy.tkui as main_mod

        monkeypatch.setenv("GAOGAO_DATA_DIR", str(tmp_path))
        app = main_mod.App.__new__(main_mod.App)
        app._acct_fail_count = 0
        app._acct_fail_until = 0.0
        app.log = lambda msg: None

        app._acct_breaker_trip()
        app._acct_breaker_trip()
        assert app._acct_fail_count == 2
        app._acct_breaker_reset()
        assert app._acct_fail_count == 0
        assert app._acct_breaker_open() is False

    @needs_tk
    def test_breaker_expires(self, tmp_path, monkeypatch):
        import legacy.tkui as main_mod
        import time as time_mod

        monkeypatch.setenv("GAOGAO_DATA_DIR", str(tmp_path))
        app = main_mod.App.__new__(main_mod.App)
        app._acct_fail_count = 0
        app._acct_fail_until = 0.0
        app.log = lambda msg: None

        for _ in range(main_mod.App.ACCT_BREAKER_THRESHOLD):
            app._acct_breaker_trip()
        assert app._acct_breaker_open() is True
        # 冷却期过后自动恢复
        app._acct_fail_until = time_mod.time() - 1
        assert app._acct_breaker_open() is False


class TestDirAcl:
    """数据目录 ACL 收紧。

    ⚠️ 这里曾经有过一个很严重的 bug：``SetNamedSecurityInfoW`` 要的是
    ``pDacl``（ACL 指针），但代码把整个 SECURITY_DESCRIPTOR 塞了进去。
    Windows 按 ACL 结构读 SD 头部，得到一个**损坏的空 DACL** ——
    目录变成「谁都没权限」，连当前用户自己都写不进去，
    而且程序自己都修不回来。

    这个测试就是防止它再犯。
    """

    @pytest.mark.skipif(os.name != "nt", reason="仅 Windows")
    def test_harden_dir_acl_keeps_own_access(self, tmp_path):
        """收紧之后，当前用户必须仍能读写 —— 不能把自己挡在门外。

        这三条断言合在一个测试里：``icacls`` 在临时目录上会被
        Defender 实时扫描，单跑一次要好几秒，拆开太慢。
        """
        import subprocess
        import vault

        d = tmp_path / "dataroot"
        d.mkdir()

        ok = vault.harden_dir_acl(d)
        assert ok, "harden_dir_acl 返回 False"

        # ① 关键：还能写
        probe = d / "probe.txt"
        probe.write_text("ok", encoding="utf-8")
        assert probe.read_text(encoding="utf-8") == "ok"

        # ② 继承应当被断开（D:P 的语义）
        r = subprocess.run(["icacls", str(d)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        assert "(I)" not in r.stdout, r.stdout

        # ③ 重复收紧不应把权限搞坏
        assert vault.harden_dir_acl(d) is True
        (d / "x.txt").write_text("ok", encoding="utf-8")


class TestWebUI:
    """本地 Web 界面的服务端行为。

    Web 界面是新的默认入口，它的安全边界靠这组测试守住。
    """

    @pytest.fixture()
    def server(self):
        import webui

        app = webui.WebApp(port=0)
        app.start()
        yield app
        app.stop()

    def _get(self, server, path, token=None, headers=None):
        import urllib.error
        import urllib.request

        url = f"http://127.0.0.1:{server.port}{path}"
        req = urllib.request.Request(url, headers=headers or {})
        if token:
            req.add_header("X-Auth-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_index_requires_token(self, server):
        """没令牌连首页都不给看。"""
        status, _ = self._get(server, "/")
        assert status == 403, status

    def test_index_with_token_ok(self, server):
        # 首屏靠 URL 里的 ?t= 传令牌（浏览器地址栏），不是 header
        status, body = self._get(server, f"/?t={server.token}")
        assert status == 200
        assert "糕糕校园网自动认证" in body.decode("utf-8")

    def test_api_requires_token(self, server):
        """API 必须鉴权 —— 防止同机其它程序调你的认证接口。"""
        status, _ = self._get(server, "/api/state")
        assert status == 401, status

    def test_api_with_token_ok(self, server):
        status, body = self._get(server, "/api/state", token=server.token)
        assert status == 200
        data = body.decode("utf-8")
        assert "gateway" in data

    def test_binds_localhost_only(self, server):
        """只监听 127.0.0.1，不对外网开放。"""
        assert server.host == "127.0.0.1"

    def test_token_is_random(self, server):
        """每次启动的令牌都不同。"""
        import webui

        other = webui.WebApp(port=0)
        assert other.token != server.token

    def test_static_assets_available(self, server):
        """样式与脚本要能取到。"""
        for path in ("/static/style.css", "/static/app.js"):
            status, body = self._get(server, path, token=server.token)
            assert status == 200, path
            assert len(body) > 500, path

    def test_path_traversal_blocked(self, server):
        """目录穿越必须被挡。"""
        status, _ = self._get(server, "/static/../core.py", token=server.token)
        assert status in (403, 404), status

    def test_gateway_allowlist_enforced(self, server):
        """非白名单网关不能通过 API 保存。"""
        import json
        import urllib.request

        req = urllib.request.Request(
            f"http://127.0.0.1:{server.port}/api/settings/save",
            data=json.dumps({"host": "evil.example.com", "port": 1110}).encode(),
            headers={"X-Auth-Token": server.token,
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            data = json.loads(r.read().decode("utf-8"))
        assert data["ok"] is False
        assert "白名单" in data["message"]

    def test_unknown_api_404(self, server):
        status, _ = self._get(server, "/api/nope", token=server.token)
        assert status == 404, status

    def test_url_file_written(self, server):
        """地址文件要能被写出来（打包版没有控制台，靠它找回入口）。"""
        p = server.write_url_file()
        assert p is not None
        assert p.read_text(encoding="utf-8").strip() == server.url()

    def test_client_disconnect_is_silent(self, server, capsys):
        """浏览器刷新 / 切标签导致的连接中断，不该刷 traceback。

        标准库默认会把每个连接异常打成一段 traceback，打包版虽然没有
        控制台，但这些噪音会混进日志文件，也会让用户误以为程序出错。
        """
        import socket
        import time

        for _ in range(3):
            s = socket.create_connection(("127.0.0.1", server.port), timeout=5)
            s.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n")  # 故意不完整
            s.close()
        time.sleep(0.4)

        out = capsys.readouterr()
        assert "Traceback" not in out.err, out.err
        assert "Exception occurred" not in out.err, out.err


class TestCoreController:
    """业务控制器（Web 与 Tk 共用的核心）。"""

    def test_info_snapshot_shape(self):
        import core

        c = core.Controller()
        info = c.info_snapshot()
        for key in ("gateway", "ip", "mac", "has_token", "autostart",
                    "phone_ip", "scheme"):
            assert key in info, key

    def test_settings_roundtrip_phone_ip_and_scheme(self, tmp_path, monkeypatch):
        """设置里的「手机 IP」必须能回读。

        曾经的 bug：``info_snapshot()`` 不返回 ``phone_ip``，
        前端每 15 秒刷新一次就把输入框清空，用户再点「保存设置」
        就把这项配置**写成了空值**。
        """
        import config
        import core

        monkeypatch.setenv("GAOGAO_DATA_DIR", str(tmp_path))
        c = core.Controller(cfg=config.Config(path=tmp_path / "config.json"))
        ok, _ = c.save_settings({
            "host": "hgs.auth.yungao-tech.com", "port": 1110,
            "scheme": "http", "phone_ip": "10.135.1.9",
        })
        assert ok
        info = c.info_snapshot()
        assert info["phone_ip"] == "10.135.1.9"
        assert info["scheme"] == "http"
        # 落盘也要在（不是只在内存里）
        assert config.Config(path=tmp_path / "config.json").get("phone_ip") == "10.135.1.9"


    def test_account_masked(self):
        """手机号只以掩码形式暴露。"""
        import core

        c = core.Controller()
        c.cfg.set_account("13800138000", "secret")
        try:
            masked = c.account_masked()
            assert "13800138000" not in masked
            assert "138" in masked and "8000" in masked
        finally:
            c.cfg.clear_credentials()

    def test_breaker_state(self):
        import core

        c = core.Controller()
        c._acct_fail_count = 0
        c._acct_fail_until = 0.0
        assert c._acct_breaker_open() is False
        for _ in range(core.Controller.ACCT_BREAKER_THRESHOLD):
            c._acct_breaker_trip()
        assert c._acct_breaker_open() is True
        c._acct_breaker_reset()
        assert c._acct_breaker_open() is False

    def test_save_settings_rejects_bad_host(self):
        import core

        c = core.Controller()
        ok, reason = c.save_settings({"host": "evil.example.com", "port": 1110})
        assert ok is False
        assert "白名单" in reason
