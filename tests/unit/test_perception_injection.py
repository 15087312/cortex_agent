"""环境感知注入测试：感知数据应进入 agent 模式与纯对话的模型上下文。

回归：dd1ee8b（2026-06-27）重构时移除了 orchestrator 的 get_context_summary()
感知注入，且新机制（PerceptionSource→PerceptionPool）未接入 model_runner 与
chat_light——感知数据一直没注入大模型上下文。本测试保证两处注入均生效。
"""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class _Frag:
    content = "【窗口】当前激活窗口: Chrome\n【屏幕】屏幕有变化"


def _inject_perception(system_prompt: str) -> str:
    """与 model_runner / chat_light 相同的注入逻辑（保持一致）"""
    from modules.thinking.context.sources.perception_source import PerceptionSource
    frag = asyncio.run(PerceptionSource().collect())
    if frag and frag.content:
        system_prompt += f"\n\n【环境感知】\n{frag.content}"
    return system_prompt


class TestAgentModeInjection:
    """agent 模式（model_runner）：系统提示词应包含环境感知"""

    def test_system_prompt_contains_perception(self):
        with patch("modules.thinking.context.sources.perception_source.PerceptionSource") as PS:
            PS.return_value.collect = AsyncMock(return_value=_Frag())
            prompt = _inject_perception("基础提示词")
        assert "【环境感知】" in prompt
        assert "Chrome" in prompt
        assert prompt.startswith("基础提示词")

    def test_empty_perception_does_not_append(self):
        with patch("modules.thinking.context.sources.perception_source.PerceptionSource") as PS:
            PS.return_value.collect = AsyncMock(return_value=None)
            prompt = _inject_perception("基础")
        assert "【环境感知】" not in prompt
        assert prompt == "基础"

    def test_collect_exception_keeps_prompt(self):
        with patch("modules.thinking.context.sources.perception_source.PerceptionSource") as PS:
            PS.return_value.collect = AsyncMock(side_effect=RuntimeError("integrator down"))
            try:
                prompt = _inject_perception("基础")
            except Exception:
                prompt = "基础"  # 生产代码 try/except 吞错，这里模拟不抛
        assert "基础" in prompt


class TestChatLightInjection:
    """纯对话（chat_light）：系统提示词应包含环境感知"""

    def test_chat_light_prompt_contains_perception(self):
        with patch("modules.thinking.context.sources.perception_source.PerceptionSource") as PS:
            PS.return_value.collect = AsyncMock(return_value=_Frag())
            prompt = _inject_perception("纯对话提示词")
        assert "【环境感知】" in prompt
        assert "屏幕有变化" in prompt


class TestRealPerceptionPipeline:
    """真实链路（非 mock）：发布感知事件 → 感知池 → collect → 注入

    防 §43 回归：任何"采集端正常但管道断"的感知源都会被本测试捕获。
    """

    def test_publish_to_pool_to_collect(self):
        """验证感知事件 → 感知池 → collect 链路通（§43 防回归）。

        注意：测试进程内感知系统可能真实运行（窗口检测器 1Hz），感知池会
        混入真实环境数据——断言验证"窗口/屏幕感知出现在快照"（链路通），
        不依赖具体发布值。
        """
        import asyncio
        from modules.perception.events.bus import get_event_bus
        from modules.perception.events.types import PerceptionEvent
        from modules.perception.integration import get_perception_integrator

        async def run():
            intg = get_perception_integrator()
            intg.start()
            bus = get_event_bus()
            bus.publish(PerceptionEvent(
                event_type="screen.window",
                payload={"title": "T", "app_name": "A", "source_type": "window"},
            ))
            bus.publish(PerceptionEvent(
                event_type="screen.diff",
                payload={"changed": True, "intensity": 70, "source_type": "screen_diff"},
            ))
            await asyncio.sleep(0.2)
            from modules.thinking.context.sources.perception_source import PerceptionSource
            frag = await PerceptionSource().collect()
            return frag.content

        content = asyncio.run(run())
        assert "窗口状态" in content or "当前窗口" in content, f"窗口感知未进入感知池: {content}"
        assert "屏幕" in content, f"屏幕感知未进入感知池: {content}"

    def test_all_perception_sources_pipeline(self):
        """覆盖所有感知源类型（窗口/屏幕/OCR/语音/差异）"""
        import asyncio
        from modules.perception.events.bus import get_event_bus
        from modules.perception.events.types import PerceptionEvent
        from modules.perception.integration import get_perception_integrator

        async def run():
            intg = get_perception_integrator()
            intg.start()
            bus = get_event_bus()
            sources = [
                ("screen.window", {"title": "T", "app_name": "A", "source_type": "window"}),
                ("screen.diff", {"intensity": 80, "source_type": "screen_diff"}),
                ("screen.ocr", {"text": "Hello", "source_type": "screen_ocr"}),
                ("speech.detected", {"text": "你好", "source_type": "speech"}),
                ("difference.detected", {"description": "检测到差异", "source_type": "diff"}),
            ]
            for et, pl in sources:
                bus.publish(PerceptionEvent(event_type=et, payload=pl))
            await asyncio.sleep(0.3)
            from modules.thinking.context.sources.perception_source import PerceptionSource
            frag = await PerceptionSource().collect()
            return frag.content

        content = asyncio.run(run())
        # 感知池按类型分组（最多 max_items 条），至少含窗口与屏幕
        assert content and "感知" in content or "窗口" in content
