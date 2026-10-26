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
