"""chat_light 技能注入测试：会话页显式选择的技能 → 全文注入 system prompt。

覆盖用户可见行为：
- 传 skill_id 且技能存在 → system_prompt 追加技能全文 + 推送【技能注入】思考事件
- 不传 / 传空 → 不注入（自动关键词匹配已废弃，只发建议）
- 传不存在的 skill_id → 静默降级，不注入也不报错
- SKILLS_ENABLED=False → 总开关关闭时不注入
- 技能 enabled=False → 不注入

回归要点：get_capability 返回的是工厂 callable，必须调用后才拿到 SkillManager。
早期实现直接把它当管理器用，异常被 except 吞掉，导致注入静默失效（prompt 长度无变化）。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.thinking.chat_light.continuous_thinker import ContinuousThinker


def _thinker():
    t = ContinuousThinker.__new__(ContinuousThinker)
    t._session_locks = {}
    t._session_locks_guard = MagicMock()
    t._runner = MagicMock()
    t._slicer = MagicMock()
    t._composer = MagicMock()
    return t


def _wire(t, captured):
    """把 thinker 的依赖替换成最小桩，并捕获最终 system_prompt。"""
    t._recall_memories = AsyncMock(return_value="")
    t._slicer.slice = AsyncMock(return_value=[{"role": "user", "content": "hi"}])
    t._composer.build_system = MagicMock(return_value="BASE")

    response = MagicMock()
    response.message.reasoning_content = ""
    response.message.content = "ok"

    async def fake_run(messages, system_prompt, on_token=None, max_tokens=None, temperature=None):
        captured["sp"] = system_prompt
        return response

    t._runner.run = fake_run


def _skill(skill_id="ui_design", name="UI 技能", enabled=True, body="# 技能正文\n要求：务必如此。"):
    s = MagicMock()
    s.id = skill_id
    s.name = name
    s.enabled = enabled
    s.to_prompt_block = MagicMock(return_value=body)
    return s


def _patch_manager(monkeypatch, skill):
    """模拟 bootstrap 注册：get_capability 返回工厂，工厂调用后返回管理器。"""
    mgr = MagicMock()
    mgr._loaded = True
    mgr.get_skill = MagicMock(return_value=skill)

    import infra.tool_manager.service_registry as sr
    monkeypatch.setattr(sr, "get_capability", lambda name: (lambda: mgr))
    return mgr


async def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


async def test_skill_id_injects_full_text(monkeypatch):
    t = _thinker()
    captured = {}
    _wire(t, captured)
    skill = _skill()
    _patch_manager(monkeypatch, skill)

    import modules.thinking.chat_light.continuous_thinker as ct
    monkeypatch.setattr(ct.settings, "SKILLS_ENABLED", True)

    q = asyncio.Queue()
    await t.think("s1", "hi", q, skill_id="ui_design")

    assert "# 技能正文" in captured["sp"]
    assert captured["sp"].startswith("BASE")
    skill.to_prompt_block.assert_called_once()
    thinking = [i["content"] for i in await _drain(q) if i["type"] == "thinking"]
    assert any("技能注入" in c and "UI 技能" in c for c in thinking)


async def test_no_skill_id_does_not_inject(monkeypatch):
    t = _thinker()
    captured = {}
    _wire(t, captured)
    skill = _skill()
    _patch_manager(monkeypatch, skill)

    import modules.thinking.chat_light.continuous_thinker as ct
    monkeypatch.setattr(ct.settings, "SKILLS_ENABLED", True)

    q = asyncio.Queue()
    await t.think("s1", "hi", q, skill_id="")

    # 心理活动/环境感知会追加到 prompt，故断言「技能正文未出现」而非全等
    assert "# 技能正文" not in captured["sp"]
    skill.to_prompt_block.assert_not_called()
    assert not any("技能注入" in i.get("content", "") for i in await _drain(q))


async def test_unknown_skill_id_degrades_silently(monkeypatch):
    t = _thinker()
    captured = {}
    _wire(t, captured)
    mgr = _patch_manager(monkeypatch, None)
    mgr.get_skill = MagicMock(return_value=None)

    import modules.thinking.chat_light.continuous_thinker as ct
    monkeypatch.setattr(ct.settings, "SKILLS_ENABLED", True)

    q = asyncio.Queue()
    await t.think("s1", "hi", q, skill_id="does_not_exist")

    assert "# 技能正文" not in captured["sp"]
    assert not any(i["type"] == "error" for i in await _drain(q))


async def test_disabled_skill_not_injected(monkeypatch):
    t = _thinker()
    captured = {}
    _wire(t, captured)
    skill = _skill(enabled=False)
    _patch_manager(monkeypatch, skill)

    import modules.thinking.chat_light.continuous_thinker as ct
    monkeypatch.setattr(ct.settings, "SKILLS_ENABLED", True)

    q = asyncio.Queue()
    await t.think("s1", "hi", q, skill_id="ui_design")

    assert "# 技能正文" not in captured["sp"]
    skill.to_prompt_block.assert_not_called()


async def test_skills_disabled_switch_blocks_injection(monkeypatch):
    t = _thinker()
    captured = {}
    _wire(t, captured)
    skill = _skill()
    _patch_manager(monkeypatch, skill)

    import modules.thinking.chat_light.continuous_thinker as ct
    monkeypatch.setattr(ct.settings, "SKILLS_ENABLED", False)

    q = asyncio.Queue()
    await t.think("s1", "hi", q, skill_id="ui_design")

    assert "# 技能正文" not in captured["sp"]
    skill.to_prompt_block.assert_not_called()


async def test_manager_factory_is_called(monkeypatch):
    """回归：get_capability 返回工厂，必须调用它才拿到管理器（否则注入静默失效）。"""
    t = _thinker()
    captured = {}
    _wire(t, captured)
    skill = _skill()

    mgr = MagicMock()
    mgr._loaded = True
    mgr.get_skill = MagicMock(return_value=skill)
    factory = MagicMock(return_value=mgr)

    import infra.tool_manager.service_registry as sr
    _real = sr.get_capability
    # 只替换 skill_manager：其余能力（conscience 等）也走同一个 get_capability，
    # 一并替换会让它们也调用这个工厂，计数就不再是「技能管理器被调用几次」。
    monkeypatch.setattr(
        sr, "get_capability",
        lambda name: factory if name == "skill_manager" else _real(name),
    )

    import modules.thinking.chat_light.continuous_thinker as ct
    monkeypatch.setattr(ct.settings, "SKILLS_ENABLED", True)

    q = asyncio.Queue()
    await t.think("s1", "hi", q, skill_id="ui_design")

    factory.assert_called_once()
    assert "# 技能正文" in captured["sp"]
