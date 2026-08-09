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


