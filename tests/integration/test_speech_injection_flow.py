"""语音识别注入流程测试
====================
追踪 SPEECH_DETECTED 事件从产生到注入 prompt 的完整流程
"""
import pytest
import time
from unittest.mock import MagicMock, patch


class TestSpeechInjectionFlow:
    """测试语音事件的完整注入流程"""

    def test_event_creation(self):
        """测试语音事件是否正确创建"""
        from modules.perception.events.types import PerceptionEvent, PerceptionEventType

        event = PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            source="voice_hotkey",
            importance=0.8,
            payload={
                "text": "帮我查一下文档",
                "raw": "科特帮我查一下文档完毕",
                "language": "zh",
                "mode": "hotkey",
            },
        )

        print(f"\n【事件创建测试】")
        print(f"  事件类型: {event.event_type}")
        print(f"  来源: {event.source}")
        print(f"  文本: {event.payload['text']}")

        assert event.event_type == "speech.detected"
        assert event.payload["text"] == "帮我查一下文档"

    def test_event_format_description(self):
        """测试事件描述格式化"""
        from modules.perception.integration import PerceptionIntegrator

        # 测试 speech.detected 事件格式化
        description = PerceptionIntegrator._format_description(
            "speech.detected",
            {"text": "帮我查一下文档", "raw": "科特帮我查一下文档完毕"}
        )

        print(f"\n【事件格式化测试】")
        print(f"  格式化结果: {description}")

        assert description == "语音: 帮我查一下文档"

    def test_pool_add_and_snapshot(self):
        """测试感知池添加和快照"""
        from modules.perception.pool import PerceptionPool

        pool = PerceptionPool(max_items=10, ttl_seconds=30.0)

        # 添加语音事件
        pool.add(
            event_type="speech.detected",
            source="voice_hotkey",
            description="语音: 帮我查一下文档",
            payload={"text": "帮我查一下文档"}
        )

        # 添加其他事件
        pool.add(
            event_type="screen.window",
            source="window",
            description="当前窗口: PyCharm - Settings.vue",
            payload={"app_name": "PyCharm", "window_title": "Settings.vue"}
        )

        # 获取快照
        snapshot = pool.snapshot(max_items=5)

        print(f"\n【感知池快照测试】")
        print(f"  来源: {snapshot.source}")
        print(f"  内容: {snapshot.content[:100]}...")
        print(f"  目标角色: {snapshot.target_roles}")

        assert snapshot.source == "perception"
        assert "语音" in snapshot.content or "窗口" in snapshot.content

    def test_context_injection(self):
        """测试上下文注入"""
        from modules.thinking.context.pool import ContextFragment

        # 模拟感知池快照
        frag = ContextFragment(
            source="perception",
            content="【窗口状态】\n当前窗口: PyCharm - Settings.vue\n\n【语音】\n语音: 帮我查一下文档",
            target_roles=("orchestrator", "large"),
            section_title="环境感知",
            priority=5,
        )

        print(f"\n【上下文注入测试】")
        print(f"  片段来源: {frag.source}")
        print(f"  片段内容:\n{frag.content}")
        print(f"  目标角色: {frag.target_roles}")

        # 验证：应该包含语音和窗口信息
        assert "语音" in frag.content
        assert "窗口" in frag.content

    def test_full_flow_simulation(self):
        """模拟完整流程：事件创建 → 格式化 → 池存储 → 快照输出"""
        from modules.perception.events.types import PerceptionEvent, PerceptionEventType
        from modules.perception.pool import PerceptionPool
        from modules.perception.integration import PerceptionIntegrator

        # 1. 创建事件
        event = PerceptionEvent(
            event_type=PerceptionEventType.SPEECH_DETECTED,
            source="voice_hotkey",
            importance=0.8,
            payload={"text": "帮我查一下文档", "raw": "科特帮我查一下文档完毕"}
        )

        # 2. 格式化描述
        integrator = PerceptionIntegrator()
        description = integrator._format_description(event.event_type, event.payload)

        # 3. 添加到池
        integrator.pool.add(
            event_type=event.event_type,
            source=event.source,
            description=description,
            payload=event.payload
        )

        # 4. 获取快照
        snapshot = integrator.pool.snapshot(max_items=5)

        print(f"\n【完整流程模拟测试】")
        print(f"  1. 事件创建: {event.event_type}")
        print(f"  2. 格式化: {description}")
        print(f"  3. 池存储: {len(integrator.pool._items)} 条")
        print(f"  4. 快照输出:\n{snapshot.content}")

        # 验证：完整流程应该成功
        assert description == "语音: 帮我查一下文档"
        assert len(integrator.pool._items) == 1

        # 验证：快照应该包含语音内容
        assert "语音" in snapshot.content, f"快照应包含语音内容，实际: {snapshot.content!r}"
        print(f"  5. 快照包含语音: ✓")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
