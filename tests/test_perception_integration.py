"""
感知集成测试 — 验证事件总线 → PerceptionIntegrator → PerceptionPool 链路

覆盖:
- 事件总线接收并分发 SCREEN_DIFF 事件到 handler
- PerceptionPool 条目增长、MD5 去重、max_items 上限
- PerceptionPool.snapshot() 在收到可入段事件后返回非空分组摘要
- 无事件时 snapshot() 返回空
- 多事件按 event_type 分组输出（【窗口状态】/【屏幕文本】等）
"""
import pytest
from unittest.mock import MagicMock

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from modules.perception.events.bus import PerceptionEventBus
from modules.perception.integration import PerceptionIntegrator


class TestEventBusToIntegrator:
    """事件总线 → PerceptionIntegrator 链路测试"""

    def setup_method(self):
        # 每次测试用独立实例，避免全局单例污染
        self.bus = PerceptionEventBus()
        self.integrator = PerceptionIntegrator()

    def test_publish_screen_diff_event(self):
        """发布 SCREEN_DIFF 事件后，integrator 能收到"""
        # 模拟订阅事件
        self.integrator._on_perception_event = MagicMock()
        handler = self.integrator._on_perception_event
        self.bus.subscribe(PerceptionEventType.SCREEN_DIFF, handler)

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_DIFF,
            source="screen_diff_mcp",
            importance=0.6,
            payload={
                "change_ratio": 0.15,
                "changed_regions": [{"x": 10, "y": 20, "w": 100, "h": 50}],
                "width": 1920, "height": 1080,
            },
        )
        self.bus.publish(event)

        handler.assert_called_once()
        args = handler.call_args[0][0]
        assert args.event_type == "screen.diff"
        assert args.payload["change_ratio"] == 0.15

    def test_attention_items_populated(self):
        """收到事件后 pool 中新增条目"""
        self.integrator._on_perception_event(
            PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_DIFF,
                source="screen_diff_mcp",
                payload={"change_ratio": 0.15, "changed_regions": []},
            )
        )
        assert len(self.integrator.pool._items) >= 1
        item = self.integrator.pool._items[-1]
        assert "变化" in item["description"]

    def test_attention_items_dedup(self):
        """相同描述不重复添加（MD5 hash 去重）"""
        for i in range(5):
            self.integrator._on_perception_event(
                PerceptionEvent(
                    event_type=PerceptionEventType.SCREEN_DIFF,
                    source="screen_diff_mcp",
                    payload={
                        "change_ratio": 0.1,
                        "changed_regions": [],
                    },
                )
            )
        # 相同的 description 只应该添加一次
        assert len(self.integrator.pool._items) == 1

    def test_different_descriptions_all_added(self):
        """不同描述的变化事件各自添加"""
        for ratio in [0.1, 0.25, 0.50]:
            self.integrator._on_perception_event(
                PerceptionEvent(
                    event_type=PerceptionEventType.SCREEN_DIFF,
                    source="screen_diff_mcp",
                    payload={
                        "change_ratio": ratio,
                        "changed_regions": [],
                    },
                )
            )
        assert len(self.integrator.pool._items) == 3

    def test_context_summary_non_empty_after_event(self):
        """收到事件后 snapshot() 返回非空 (window 事件可进 snapshot)"""
        self.integrator._on_perception_event(
            PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_WINDOW,
                source="window_detector",
                payload={"app_name": "Terminal", "window_title": "bash"},
            )
        )
        summary = self.integrator.pool.snapshot().content
        assert summary != ""
        assert "环境感知" in summary or "【窗口状态】" in summary

    def test_context_summary_empty_without_events(self):
        """无事件时 snapshot() 返回空"""
        self.integrator.pool.clear()
        assert self.integrator.pool.snapshot().content == ""

    def test_attention_items_capped(self):
        """pool._items 受 max=20 限制"""
        for i in range(50):
            # 每个事件不同 payload 确保不同 description
            self.integrator._on_perception_event(
                PerceptionEvent(
                    event_type=PerceptionEventType.SCREEN_DIFF,
                    source="screen_diff_mcp",
                    payload={
                        "change_ratio": 0.01 + i * 0.002,
                        "changed_regions": [],
                    },
                )
            )
        assert len(self.integrator.pool._items) <= 20

    def test_context_summary_groups_event_types(self):
        """事件按类型分组输出 (window→【窗口状态】, ocr→【屏幕文本】)"""
        self.integrator._on_perception_event(
            PerceptionEvent(event_type=PerceptionEventType.SCREEN_WINDOW,
                            source="window_detector",
                            payload={"app_name": "Terminal", "window_title": "bash"})
        )
        self.integrator._on_perception_event(
            PerceptionEvent(event_type=PerceptionEventType.SCREEN_OCR,
                            source="ocr",
                            payload={"new_lines": ["hello world"]})
        )
        summary = self.integrator.pool.snapshot().content
        assert "【环境感知】" in summary
        assert "【窗口状态】" in summary
        assert "【屏幕文本】" in summary

    def test_context_summary_starts_with_perception_header(self):
        """收到事件后摘要以【环境感知】为开头（用于 prompt 分段）"""
        self.integrator._on_perception_event(
            PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_WINDOW,
                source="window_detector",
                payload={"app_name": "Terminal", "window_title": "bash"},
            )
        )
        summary = self.integrator.pool.snapshot().content
        assert summary.startswith("【环境感知】")

    def test_description_contains_screen_and_change_keywords(self):
        """SCREEN_DIFF 事件生成的描述必须包含"屏幕"和"变化"关键词"""
        self.integrator._on_perception_event(
            PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_DIFF,
                source="screen_diff_mcp",
                importance=0.6,
                payload={
                    "change_ratio": 0.15,
                    "changed_regions": [{"x": 10, "y": 20, "w": 200, "h": 100}],
                },
            )
        )
        assert len(self.integrator.pool._items) == 1
        desc = self.integrator.pool._items[0]["description"]
        assert "屏幕" in desc
        assert "变化" in desc

    def test_window_summary_includes_app_name(self):
        """SCREEN_WINDOW 事件的摘要应在【窗口状态】区域出现 app_name"""
        self.integrator._on_perception_event(
            PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_WINDOW,
                source="window",
                payload={
                    "app_name": "Terminal",
                    "window_title": "bash",
                    "prev_app": "Safari",
                    "prev_window": "Google",
                },
            )
        )
        summary = self.integrator.pool.snapshot().content
        assert "【窗口状态】" in summary
        assert "Terminal" in summary


