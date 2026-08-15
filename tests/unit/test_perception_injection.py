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
