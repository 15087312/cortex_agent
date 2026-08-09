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


def test_deep_recall_success(monkeypatch):
    import modules.memory.depth_recall as dr_mod
    import modules.memory.result_fusion as rf_mod

    result = MagicMock()
    result.success = True
    result.fallback = False
    result.error = ""
    result.causal_chains = [1, 2]
    result.supporting_events = [1, 2, 3]

    scheduler = MagicMock()
    scheduler.deep_recall = MagicMock(return_value=asyncio.Future())
    scheduler.deep_recall.return_value.set_result(result)
    monkeypatch.setattr(dr_mod, "DepthRecallScheduler", lambda: scheduler)
    monkeypatch.setattr(rf_mod, "format_deep_recall_result", lambda r, max_events=5: "格式化结果")

    out = probe_tools.deep_recall("查询", depth_level=1, max_events=5)
    assert out["success"] is True
    assert out["causal_chains"] == 2


def test_deep_recall_not_found(monkeypatch):
    import modules.memory.depth_recall as dr_mod
    result = MagicMock()
    result.success = False
    result.fallback = False
    result.error = "未找到"
    scheduler = MagicMock()
    scheduler.deep_recall = MagicMock(return_value=asyncio.Future())
    scheduler.deep_recall.return_value.set_result(result)
    monkeypatch.setattr(dr_mod, "DepthRecallScheduler", lambda: scheduler)
    out = probe_tools.deep_recall("查询")
    assert out["success"] is False
    assert "未找到" in out["error"]


def test_deep_recall_exception(monkeypatch):
    import modules.memory.depth_recall as dr_mod
    def boom():
        raise RuntimeError("挂了")
    monkeypatch.setattr(dr_mod, "DepthRecallScheduler", boom)
    out = probe_tools.deep_recall("查询")
    assert out["success"] is False