class TestScreenDiffSourcePublishesEvents:
    """ScreenDiffSource 发布事件的 mock 验证"""

    def test_screen_diff_source_imports(self):
        """ScreenDiffSource 能正常导入（语法/依赖验证）"""
        from modules.perception.difference.sources.mcp_screen_source import (
            ScreenDiffSource, get_screen_diff_source, _PERCEPTION_EVENT_IDLE_INTERVAL
        )
        assert hasattr(ScreenDiffSource, "_publish_screen_diff_event")
        assert _PERCEPTION_EVENT_IDLE_INTERVAL == 2.0
        assert hasattr(ScreenDiffSource, "source_type")

    def test_screen_diff_source_can_register_to_registry(self):
        """ScreenDiffSource 可注册到 detector registry"""
        from modules.perception.difference.sources.mcp_screen_source import ScreenDiffSource
        from modules.perception.difference.sources.base import DifferenceSourceRegistry
        registry = DifferenceSourceRegistry()
        src = ScreenDiffSource()
        registry.register(src)
        assert "screen" in registry.registered_types

    def test_screen_diff_source_enable_disable(self):
        """ScreenDiffSource.enabled 标识可正常切换"""
        from modules.perception.difference.sources.mcp_screen_source import ScreenDiffSource
        src = ScreenDiffSource()
        assert src.enabled is True
        src.enabled = False
        assert src.enabled is False
        src.enabled = True
        assert src.enabled is True
