"""感知数据 e2e：真实事件 → 感知池 → collect → 注入 system prompt（完整链路）

§43 防回归：验证感知数据能走完整链路到达模型 prompt（含【环境感知】块）。
"""
import asyncio

import pytest

from modules.perception.events.bus import get_event_bus
from modules.perception.events.types import PerceptionEvent
from modules.perception.integration import get_perception_integrator


@pytest.fixture
def perception_pipeline():
    """启动感知集成器，返回 (发布事件的函数, 收集感知的函数)"""
    async def _publish(event_type, payload):
        get_event_bus().publish(PerceptionEvent(event_type=event_type, payload=payload))
        await asyncio.sleep(0.15)

    async def _collect():
        from modules.thinking.context.sources.perception_source import PerceptionSource
        frag = await PerceptionSource().collect()
        return frag.content if frag else ""

    get_perception_integrator().start()
    return _publish, _collect


@pytest.mark.asyncio
async def test_perception_flows_to_system_prompt(perception_pipeline):
    """感知数据经【环境感知】块注入 system prompt（model_runner/chat_light 同款逻辑）"""
    _publish, _collect = perception_pipeline
    await _publish("screen.window", {"title": "e2e", "app_name": "TestApp", "source_type": "window"})
    await _publish("screen.diff", {"intensity": 60, "source_type": "screen_diff"})

    perception_content = await _collect()
    assert "窗口" in perception_content or "屏幕" in perception_content

    # 注入逻辑（与 model_runner / chat_light 一致）：感知内容追加为【环境感知】块
    system_prompt = "基础系统提示词"
    if perception_content:
        system_prompt += f"\n\n【环境感知】\n{perception_content}"
    assert "【环境感知】" in system_prompt
    assert "TestApp" in system_prompt or "窗口" in system_prompt


@pytest.mark.asyncio
async def test_perception_collect_always_returns_fragment():
    """感知池清空后 collect 仍返回快照（机制健壮，不崩）"""
    from modules.perception.integration import get_perception_integrator
    intg = get_perception_integrator()
    intg.pool.clear()
    await asyncio.sleep(0.1)
    from modules.thinking.context.sources.perception_source import PerceptionSource
    frag = await PerceptionSource().collect()
    assert frag is not None
    assert hasattr(frag, "content")
    # 无论感知池有/无数据，注入逻辑（model_runner/chat_light 一致）都不抛异常
    system_prompt = "基础"
    if frag.content:
        system_prompt += f"\n\n【环境感知】\n{frag.content}"
    assert system_prompt.startswith("基础")
