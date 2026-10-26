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
