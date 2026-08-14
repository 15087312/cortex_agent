"""probe_tools / adapters 扩展测试：深度回忆 / 中途回复 / 端口适配器"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import modules.thinking.probes.probe_tools as pt
import modules.thinking.adapters as ad


# ── probe_tools: set_session_guidance ──────────────────────────────────

def test_set_session_guidance():
    pt.set_session_guidance("s1", {"inner_thoughts": "引导"}, model_id="m1")
    assert pt._session_guidance[("m1", "s1")] == {"inner_thoughts": "引导"}


# ── probe_tools: deep_recall ───────────────────────────────────────────

def test_deep_recall_success(monkeypatch):
    scheduler = MagicMock()
    result = MagicMock()
    result.success = True
    result.fallback = False
    result.error = ""
    result.causal_chains = [1, 2]
    result.supporting_events = [1, 2, 3]
    scheduler.deep_recall = AsyncMock(return_value=result)
    monkeypatch.setattr("modules.memory.depth_recall.DepthRecallScheduler", lambda: scheduler)
    monkeypatch.setattr("modules.memory.result_fusion.format_deep_recall_result", lambda r, max_events=5: "格式化结果")

    import asyncio as _a
    loop = _a.new_event_loop()
    monkeypatch.setattr(_a, "get_event_loop", lambda: loop)
    out = pt.deep_recall("项目延期原因")
    assert out["success"] is True
    assert out["causal_chains"] == 2
    assert out["supporting_events"] == 3
    loop.close()


def test_deep_recall_failure(monkeypatch):
    scheduler = MagicMock()
    result = MagicMock()
    result.success = False
    result.fallback = True
    result.error = "no_anchor"
    scheduler.deep_recall = AsyncMock(return_value=result)
    monkeypatch.setattr("modules.memory.depth_recall.DepthRecallScheduler", lambda: scheduler)
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    out = pt.deep_recall("无关")
    assert out["success"] is False
    assert "no_anchor" in out["error"]
    loop.close()


def test_deep_recall_exception(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr("modules.memory.depth_recall.DepthRecallScheduler", boom)
    out = pt.deep_recall("q")
    assert out["success"] is False


# ── probe_tools: request_intermediate_response ─────────────────────────

def test_intermediate_no_thought(monkeypatch):
    monkeypatch.setattr("modules.thinking.multi_model_orchestrator.get_active_sessions", lambda: [])
    out = pt.request_intermediate_response()
    assert out["success"] is False


def test_intermediate_with_thought(monkeypatch):
    long_content = "【步骤】\n\n这是一段超过二十个字符的思考内容段落内容内容内容内容内容。"
    entries = [{"type": "thought", "content": long_content}]
    bb = MagicMock()
    bb.read_dialog = MagicMock(return_value=entries)
    monkeypatch.setattr(
        "modules.thinking.multi_model_orchestrator.get_active_sessions",
        lambda: [{"blackboard": bb}],
    )
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="截断内容")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    out = pt.request_intermediate_response(max_length=100, _caller_role="expert")
    assert out["success"] is True
    assert "截断内容" in out["content"]


def test_intermediate_send_fails(monkeypatch):
    long_content = "【步骤】\n\n这是一段超过二十个字符的思考内容段落内容内容内容内容内容。"
    entries = [{"type": "thought", "content": long_content}]
    bb = MagicMock()
    bb.read_dialog = MagicMock(return_value=entries)
    monkeypatch.setattr(
        "modules.thinking.multi_model_orchestrator.get_active_sessions",
        lambda: [{"blackboard": bb}],
    )
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="截断")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)

    def boom(*a, **k):
        raise RuntimeError("bus down")
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", boom)
    out = pt.request_intermediate_response()
    assert out["success"] is True  # 总线失败不阻断返回


def test_intermediate_without_running_loop(monkeypatch):
    long_content = "【步骤】\n\n这是一段超过二十个字符的思考内容段落内容内容内容内容内容。"
    entries = [{"type": "thought", "content": long_content}]
    bb = MagicMock()
    bb.read_dialog = MagicMock(return_value=entries)
    monkeypatch.setattr(
        "modules.thinking.multi_model_orchestrator.get_active_sessions",
        lambda: [{"blackboard": bb}],
    )
    engine = MagicMock()
    engine._truncate_to_tokens = MagicMock(return_value="截断")
    monkeypatch.setattr("modules.thinking.context.compression.get_compression_engine", lambda: engine)
    bus = MagicMock()
    bus.send = AsyncMock()
    monkeypatch.setattr("modules.thinking.communication.message_bus.get_message_bus", lambda: bus)
    out = pt.request_intermediate_response()
    assert out["success"] is True


# ── adapters ───────────────────────────────────────────────────────────

def test_difference_notifier_ok(monkeypatch):
    n = ad.DifferenceDetectorActivityNotifier()
    det = MagicMock()
    monkeypatch.setattr("modules.perception.difference.get_detector", lambda: det)
    ps = MagicMock()
    ps.proactive_trigger = MagicMock()
    monkeypatch.setattr("modules.perception.setup.get_perception_system", lambda: ps)
    n.notify_activity()
    det.notify_activity.assert_called_once()
    ps.proactive_trigger.notify_activity.assert_called_once()


def test_difference_notifier_error(monkeypatch):
    n = ad.DifferenceDetectorActivityNotifier()
    monkeypatch.setattr("modules.perception.difference.get_detector", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    monkeypatch.setattr("modules.perception.setup.get_perception_system", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    n.notify_activity()  # 不抛异常


def test_security_adapter_ok(monkeypatch):
    api = MagicMock()
    api.validate_input = MagicMock(return_value=(True, ""))
    monkeypatch.setattr("modules.security_system.api.get_security_api", lambda: api)
    a = ad.SecurityApiAdapter()
    assert a.validate_input("hi") == (True, "")


def test_security_adapter_error(monkeypatch):
    monkeypatch.setattr("modules.security_system.api.get_security_api", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    a = ad.SecurityApiAdapter()
    ok, err = a.validate_input("hi")
    assert ok is False
    assert "异常" in err


async def test_guidance_adapter(monkeypatch):
    cons = MagicMock()
    cons.think = AsyncMock(return_value="内心独白")
    client = MagicMock()
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: cons)
    monkeypatch.setattr("infra.model.small_model_client.SmallModelClient", lambda **k: client)
    import sys, types
    cfg = sys.modules["config.settings"]
    monkeypatch.setattr(cfg, "settings", types.SimpleNamespace(
        SMALL_MODEL_API_KEY="k", LARGE_MODEL_API_KEY="lk",
        SMALL_MODEL_API_URL="u", LARGE_MODEL_API_URL="lu",
    ))
    a = ad.PreGenExpertGuidanceAdapter()
    out = await a.run("问题", owner_id="m1")
    assert out == {"inner_thoughts": "内心独白"}
    assert cons._model_client is client


async def test_guidance_adapter_error(monkeypatch):
    monkeypatch.setattr("modules.thinking.conscience.get_conscience", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    a = ad.PreGenExpertGuidanceAdapter()
    assert await a.run("q") == {}


async def test_output_review_ok(monkeypatch):
    monkeypatch.setattr("modules.output_system.core.OutputSystem.clean_response", staticmethod(lambda s: f"clean:{s}"))
    a = ad.OutputSystemReviewAdapter()
    assert await a.review("raw") == "clean:raw"


async def test_output_review_empty():
    a = ad.OutputSystemReviewAdapter()
    assert await a.review("") == ""


async def test_output_review_error(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("clean fail")
    monkeypatch.setattr("modules.output_system.core.OutputSystem.clean_response", staticmethod(boom))
    a = ad.OutputSystemReviewAdapter()
    assert await a.review("raw") == "raw"
