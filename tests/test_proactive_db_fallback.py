"""主动搭话会话级规则判定测试

覆盖：
- 综合冷却（_cooldown_ok / _screen_cooldown_ok）
- 规则判定（_check_schedule / _check_idle_rule / _check_time_windows）
- 会话记忆与 enabled 会话读取（_get_session_conversation / _get_enabled_outreach_sessions）
- Qt 端前提（_qt_active）
"""
import sys
import os
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.perception.trigger import ProactiveTrigger


class TestCooldown(unittest.TestCase):
    def setUp(self):
        self.trigger = ProactiveTrigger.__new__(ProactiveTrigger)
        self.trigger._session_last_trigger = {}
        self.trigger._screen_last_trigger = {}
        self.trigger._last_rule_check = {}
        self.trigger._lock = __import__("threading").Lock()
        self.trigger._trigger_count = 0

    def test_cooldown_ok_default(self):
        """无冷却记录时允许触发"""
        self.assertTrue(self.trigger._cooldown_ok("s1", {}))

    def test_cooldown_blocked_within_window(self):
        """刚触发过（综合冷却内）应阻止"""
        self.trigger._session_last_trigger["s1"] = time.time()
        self.assertFalse(self.trigger._cooldown_ok("s1", {"cooldown_minutes": 30}))
        self.assertTrue(self.trigger._cooldown_ok("s1", {"cooldown_minutes": 0}))

    def test_screen_cooldown_ok(self):
        """screen 规则冷却与综合冷却同时满足才允许"""
        self.assertTrue(self.trigger._screen_cooldown_ok("s1", {}))
        self.trigger._screen_last_trigger["s1"] = time.time()
        self.assertFalse(self.trigger._screen_cooldown_ok("s1", {"cooldown_minutes": 20}))

    def test_rule_ready_interval(self):
        """规则判定间隔控制频率"""
        self.assertTrue(self.trigger._rule_ready("s1", "idle", 60))
        self.assertFalse(self.trigger._rule_ready("s1", "idle", 60))  # 60s 内不再判定
        self.assertTrue(self.trigger._rule_ready("s1", "screen", 30))  # 不同规则独立


class TestScheduleRule(unittest.TestCase):
    def setUp(self):
        self.trigger = ProactiveTrigger.__new__(ProactiveTrigger)

    def test_schedule_no_config(self):
        self.assertFalse(self.trigger._check_schedule({}))
        self.assertFalse(self.trigger._check_schedule({"schedule": {}}))

    def test_schedule_in_window(self):
        """当前在 schedule.time ± jitter 内应触发"""
        from datetime import datetime, timedelta
        now = datetime.now()
        # 目标 = 当前时间，jitter 1 分钟 → 在窗口内
        cfg = {"schedule": {"time": now.strftime("%H:%M"), "jitter_minutes": 5}}
        self.assertTrue(self.trigger._check_schedule(cfg))
        # 目标 = 当前时间 ± 2 小时，jitter 1 分钟 → 不在窗口
        far = now + timedelta(hours=2)
        cfg2 = {"schedule": {"time": far.strftime("%H:%M"), "jitter_minutes": 1}}
        self.assertFalse(self.trigger._check_schedule(cfg2))


class TestIdleAndTimeWindow(unittest.TestCase):
    def setUp(self):
        self.trigger = ProactiveTrigger.__new__(ProactiveTrigger)

    def test_idle_rule(self):
        """空闲 >= idle_minutes 且概率命中"""
        # 空闲不足
        with mock.patch.object(self.trigger._idle_timer if hasattr(self.trigger, "_idle_timer") else type("T", (), {"idle_minutes": 0})(), "idle_minutes", 0):
            pass
        # 直接测：idle_minutes=0 且 probability=1 → 触发
        idle_mock = mock.Mock()
        idle_mock.idle_minutes = 60
        self.trigger._idle_timer = idle_mock
        self.assertTrue(self.trigger._check_idle_rule({"idle_minutes": 30, "probability": 1.0}))
        # 空闲不足
        idle_mock.idle_minutes = 10
        self.assertFalse(self.trigger._check_idle_rule({"idle_minutes": 30, "probability": 1.0}))
        # 概率 0 → 永不触发
        idle_mock.idle_minutes = 60
        self.assertFalse(self.trigger._check_idle_rule({"idle_minutes": 30, "probability": 0.0}))

    def test_time_windows(self):
        """当前在某时段内按概率触发"""
        from datetime import datetime, timedelta
        now = datetime.now()
        start = (now - timedelta(minutes=5)).strftime("%H:%M")
        end = (now + timedelta(minutes=5)).strftime("%H:%M")
        cfg = {"time_windows": [{"start": start, "end": end, "probability": 1.0}]}
        self.assertTrue(self.trigger._check_time_windows(cfg))
        # 跨午夜窗口（start>end 时视为跨天）：用相对时间构造含当前时刻的跨天窗口
        s = (now - timedelta(minutes=30)).strftime("%H:%M")
        e = (now - timedelta(minutes=60)).strftime("%H:%M")
        cfg_night = {"time_windows": [{"start": s, "end": e, "probability": 1.0}]}
        self.assertTrue(self.trigger._check_time_windows(cfg_night))
        # 概率 0 → 不触发
        cfg0 = {"time_windows": [{"start": start, "end": end, "probability": 0.0}]}
        self.assertFalse(self.trigger._check_time_windows(cfg0))
        # 无时段 → 不触发
        self.assertFalse(self.trigger._check_time_windows({}))


class TestSessionHelpers(unittest.TestCase):
    def setUp(self):
        self.trigger = ProactiveTrigger.__new__(ProactiveTrigger)

    def test_get_session_conversation(self):
        """会话记忆：DB 最近消息组装 [role]: content"""
        fake_repo = mock.Mock()
        fake_repo.get_recent_messages.return_value = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "有什么可以帮你？"},
        ]
        with mock.patch("modules.database.session_repo.get_session_repo", return_value=fake_repo):
            conv = self.trigger._get_session_conversation("s1")
        self.assertIn("[user]: 你好", conv)
        self.assertIn("[assistant]: 有什么可以帮你", conv)

    def test_get_enabled_outreach_sessions(self):
        """只返回 enabled 会话"""
        fake_repo = mock.Mock()
        fake_repo.get_all_sessions.return_value = [
            {"session_id": "a", "metadata": {"outreach": {"enabled": True}}},
            {"session_id": "b", "metadata": {"outreach": {"enabled": False}}},
            {"session_id": "c", "metadata": {}},
        ]
        with mock.patch("modules.database.session_repo.get_session_repo", return_value=fake_repo):
            enabled = self.trigger._get_enabled_outreach_sessions()
        self.assertEqual(list(enabled.keys()), ["a"])

    def test_qt_active(self):
        """Qt 端开着（有活跃 WS 连接）为前提"""
        mgr = mock.Mock()
        mgr.active_connections = {"s1": object()}
        with mock.patch("modules.thinking.api_stream.connection_manager", mgr):
            self.assertTrue(self.trigger._qt_active())
        mgr2 = mock.Mock()
        mgr2.active_connections = {}
        with mock.patch("modules.thinking.api_stream.connection_manager", mgr2):
            self.assertFalse(self.trigger._qt_active())


if __name__ == "__main__":
    unittest.main()
