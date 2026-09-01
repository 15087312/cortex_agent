"""probe_tools 测试（此前 16% 覆盖）：会话引导写入与读取"""
import asyncio
from unittest.mock import MagicMock, patch

from modules.thinking.probes import probe_tools


def test_set_session_guidance_and_get(monkeypatch):
    monkeypatch.setattr(probe_tools, "_session_guidance", {})
    probe_tools.set_session_guidance("s1", {"a": "引导"}, model_id="large_primary")
    stored = probe_tools._session_guidance.get(("large_primary", "s1"))
    assert stored == {"a": "引导"}


def test_session_guidance_distinct_sessions(monkeypatch):
    monkeypatch.setattr(probe_tools, "_session_guidance", {})
    probe_tools.set_session_guidance("s1", {"a": "x"})
    probe_tools.set_session_guidance("s2", {"b": "y"})
    assert ("large_primary", "s1") in probe_tools._session_guidance
    assert ("large_primary", "s2") in probe_tools._session_guidance


def test_request_intermediate_response_no_thoughts(monkeypatch):
    import modules.thinking.multi_model_orchestrator as orc
    monkeypatch.setattr(orc, "get_active_sessions", lambda: [])
    result = probe_tools.request_intermediate_response(max_length=500, _caller_role="large")
    assert result.get("success") is False
    assert "暂无" in result.get("error", "")




def test_deep_recall_running_loop(monkeypatch):
    from types import SimpleNamespace

    import modules.memory.depth_recall as dr_mod
    import modules.memory.result_fusion as rf_mod

    result = SimpleNamespace(
        success=True, fallback=False,
        causal_chains=["c1"], supporting_events=["e1"], error="",
    )
    scheduler = MagicMock()
    scheduler.deep_recall.return_value = result
    monkeypatch.setattr(dr_mod, "DepthRecallScheduler", lambda: scheduler)
    monkeypatch.setattr(rf_mod, "format_deep_recall_result", lambda r, max_events=5: "formatted")
    loop = MagicMock()
    loop.is_running.return_value = True
    fut = MagicMock()
    fut.result.return_value = result
    loop.run_coroutine_threadsafe.return_value = fut
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", lambda coro, l: fut)
    out = probe_tools.deep_recall("q", depth_level=2, max_events=3)
    assert out["success"] is True
    assert out["result"] == "formatted"
    assert out["causal_chains"] == 1


def test_request_intermediate_inner_exception(monkeypatch):
    import modules.thinking.multi_model_orchestrator as orc

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(orc, "get_active_sessions", boom)
    result = probe_tools.request_intermediate_response()
    assert result.get("success") is False
    assert "暂无" in result.get("error", "")


def test_request_intermediate_with_thoughts(monkeypatch):
    import modules.thinking.multi_model_orchestrator as orc

    class FakeBB:
        def read_dialog(self, limit=None):
            return [
                {"type": "thought", "content": "短\n\n" + "这是一段足够长的思考内容" * 20},
            ]

    monkeypatch.setattr(orc, "get_active_sessions", lambda: [{"blackboard": FakeBB()}])
    result = probe_tools.request_intermediate_response(max_length=500, _caller_role="large")
    assert result.get("success") is True
    assert result["content"]
    assert "中途回复已发送" in result["message"]
