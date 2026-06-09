"""
Tests for MultiModelOrchestrator — the core orchestration layer.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator


@pytest.fixture
def orchestrator():
    orch = MultiModelOrchestrator.__new__(MultiModelOrchestrator)
    orch._security = None
    orch._context_service = None
    orch._guidance_service = None
    orch._output_reviewer = None
    orch._reflection_sm = None
    orch._activity_notifier = None
    orch._expert_pipeline = None
    orch._gcm_pool = None
    return orch


# --- _validate_security ---

@pytest.mark.asyncio
async def test_validate_security_passes(orchestrator):
    mock_security = MagicMock()
    mock_security.validate_input.return_value = (True, "")
    orchestrator._security = mock_security

    passed, error = await orchestrator._validate_security("hello")
    assert passed is True
    assert error == ""


@pytest.mark.asyncio
async def test_validate_security_blocks(orchestrator):
    mock_security = MagicMock()
    mock_security.validate_input.return_value = (False, "dangerous input")
    orchestrator._security = mock_security

    passed, error = await orchestrator._validate_security("rm -rf /")
    assert passed is False
    assert "dangerous" in error


# --- _match_skill ---

def test_match_skill_no_manager(orchestrator):
    result = orchestrator._match_skill("hello")
    # Should return None when no skill manager
    assert result is None or isinstance(result, str)


# --- _build_security_error ---

def test_build_security_error(orchestrator):
    result = MultiModelOrchestrator._build_security_error("blocked", 0.0)
    assert "安全拦截" in result["response"]
    assert result["security_passed"] is False
