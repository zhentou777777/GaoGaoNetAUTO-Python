# -*- coding: utf-8 -*-
"""修复项回归测试 —— 与 ``test_gaogao.py`` 分离，便于单跑：

    python -m pytest tests/test_regressions.py -v
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestTakeoverWithoutOtherDevices:
    """抢占上线：没有其他设备在online时也必须真的开始认证。

    曾经的 bug：``_takeover_bg_inner`` 里调 ``self.login()``，
    而忙碌锁已被 ``takeover()`` 持有 —— 于是只打一行
    「已有认证在进行中」，按钮静默无效。
    """

    def test_takeover_runs_login_when_no_other_devices(self):
        import core

        class FakeCfg:
            def get(self, key, default=None):
                return {"phone_ip": "", "auto_kick_phone": False}.get(key, default)

            def get_token(self):
                return "FAKE_TOKEN"

            def get_account(self):
                return "", ""

            def has_account(self):
                return False

        class FakeAuth:
            def detect_local_ip(self):
                return "9.9.9.9"

            def online_devices(self, token):
                return []

        c = core.Controller(cfg=FakeCfg())
        c.auth = FakeAuth()
        assert c._set_busy(True) is True      # 模拟 takeover() 已持锁

        called = {}
        c._login_bg_inner = lambda takeover=False: called.update(takeover=takeover)
        c._takeover_bg_inner()

        assert called.get("takeover") is True, "抢占在无其他设备时没有继续认证"


class TestNetGuardReconnect:
    """重连循环必须能被停掉，并且停掉之后还能再启动。"""

    def test_stop_reconnect_actually_stops_loop(self, monkeypatch):
        import netguard

        monkeypatch.setattr(netguard, "probe_online", lambda *a, **k: False)
        g = netguard.NetGuard(auth=None, retry_base=0.02, retry_max=0.02)

        g.start_reconnect()
        assert g.reconnecting is True
        import time
        time.sleep(0.12)
        g.stop_reconnect()
        g._reconnect_thread.join(timeout=3)
        assert not g._reconnect_thread.is_alive(), "重连线程没停下来"
        assert g.reconnecting is False

        # 旧实现里线程自己 return 后标志不复位，第二次永远起不来
        g.start_reconnect()
        assert g.reconnecting is True
        g.stop_reconnect()
        g._reconnect_thread.join(timeout=3)

    def test_probe_targets_are_consistent(self):
        """探测目标必须自带期望状态码 —— generate_204 返回的是 204，
        曾经按 200 判定，导致这条探针永远失败。"""
        import netguard

        for url, status, expect in netguard._PROBE_TARGETS:
            assert status in (200, 204), url
            assert isinstance(expect, str), url
        assert netguard._PROBE_TARGETS[0][1] == 204


class TestAccountLoginClassify:
    """登录失败归类 —— 「手机号不存在」不能被说成「手机号格式不对」。"""

    def test_messages_are_classified_in_the_right_order(self):
        from acclogin import (
            ERR_BAD_MOBILE,
            ERR_BAD_PARAMS,
            ERR_BAD_PASSWORD,
            ERR_NO_ACCOUNT,
            _classify,
        )

        assert _classify({"msg": "该手机号不存在"}) == ERR_NO_ACCOUNT
        assert _classify({"msg": "手机号不合法"}) == ERR_BAD_MOBILE
        assert _classify({"msg": "参数不能为空"}) == ERR_BAD_PARAMS
        assert _classify({"msg": "密码错误"}) == ERR_BAD_PASSWORD


class TestQrPollThrottle:
    """扫码轮询不能变成对网关的压测。

    曾经的实现每 2 秒发一次 ``auth?str=``（真实认证请求）+ 一轮
    ``probe_online()``（3 个探测地址），10 分钟能发出几百次请求。
    """

    def _assistant(self, monkeypatch):
        import netguard
        import qrflow

        monkeypatch.setattr(netguard, "probe_online", lambda *a, **k: False)
        calls = []

        class FakeCfg:
            @staticmethod
            def get_token():
                return "token-value-1234567890"

        class FakeAuth:
            cfg = FakeCfg()

            def fetch_code_page(self, ip, mac):
                return "uuid-1", '<html>var code="uuid-1"</html>'

            def auth_by_code(self, session, token):
                calls.append(session)
                return type("R", (), {"ok": False})()

        a = qrflow.QrAssistant(FakeAuth())
        a.load("1.2.3.4", "aa:bb")
        return a, calls

    def test_poll_does_not_hit_gateway_every_tick(self, monkeypatch):
        a, calls = self._assistant(monkeypatch)
        for _ in range(10):
            a.poll("1.2.3.4", "aa:bb")
        assert calls == [], "二维码刚生成就开始打 auth?str= 了"

    def test_poll_confirms_after_grace_and_then_respects_interval(self, monkeypatch):
        import qrflow

        a, calls = self._assistant(monkeypatch)
        a._created_at -= qrflow.AUTH_CONFIRM_GRACE + 1
        a.poll("1.2.3.4", "aa:bb")
        assert len(calls) == 1

        for _ in range(5):          # 间隔没到，不该再发
            a.poll("1.2.3.4", "aa:bb")
        assert len(calls) == 1

        a._last_auth_at -= qrflow.AUTH_CONFIRM_INTERVAL + 1
        a.poll("1.2.3.4", "aa:bb")
        assert len(calls) == 2


class TestBuildTooling:
    """构建脚本的两条硬约束。"""

    def test_release_zip_names_are_ascii(self):
        """交付包文件名必须是纯 ASCII —— 中文名上传 GitHub Release 会 404。"""
        text = (ROOT / "tools" / "make_release.py").read_text(encoding="utf-8")
        for suffix in ("portable.zip", "singlefile.zip", "source.zip"):
            assert suffix in text, suffix
        for legacy in ("免安装.zip", "单文件.zip", "源码绿色包.zip"):
            assert legacy not in text, f"仍有中文包名：{legacy}"

    def test_build_scripts_force_utf8_stdout(self):
        """Windows runner 的控制台是 cp1252 —— 不切 UTF-8，print 中文就崩。

        v1.0.0 / v1.0.1 的两次 CI 都是挂在
        ``UnicodeEncodeError: 'charmap' codec can't encode`` 上。
        """
        for name in ("tools/build_exe.py", "tools/make_release.py",
                     "tools/make_docs_package.py", "tools/audit_features.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            assert 'reconfigure(encoding="utf-8"' in text, name


class TestAutostartWrite:
    """开机自启的写入鲁棒性。

    真实故障：点「开机自启」报
    ``PermissionError: [Errno 13] ... Startup\\GaoGaoNetAUTO.cmd``，
    开关直接开不了，界面只显示一句 Errno 13。
    常见原因是安全软件的「启动项保护」或文件被占用，不是 Windows 权限。
    """

    def _startup(self, tmp_path, monkeypatch):
        import autostart

        d = tmp_path / "Startup"
        d.mkdir()
        monkeypatch.setattr(autostart, "startup_dir", lambda: d)
        return autostart, d / autostart.CMD_NAME

    def test_write_falls_back_to_temp_replace(self, tmp_path, monkeypatch):
        """直接写被拒时，走「临时文件 + 原子替换」这条路。"""
        autostart, target = self._startup(tmp_path, monkeypatch)

        real = autostart._write_direct

        def fake(path, data):
            if path == target:
                raise PermissionError(13, "Permission denied")
            return real(path, data)

        monkeypatch.setattr(autostart, "_write_direct", fake)
        ok, _msg = autostart.apply_autostart(True)
        assert ok, "兜底写入路径没有生效"
        assert target.exists()
        assert b"@echo off" in target.read_bytes()

    def test_stale_entry_is_detected(self, tmp_path, monkeypatch):
        """文件在但指向的程序已经不在了 —— 必须识别成「过期」。"""
        autostart, target = self._startup(tmp_path, monkeypatch)
        stale = '@echo off\r\nstart "" "D:\\old\\gone.exe" --silent\r\n'
        target.write_bytes(stale.encode("gbk"))
        assert autostart.autostart_stale() is True

        autostart.apply_autostart(True)
        assert autostart.autostart_stale() is False

    def test_other_but_valid_entry_is_not_stale(self, tmp_path, monkeypatch):
        """只是路径不同（源码 ↔ 打包版）但程序还在，不算过期。

        否则每换一种运行方式就重写一次启动项，入口会来回翻烧饼。
        """
        autostart, target = self._startup(tmp_path, monkeypatch)
        other = tmp_path / "elsewhere.exe"
        other.write_bytes(b"MZ")
        cmd = '@echo off\r\nstart "" "%s" --silent\r\n' % other
        target.write_bytes(cmd.encode("gbk"))
        assert autostart.autostart_stale() is False

    def test_same_content_is_not_rewritten(self, tmp_path, monkeypatch):
        """内容已经一样就别再写一遍。

        白挨一次杀软扫描不说，拦截还可能让自启**明明已经生效**却报失败，
        用户以为没设上，反复折腾。
        """
        autostart, target = self._startup(tmp_path, monkeypatch)
        autostart.apply_autostart(True)
        assert target.exists()

        calls = []
        real = autostart._write_direct

        def spy(path, data):
            calls.append(path)
            return real(path, data)

        monkeypatch.setattr(autostart, "_write_direct", spy)
        ok, _msg = autostart.apply_autostart(True)
        assert ok
        assert calls == [], "内容没变却重写了文件"

    def test_transient_failure_is_retried(self, tmp_path, monkeypatch):
        """被拦一次的后果应该只是「慢一点」，不是「开不了」。"""
        autostart, target = self._startup(tmp_path, monkeypatch)
        state = {"n": 0}
        real = autostart._write_direct

        def flaky(path, data):
            state["n"] += 1
            if state["n"] == 1:
                raise PermissionError(13, "Permission denied")
            return real(path, data)

        monkeypatch.setattr(autostart, "_write_direct", flaky)
        ok, _msg = autostart.apply_autostart(True)
        assert ok
        assert target.exists()
        assert b"@echo off" in target.read_bytes()

    def test_config_write_failure_is_reported(self, tmp_path):
        """配置写不进去要说出来，不能再静默成功。"""
        import config as cfgmod

        c = cfgmod.Config(str(tmp_path / "config.json"))
        assert c.storage_problem() == ""
        c.write_error = "[Errno 13] Permission denied"
        assert "写不进去" in c.storage_problem()

    def test_permission_error_message_is_actionable(self, tmp_path, monkeypatch):
        """PermissionError 要给出人能照做的建议，而不是一句 Errno 13。"""
        autostart, target = self._startup(tmp_path, monkeypatch)

        def fake(path, data):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(autostart, "_write_direct", fake)
        monkeypatch.setattr(
            autostart.os, "replace",
            lambda *a: (_ for _ in ()).throw(PermissionError(13, "denied")))
        ok, msg = autostart.apply_autostart(True)
        assert ok is False
        assert "shell:startup" in msg
        assert "安全软件" in msg

    def test_failed_enable_reverts_config_flag(self, tmp_path, monkeypatch):
        """开不了自启时要把配置里的开关拨回去，否则每次启动都再失败一次。"""
        import autostart
        import core

        monkeypatch.setattr(autostart, "apply_autostart",
                            lambda enable: (False, "没有权限写入「启动」文件夹"))
        c = core.Controller()
        c.cfg.set("autostart", True, save=False)
        assert c.cfg.get("autostart") is True

        ok, _msg = c.apply_autostart(True)
        assert ok is False
        assert c.cfg.get("autostart") is False


class TestWebStartupChecks:
    """Web 界面启动时必须接上 ``Controller.startup_checks``。

    这条线曾经是**断的**：方法定义了却没人调用，于是「自启开关是开的，
    开机后却不自动认证」「配置里的自启状态和启动文件夹里的文件长期对不上」，
    日志文件也可能整个启动过程一行不写。
    """

    class _FakeApp:
        def __init__(self, box):
            self.box = box
            outer = self

            class _Ctrl:
                def startup_checks(self):
                    outer.box["checks"] += 1

            self.controller = _Ctrl()

        def start(self):
            return 8765

        def url(self):
            return "http://127.0.0.1:8765/"

        def write_url_file(self):
            return None

        def open_browser(self):
            self.box["browser"] += 1
            return True

        def start_tray(self):
            pass

        def serve_forever(self):
            pass

        def stop(self):
            pass

    def _run(self, monkeypatch, silent):
        import main as mainmod
        import webui

        box = {"checks": 0, "browser": 0}
        monkeypatch.setattr(webui, "WebApp", lambda **kw: self._FakeApp(box))
        monkeypatch.setattr(webui, "pick_port", lambda: 8765)
        mainmod._run_web_ui(silent)

        for _ in range(100):
            if box["checks"]:
                break
            time.sleep(0.02)
        return box

    def test_startup_checks_are_invoked(self, monkeypatch):
        box = self._run(monkeypatch, silent=False)
        assert box["checks"] == 1, "启动检查没被调用"
        assert box["browser"] == 1

    def test_silent_run_does_not_open_browser(self, monkeypatch):
        """开机自启（--silent）不该在开机时弹一个浏览器窗口。"""
        box = self._run(monkeypatch, silent=True)
        assert box["checks"] == 1
        assert box["browser"] == 0


class TestConfigRobustness:
    """配置文件不能「悄悄坏、悄悄丢」。"""

    def _cfg(self, tmp_path, name="config.json"):
        import config as cfgmod

        return cfgmod.Config(str(tmp_path / name))

    def test_corrupt_config_is_reported(self, tmp_path):
        """半截 JSON（写到一半被杀进程 / 断电）要留痕。"""
        p = tmp_path / "config.json"
        p.write_text('{"token": "dpapi:AAAA', encoding="utf-8")
        c = self._cfg(tmp_path)
        assert "损坏" in c.read_error, c.read_error
        assert "读不了" in c.storage_problem()
        # 不能因为一个坏文件就崩掉 —— 用默认值继续跑
        assert c.get("port") == 1110

    def test_corrupt_primary_falls_back_to_backup(self, tmp_path):
        """主文件坏了但备份是好的，应该用备份，且不算「读不了」。"""
        import json

        (tmp_path / "config.json").write_text("{半截", encoding="utf-8")
        (tmp_path / "config.json.bak").write_text(
            json.dumps({"port": 2222}), encoding="utf-8")
        c = self._cfg(tmp_path)
        assert c.get("port") == 2222
        assert c.storage_problem() == ""

    def test_save_leaves_no_temp_file(self, tmp_path):
        """原子写入：临时文件必须清干净。"""
        c = self._cfg(tmp_path)
        assert c.save() is True
        assert (tmp_path / "config.json").exists()
        assert not (tmp_path / "config.json.tmp").exists()
        assert not (tmp_path / "config.json.bak.tmp").exists()


    def test_failed_save_keeps_previous_content(self, tmp_path, monkeypatch):
        """写盘失败时，旧配置必须**完好**（这就是原子替换的意义）。"""
        import config as cfgmod

        p = tmp_path / "config.json"
        c = cfgmod.Config(str(p))
        c.set("port", 1110)
        good = p.read_text(encoding="utf-8")

        c.set("port", 2222, save=False)
        monkeypatch.setattr(
            cfgmod.os, "replace",
            lambda *a, **k: (_ for _ in ()).throw(OSError("目标被占用")))

        assert c.save() is False
        assert p.read_text(encoding="utf-8") == good, "旧配置被写坏了"
        assert not (tmp_path / "config.json.tmp").exists(), "临时文件没清掉"
        assert "目标被占用" in c.storage_problem()


class TestSettingsPartialUpdate:
    """只改网关不该把别的开关一起关掉。"""

    def test_missing_flags_are_preserved(self, tmp_path):
        import config as cfgmod
        from core import Controller

        c = Controller(cfg=cfgmod.Config(str(tmp_path / "config.json")))
        c.cfg.set("autostart", True)
        c.cfg.set("auto_kick_phone", True)
        c.cfg.set("phone_ip", "10.0.0.9")

        ok, _msg = c.save_settings({"host": "hgs.auth.yungao-tech.com",
                                    "port": "1110"})
        assert ok
        assert c.cfg.get("autostart") is True, "自启开关被静默关掉了"
        assert c.cfg.get("auto_kick_phone") is True
        assert c.cfg.get("phone_ip") == "10.0.0.9"

    def test_explicit_false_still_works(self, tmp_path):
        """显式传 false 仍然要能关掉。"""
        import config as cfgmod
        from core import Controller

        c = Controller(cfg=cfgmod.Config(str(tmp_path / "config.json")))
        c.cfg.set("autostart", True)
        ok, _ = c.save_settings({"host": "hgs.auth.yungao-tech.com",
                                 "port": "1110", "autostart": False})
        assert ok
        assert c.cfg.get("autostart") is False


class TestTokenRotation:
    """账号密码换到的 Token 必须立刻存下来。"""

    def test_self_heal_token_persists_even_if_auth_fails(self, tmp_path, monkeypatch):
        import acclogin
        import config as cfgmod
        from auth import TOKEN_INVALID, AuthResult, CampusAuth

        cfg = cfgmod.Config(str(tmp_path / "config.json"))
        cfg.set_token("OLD_TOKEN")
        auth = CampusAuth(cfg)

        class _FakeLogin:
            ok = True
            token = "NEW_TOKEN"
            message = "ok"

            def login(self, mobile, password):
                return self

        monkeypatch.setattr(acclogin, "AccountLogin", lambda: _FakeLogin())
        monkeypatch.setattr(
            auth, "login_chain",
            lambda *a, **k: AuthResult(ok=False, status=TOKEN_INVALID,
                                       message="服务端拒绝"))

        res = auth.login_chain_self_heal("10.0.0.5", "OLD_TOKEN",
                                         account=("13100000000", "pwd"))
        assert res.ok is False
        assert cfg.get_token() == "NEW_TOKEN", "换到的 Token 没落盘"
        assert cfg.get_token_prev() == "OLD_TOKEN", "旧的 Token 没留作回退"

    def test_core_does_not_clobber_token_prev(self, tmp_path, monkeypatch):
        """自愈链路已存过新 Token 时，core 不能把 token_prev 写成新 Token 自己。"""
        import config as cfgmod
        from core import Controller

        c = Controller(cfg=cfgmod.Config(str(tmp_path / "config.json")))
        c.cfg.set_token("OLD")
        c.cfg.set("token_prev", c.cfg.get("token"))

        # 模拟：认证成功 + 服务端下发新 Token，而新 Token 已经是当前值
        c.cfg.set_token("NEW")

        from auth import AuthResult
        res = AuthResult(ok=True, message="ok", new_token="NEW")
        token = "OLD"
        saved = c.cfg.get_token()
        # 复刻 core._login_bg_inner 里的续存判断
        if res.ok and res.new_token and res.new_token != saved:
            c.cfg.set("token_prev", c.cfg.get("token"))
            c.cfg.set_token(res.new_token)
        assert c.cfg.get_token_prev() == "OLD", "token_prev 被污染了"

class TestWebAuthCookie:
    """Cookie 鉴权必须精确比对，不能靠子串。"""

    def test_cookie_token_must_match_exactly(self):
        import urllib.error
        import urllib.request

        import webui

        app = webui.WebApp(port=0)
        app.start()
        try:
            base = f"http://127.0.0.1:{app.port}/api/state"

            good = urllib.request.Request(
                base, headers={"Cookie": f"gg_token={app.token}"})
            with urllib.request.urlopen(good, timeout=5) as r:
                assert r.status == 200

            # 前缀相同、多一个字符的假 token 不能过
            bad = urllib.request.Request(
                base, headers={"Cookie": f"gg_token={app.token}X"})
            try:
                urllib.request.urlopen(bad, timeout=5)
                assert False, "伪造的 cookie 被放行了"
            except urllib.error.HTTPError as e:
                assert e.code == 401
        finally:
            app.stop()
