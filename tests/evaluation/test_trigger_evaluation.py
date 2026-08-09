"""
Perception Trigger 评估测试
========================
评估主动搭话系统的实际触发逻辑和合理性
"""
import pytest
import time
from unittest.mock import MagicMock, patch


class TestProactiveTriggerLogic:
    """评估主动搭话触发逻辑"""

    def test_idle_timer_behavior(self):
        """空闲计时器是否正常工作"""
        from modules.perception.trigger import IdleTimer

        timer = IdleTimer()
        initial_idle = timer.idle_seconds

        time.sleep(0.1)
        after_sleep = timer.idle_seconds

        print(f"\n【IdleTimer 评估】")
        print(f"  初始空闲: {initial_idle:.2f}s")
        print(f"  睡眠0.1s后: {after_sleep:.2f}s")

        assert after_sleep > initial_idle, "空闲时间应该增加"
        assert abs(after_sleep - initial_idle - 0.1) < 0.05, "增加量应该约等于睡眠时间"

    def test_idle_timer_reset(self):
        """用户活动是否重置空闲计时"""
        from modules.perception.trigger import IdleTimer

        timer = IdleTimer()
        time.sleep(0.1)
        before_reset = timer.idle_seconds

        timer.notify_activity()
        after_reset = timer.idle_seconds

        print(f"\n【IdleTimer 重置评估】")
        print(f"  重置前: {before_reset:.2f}s")
        print(f"  重置后: {after_reset:.2f}s")

        assert after_reset < before_reset, "重置后空闲时间应该减少"
        assert after_reset < 0.05, "重置后空闲时间应该接近0"

    def test_outreach_trigger_conditions(self):
        """主动搭话的触发条件是否合理"""
        from modules.perception.trigger import outreach_trigger_allowed

        # 测试全局开关
        with patch('modules.perception.trigger.ProactiveTrigger') as MockTrigger:
            mock_instance = MagicMock()
            mock_instance._get_enabled_outreach_sessions.return_value = {}
            MockTrigger.return_value = mock_instance

            result = outreach_trigger_allowed()
            print(f"\n【触发条件评估】")
            print(f"  无启用会话时: {result}")
            assert result is False, "无启用会话时应返回False"

    def test_screen_diff_handling(self):
        """屏幕变化事件处理是否合理"""
        from modules.perception.trigger import ProactiveTrigger

        trigger = ProactiveTrigger()

        # 模拟屏幕变化事件
        event = MagicMock()
        event.payload = {
            "change_ratio": 0.25,  # 25% 变化
            "changed_regions": [{"x": 0, "y": 0, "w": 800, "h": 600}]
        }

        # 测试：无启用会话时不应触发
        with patch.object(trigger, '_get_enabled_outreach_sessions', return_value={}):
            trigger._on_screen_diff(event)
            print(f"\n【屏幕变化处理评估】")
            print(f"  25% 屏幕变化，无启用会话 → 无触发")

    def test_cooldown_mechanism(self):
        """冷却机制是否有效防止过度触发"""
        from modules.perception.trigger import ProactiveTrigger

        trigger = ProactiveTrigger()

        # 模拟触发
        session_id = "test_session"
        cfg = {"cooldown_minutes": 15}

        # 第一次触发
        with patch.object(trigger, '_lock'):
            trigger._session_last_trigger[session_id] = time.time()

        # 检查冷却
        is_cooldown = not trigger._cooldown_ok(session_id, cfg)

        print(f"\n【冷却机制评估】")
        print(f"  刚触发后检查冷却: {is_cooldown}")
        assert is_cooldown, "刚触发后应该处于冷却期"


class TestOutreachPromptQuality:
    """评估主动搭话的提示词质量"""

    def test_prompt_construction(self):
        """提示词是否包含必要信息"""
        from modules.perception.trigger import _build_outreach_system_prompt

        prompt = _build_outreach_system_prompt()

        print(f"\n【提示词质量评估】")
        print(f"  提示词长度: {len(prompt)} 字符")
        print(f"  包含主动搭话指令: {'主动搭话模式' in prompt}")

        # 验证：应该包含主动搭话专用指令
        assert "主动搭话模式" in prompt, "应该包含主动搭话模式指令"
        assert "回复简短自然" in prompt, "应该要求简短回复"

    def test_prompt_skips_tool_rules(self):
        """提示词是否正确跳过工具规则"""
        from modules.perception.trigger import _build_outreach_system_prompt

        prompt = _build_outreach_system_prompt()

        # 验证：不应该包含工具调用规则
        has_tool_rules = "【工具调用规则】" in prompt or "tools_search" in prompt
        print(f"  包含工具规则: {has_tool_rules}")
        assert not has_tool_rules, "主动搭话不应包含工具规则"


class TestTriggerIntegration:
    """评估触发系统与主系统的集成"""

    def test_event_bus_subscription(self):
        """是否正确订阅事件总线"""
        from modules.perception.trigger import ProactiveTrigger
        from modules.perception.events.types import PerceptionEventType

        trigger = ProactiveTrigger()
        mock_bus = MagicMock()

        trigger.start(mock_bus)

        print(f"\n【事件订阅评估】")
        print(f"  订阅事件类型: {mock_bus.subscribe.call_args}")

        # 验证：应该订阅 SCREEN_DIFF 事件
        assert mock_bus.subscribe.called, "应该订阅事件"
        call_args = mock_bus.subscribe.call_args
        assert call_args[0][0] == PerceptionEventType.SCREEN_DIFF, "应该订阅 SCREEN_DIFF"

        trigger.stop()

    def test_lifecycle_management(self):
        """生命周期管理是否完整"""
        from modules.perception.trigger import ProactiveTrigger

        trigger = ProactiveTrigger()

        # 测试启动/停止
        mock_bus = MagicMock()
        trigger.start(mock_bus)
        assert trigger._event_bus is not None, "启动后应该有 event_bus"

        trigger.stop()
        assert trigger._event_bus is None, "停止后 event_bus 应为 None"

        print(f"\n【生命周期评估】")
        print(f"  启动/停止正常")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
