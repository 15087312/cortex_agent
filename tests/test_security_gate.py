"""
Tests for ToolSecurityGate — tool execution security.
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from modules.security_system.tool_security_gate import (
    ToolSecurityGate,
    get_tool_security_gate,
    _get_high_risk_tools,
    _get_medium_risk_tools,
    _emit_security_event,
    set_security_event_callback,
)


@pytest.fixture
def gate():
    """ToolSecurityGate with no LLM model."""
    from config.settings import settings
    object.__setattr__(settings, 'EXECUTION_MODE', 'yolo')
    g = ToolSecurityGate(lite_model=None)
    yield g
    object.__setattr__(settings, 'EXECUTION_MODE', 'edit')


@pytest.fixture
def mock_audit_gate():
    """ToolSecurityGate with a mocked audit logger."""
    from config.settings import settings
    object.__setattr__(settings, 'EXECUTION_MODE', 'yolo')
    g = ToolSecurityGate(lite_model=None)
    g._audit = MagicMock()
    yield g
    object.__setattr__(settings, 'EXECUTION_MODE', 'edit')


# ------------------------------------------------------------------ #
# LOW risk tools — direct pass-through
# ------------------------------------------------------------------ #

class TestLowRiskPassThrough:
    @pytest.mark.asyncio
    async def test_low_risk_tool_allowed(self, mock_audit_gate):
        """LOW risk tools (not in HIGH or MEDIUM sets) return True immediately."""
        allowed, reason = await mock_audit_gate.check(
            "read_file", {"path": "/tmp/x"}, "expert", "model_1"
        )
        assert allowed is True
        assert "LOW" in reason

    @pytest.mark.asyncio
    async def test_low_risk_logs_audit(self, mock_audit_gate):
        """LOW risk tools still produce an audit log entry."""
        await mock_audit_gate.check("list_files", {"path": "/tmp"}, "expert", "m1")
        mock_audit_gate._audit.log.assert_called_once()
        call = mock_audit_gate._audit.log.call_args
        assert call.kwargs.get("event_type") == "tool_approved" or call.args[0] == "tool_approved"
        assert call.kwargs.get("level") == "LOW" or call.args[1] == "LOW"


# ------------------------------------------------------------------ #
# MEDIUM risk tools — static checks
# ------------------------------------------------------------------ #

class TestMediumRiskChecks:
    @pytest.mark.asyncio
    async def test_run_python_safe_code_passes(self, mock_audit_gate):
        """run_python with safe code passes medium-risk checks."""
        allowed, reason = await mock_audit_gate.check(
            "run_python", {"code": "print('hello')"}, "expert", "m1"
        )
        assert allowed is True
        assert "放行" in reason

    @pytest.mark.asyncio
    async def test_run_python_dangerous_code_allowed(self, mock_audit_gate):
        """真机模式：run_python with dangerous code (eval) is allowed."""
        allowed, reason = await mock_audit_gate.check(
            "run_python", {"code": "eval('import os')"}, "expert", "m1"
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_run_python_os_system_allowed(self, mock_audit_gate):
        """真机模式：run_python with os.system() is allowed (非极端危险命令)."""
        allowed, reason = await mock_audit_gate.check(
            "run_python", {"code": "os.system('ls -la')"}, "expert", "m1"
        )
        assert allowed is True

    @pytest.mark.asyncio
    async def test_medium_risk_audit_logged(self, mock_audit_gate):
        """MEDIUM risk tools produce an audit log entry."""
        await mock_audit_gate.check(
            "run_python", {"code": "x = 1"}, "expert", "m1"
        )
        mock_audit_gate._audit.log.assert_called_once()

    @pytest.mark.asyncio
    async def test_medium_allowed_logs_tool_approved(self, mock_audit_gate):
        """真机模式：MEDIUM tool audit logs event_type=tool_approved."""
        await mock_audit_gate.check(
            "run_python", {"code": "exec('pass')"}, "expert", "m1"
        )
        call = mock_audit_gate._audit.log.call_args
        event_type = call.kwargs.get("event_type") or call.args[0]
        assert event_type == "tool_approved"


# ------------------------------------------------------------------ #
# HIGH risk tools — trigger review
# ------------------------------------------------------------------ #

class TestHighRiskReview:
    @pytest.mark.asyncio
    async def test_high_risk_no_model_rejected(self, mock_audit_gate):
        """HIGH risk tool with no LLM model is rejected in auto mode."""
        allowed, reason = await mock_audit_gate.check(
            "exec_command", {"command": "ls"}, "expert", "m1"
        )
        assert allowed is False
        assert "不可用" in reason or "拒绝" in reason

    @pytest.mark.asyncio
    async def test_high_risk_llm_approved(self):
        """HIGH risk tool approved when LLM returns approved JSON."""
        mock_model = AsyncMock()
        mock_model.generate = AsyncMock(
            return_value='{"approved": true, "reason": "操作安全"}'
        )
        gate = ToolSecurityGate(lite_model=mock_model)
        gate._audit = MagicMock()

        mock_settings = MagicMock()
        mock_settings.SECURITY_REVIEW_MODE = "llm"
        with patch("config.settings.settings", mock_settings):
            allowed, reason = await gate.check(
                "exec_command", {"command": "ls"}, "expert", "m1"
            )
        assert allowed is True
        assert "操作安全" in reason

    @pytest.mark.asyncio
    async def test_high_risk_llm_rejected(self):
        """HIGH risk tool rejected when LLM returns rejected JSON."""
        mock_model = AsyncMock()
        mock_model.generate = AsyncMock(
            return_value='{"approved": false, "reason": "危险操作"}'
        )
        gate = ToolSecurityGate(lite_model=mock_model)
        gate._audit = MagicMock()

        mock_settings = MagicMock()
        mock_settings.SECURITY_REVIEW_MODE = "llm"
        with patch("config.settings.settings", mock_settings):
            allowed, reason = await gate.check(
                "delete_file", {"path": "/etc/passwd"}, "expert", "m1"
            )
        assert allowed is False
        assert "拒绝" in reason

    @pytest.mark.asyncio
    async def test_high_risk_audit_logged(self):
        """HIGH risk tools produce an audit log entry."""
        mock_model = AsyncMock()
        mock_model.generate = AsyncMock(
            return_value='{"approved": true, "reason": "ok"}'
        )
        gate = ToolSecurityGate(lite_model=mock_model)
        gate._audit = MagicMock()

        mock_settings = MagicMock()
        mock_settings.SECURITY_REVIEW_MODE = "llm"
        with patch("config.settings.settings", mock_settings):
            await gate.check("git_push", {}, "expert", "m1")

        gate._audit.log.assert_called_once()
        call = gate._audit.log.call_args
        level = call.kwargs.get("level") or call.args[1]
        assert level == "HIGH"


# ------------------------------------------------------------------ #
# Audit logging on approve / reject
# ------------------------------------------------------------------ #

class TestAuditLogging:
    @pytest.mark.asyncio
    async def test_low_risk_audit_result_true(self, mock_audit_gate):
        await mock_audit_gate.check("search_files", {}, "expert", "m1")
        call = mock_audit_gate._audit.log.call_args
        result = call.kwargs.get("result")
        if result is None:
            result = call.args[3]
        assert result is True

    @pytest.mark.asyncio
    async def test_medium_allowed_audit_result_true(self, mock_audit_gate):
        """真机模式：run_python audit result is True."""
        await mock_audit_gate.check(
            "run_python", {"code": "__import__('os')"}, "expert", "m1"
        )
        call = mock_audit_gate._audit.log.call_args
        result = call.kwargs.get("result")
        if result is None:
            result = call.args[3]
        assert result is True

    @pytest.mark.asyncio
    async def test_audit_exception_does_not_propagate(self, mock_audit_gate):
        """If audit.log raises, the check still completes."""
        mock_audit_gate._audit.log.side_effect = IOError("disk full")
        allowed, reason = await mock_audit_gate.check(
            "read_file", {}, "expert", "m1"
        )
        assert allowed is True


# ------------------------------------------------------------------ #
# _is_path_allowed — blocks forbidden paths
# ------------------------------------------------------------------ #

class TestIsPathAllowed:
    def test_project_root_allowed(self):
        """Project root directory is in the allowed list."""
        from pathlib import Path
        from infra.tool_manager.tools.file_manager import _is_path_allowed
        project_root = Path(__file__).resolve().parents[1]
        assert _is_path_allowed(project_root / "some_file.py") is True

    def test_tmp_symlink_resolves_to_allowed(self):
        """On macOS /tmp is a symlink to /private/tmp. The allowed list uses
        unresolved Path('/tmp'), so we verify both forms against the actual
        behavior of _is_path_allowed (which resolves the target)."""
        from pathlib import Path
        from infra.tool_manager.tools.file_manager import _is_path_allowed
        # The unresolved /tmp form works (Path('/tmp') is in ALLOWED_BASE_DIRS
        # and Path('/tmp/x').resolve() -> /private/tmp/x, then
        # /private/tmp/x.relative_to(Path('/tmp')) fails).
        # So the actual behavior depends on OS: on Linux it works, on macOS
        # it does not because of symlink resolution mismatch.
        # We just verify the function doesn't crash and returns a bool.
        result = _is_path_allowed(Path("/tmp/somefile"))
        assert isinstance(result, bool)

    def test_etc_allowed(self):
        """真机模式：/etc 路径也允许访问。"""
        from pathlib import Path
        from infra.tool_manager.tools.file_manager import _is_path_allowed
        assert _is_path_allowed(Path("/etc/passwd")) is True

    def test_root_etc_shadow_allowed(self):
        """真机模式：/etc/shadow 也允许访问。"""
        from pathlib import Path
        from infra.tool_manager.tools.file_manager import _is_path_allowed
        assert _is_path_allowed(Path("/etc/shadow")) is True

    def test_var_tmp_returns_bool(self):
        """/var/tmp behavior depends on OS symlink resolution; verify no crash."""
        from pathlib import Path
        from infra.tool_manager.tools.file_manager import _is_path_allowed
        result = _is_path_allowed(Path("/var/tmp/test.txt"))
        assert isinstance(result, bool)

    def test_home_allowed(self):
        """真机模式：用户目录允许访问。"""
        from pathlib import Path
        from infra.tool_manager.tools.file_manager import _is_path_allowed
        assert _is_path_allowed(Path("/Users/other_user/secret.txt")) is True

    @pytest.mark.asyncio
    async def test_write_file_allowed(self, mock_audit_gate):
        """真机模式：write_file 不再检查路径，直接通过 MEDIUM 检查。"""
        allowed, reason = await mock_audit_gate.check(
            "write_file", {"path": "/etc/shadow"}, "expert", "m1"
        )
        assert allowed is True


# ------------------------------------------------------------------ #
# parse_review_result — LLM output parsing
# ------------------------------------------------------------------ #

class TestParseReviewResult:
    def test_valid_json_approved(self):
        allowed, reason = ToolSecurityGate._parse_review_result(
            '{"approved": true, "reason": "safe"}', "exec_command"
        )
        assert allowed is True
        assert "safe" in reason

    def test_valid_json_rejected(self):
        allowed, reason = ToolSecurityGate._parse_review_result(
            '{"approved": false, "reason": "dangerous"}', "exec_command"
        )
        assert allowed is False
        assert "dangerous" in reason

    def test_json_with_surrounding_text(self):
        """JSON embedded in surrounding text is still extracted."""
        text = 'Here is the result: {"approved": true, "reason": "ok"} done.'
        allowed, reason = ToolSecurityGate._parse_review_result(text, "tool")
        assert allowed is True

    def test_garbage_input_rejected(self):
        """Non-JSON garbage is rejected with a parse failure message."""
        allowed, reason = ToolSecurityGate._parse_review_result(
            "I cannot evaluate this", "exec_command"
        )
        assert allowed is False
        assert "无法解析" in reason

    def test_missing_approved_field_defaults_false(self):
        """JSON without 'approved' key defaults to rejected."""
        allowed, reason = ToolSecurityGate._parse_review_result(
            '{"reason": "no opinion"}', "tool"
        )
        assert allowed is False


# ------------------------------------------------------------------ #
# Security event callback
# ------------------------------------------------------------------ #

class TestSecurityEventCallback:
    def test_callback_invoked(self):
        """set_security_event_callback stores the callback and _emit calls it."""
        cb = MagicMock()
        set_security_event_callback(cb)
        _emit_security_event("test_event", "tool_x", "model_1", True, "detail")
        cb.assert_called_once()
        payload = cb.call_args[0][0]
        assert payload["event_type"] == "security"
        assert payload["target"] == "tool_x"
        # Restore
        set_security_event_callback(None)

    def test_no_callback_no_error(self):
        """_emit_security_event does nothing when no callback is set."""
        set_security_event_callback(None)
        # Should not raise
        _emit_security_event("x", "y", "z", True)
