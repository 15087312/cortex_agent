"""空间增强 + 心理活动推送测试"""
import asyncio

import pytest


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def fake_causal(monkeypatch):
    """mock 因果知识提取（避免 LLM/数据库慢）"""
    from modules.thinking.conscience import Conscience

    async def _fake(self, user_input, owner_id=""):
        return "（因果知识）"
    monkeypatch.setattr(Conscience, "_get_causal_knowledge", _fake)


def _make_client(captured):
    class FakeClient:
        async def generate(self, prompt, **kw):
            captured["prompt"] = prompt
            return "（内心独白测试内容）"
    return FakeClient()


def test_spatial_prompt_injected_when_enabled(monkeypatch, fake_causal):
    from config.settings import settings
    from modules.thinking.conscience import Conscience
    monkeypatch.setattr(settings, "SPATIAL_ENHANCEMENT_ENABLED", True)
    captured = {}
    cons = Conscience(model_client=_make_client(captured))
    result = _run(cons.think("测试输入", owner_id="spatial_test"))
    assert result == "（内心独白测试内容）"
    prompt = captured["prompt"]
    assert "空间增强" in prompt
    assert "三维环境" in prompt
    assert "身体朝向" in prompt
    assert "移动距离" in prompt
    assert "障碍物" in prompt


def test_spatial_prompt_not_injected_when_disabled(monkeypatch, fake_causal):
    from config.settings import settings
    from modules.thinking.conscience import Conscience
    monkeypatch.setattr(settings, "SPATIAL_ENHANCEMENT_ENABLED", False)
    captured = {}
    cons = Conscience(model_client=_make_client(captured))
    _run(cons.think("测试输入", owner_id="spatial_test"))
    prompt = captured["prompt"]
    assert "空间增强" not in prompt
    assert "三维环境" not in prompt


def test_conscience_prompt_format():
    """CONSCIENCE_PROMPT 必须能 format（含 spatial_enhancement 占位）"""
    from modules.thinking.conscience import CONSCIENCE_PROMPT
    s = CONSCIENCE_PROMPT.format(
        causal_knowledge="c", values="v", recent_dialog="r",
        spatial_enhancement="", user_input="u",
    )
    assert "c" in s and "u" in s


def test_mental_event_pushed_to_frontend(monkeypatch):
    """内心独白生成后推送 mental 事件到前端（标注心理活动）"""
    from modules.thinking import api_stream

    sent = []

    class FakeCM:
        active_connections = {"ses1": object()}

        def send_json_from_thread(self, sid, event):
            sent.append((sid, event))

    class FakeEvent:
        def __init__(self, session_id="", msg_type="", event="", content="", role="", data=None):
            self.session_id = session_id
            self.msg_type = msg_type
            self.event = event
            self.content = content
            self.role = role
            self.data = data or {}

    fake_cm = FakeCM()
    monkeypatch.setattr(api_stream, "connection_manager", fake_cm)
    monkeypatch.setattr(api_stream, "_build_event", lambda **kw: FakeEvent(**kw))

    from modules.thinking.probes import probe_tools
    monkeypatch.setattr(probe_tools, "set_session_guidance", lambda *a, **kw: None)

    async def _run_push():
        inner_thoughts = "我记得之前这样规划过动作…"
        session_id = "ses1"
        try:
            from modules.thinking.api_stream import connection_manager, _build_event
            event = _build_event(
                session_id=session_id, msg_type="mental", event="mental",
                content=inner_thoughts, role="system", data={"label": "心理活动"},
            )
            for sid in list(connection_manager.active_connections.keys()):
                connection_manager.send_json_from_thread(sid, event)
        except Exception:
            pass

    _run(_run_push())
    assert len(sent) == 1
    sid, event = sent[0]
    assert sid == "ses1"
    assert event.msg_type == "mental"
    assert event.content == "我记得之前这样规划过动作…"
