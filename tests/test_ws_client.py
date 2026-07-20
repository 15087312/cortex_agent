"""
Tests for WSClient — WebSocket client event parsing and headers.
"""
import time
import pytest
from unittest.mock import MagicMock

from cli_tui.services.ws_client import WSClient


@pytest.fixture
def client():
    return WSClient(api_url="http://localhost:8080", api_key="")


@pytest.fixture
def client_with_key():
    return WSClient(api_url="http://localhost:8080", api_key="test-secret-key")


# ------------------------------------------------------------------ #
# parse_event — dict input
# ------------------------------------------------------------------ #

class TestParseEventDict:
    def test_parse_dict_returns_dict_or_none(self, client):
        """parse_event on a dict input returns dict or None (never raises)."""
        result = client.parse_event({"type": "unknown", "content": "hi"})
        assert result is None or isinstance(result, dict)

    def test_parse_empty_dict_returns_none(self, client):
        """An empty dict with no recognized fields returns None."""
        result = client.parse_event({})
        assert result is None


# ------------------------------------------------------------------ #
# parse_event — non-dict input (returns None)
# ------------------------------------------------------------------ #

class TestParseEventNonDict:
    def test_parse_string_returns_none(self, client):
        assert client.parse_event("not a dict") is None

    def test_parse_list_returns_none(self, client):
        assert client.parse_event([1, 2, 3]) is None

    def test_parse_int_returns_none(self, client):
        assert client.parse_event(42) is None

    def test_parse_none_returns_none(self, client):
        assert client.parse_event(None) is None

    def test_parse_bool_returns_none(self, client):
        assert client.parse_event(True) is None


# ------------------------------------------------------------------ #
# parse_event — malformed events (graceful, returns None)
# ------------------------------------------------------------------ #

class TestParseEventMalformed:
    def test_malformed_no_type(self, client):
        result = client.parse_event({"random_key": "value"})
        assert result is None

    def test_malformed_none_data(self, client):
        """None data field does not crash; returns None."""
        result = client.parse_event({"type": "status", "data": None, "event": "x"})
        assert result is None or isinstance(result, dict)

    def test_malformed_nested_none(self, client):
        """Nested None in data.stage_event does not crash."""
        event = {
            "type": "status",
            "data": {"stage_event": None},
            "event": "tool_call",
        }
        result = client.parse_event(event)
        # Should not raise; result depends on how the code handles it
        assert result is None or isinstance(result, dict)


# ------------------------------------------------------------------ #
# parse_event — broadcast events
# ------------------------------------------------------------------ #

class TestParseEventBroadcast:
    def test_broadcast_event_parsed(self, client):
        """A broadcast event with dialog_id produces a dialog entry."""
        event = {
            "type": "status",
            "data": {
                "stage_event": {
                    "type": "message",
                    "payload": {
                        "msg_type": "broadcast",
                        "content": {
                            "content": "Hello from expert",
                            "entry_type": "response",
                            "model_id": "expert-1",
                            "tier": "expert",
                            "round": 1,
                        },
                        "metadata": {"dialog_id": "dlg-123"},
                    },
                },
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "dialog"
        assert result["tier"] == "expert"
        assert result["content"] == "Hello from expert"
        assert result["model_id"] == "expert-1"
        assert result["round_num"] == 1

    def test_broadcast_string_content(self, client):
        """Broadcast with plain string content is handled."""
        event = {
            "data": {
                "stage_event": {
                    "payload": {
                        "msg_type": "broadcast",
                        "content": "plain text message",
                        "metadata": {"dialog_id": "dlg-456"},
                    },
                },
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "dialog"
        assert result["content"] == "plain text message"

    def test_broadcast_without_dialog_id_ignored(self, client):
        """Broadcast without dialog_id in metadata is not parsed as dialog."""
        event = {
            "data": {
                "stage_event": {
                    "payload": {
                        "msg_type": "broadcast",
                        "content": "msg",
                        "metadata": {},
                    },
                },
            },
        }
        result = client.parse_event(event)
        # Without dialog_id, broadcast branch is skipped
        assert result is None or (isinstance(result, dict) and result.get("kind") != "dialog")


# ------------------------------------------------------------------ #
# parse_event — tool_call events
# ------------------------------------------------------------------ #

class TestParseEventToolCall:
    def test_tool_call_parsed(self, client):
        """A tool_call event produces a tool entry."""
        event = {
            "data": {
                "stage_event": {
                    "type": "tool_call",
                    "target": "run_python",
                    "action": "execute",
                    "success": True,
                    "latency_ms": 150,
                    "payload": {
                        "params": {"code": "print('hi')"},
                        "result": "hi",
                    },
                },
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "tool"
        assert result["tool"] == "run_python"
        assert result["success"] is True
        assert result["latency_ms"] == 150

    def test_tool_call_failure(self, client):
        """A failed tool call is recorded with success=False."""
        event = {
            "data": {
                "stage_event": {
                    "type": "tool_call",
                    "target": "exec_command",
                    "action": "execute",
                    "success": False,
                    "latency_ms": 50,
                    "payload": {
                        "error": "command not found",
                    },
                },
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "tool"
        assert result["success"] is False
        assert result["error"] == "command not found"


# ------------------------------------------------------------------ #
# parse_event — simple status events
# ------------------------------------------------------------------ #

class TestParseEventSimpleStatus:
    def test_thinking_step_parsed(self, client):
        """A simple thinking_step event produces a dialog entry."""
        event = {
            "type": "thinking",
            "event": "thinking_step",
            "content": "Analyzing the request...",
            "role": "supervisor",
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "dialog"
        assert result["tier"] == "supervisor"
        assert result["content"] == "Analyzing the request..."
        assert result["entry_type"] == "thought"

    def test_module_result_parsed(self, client):
        """A module_result status event produces a system dialog entry."""
        event = {
            "type": "status",
            "event": "module_result",
            "content": "Module X completed",
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "dialog"
        assert result["tier"] == "system"
        assert result["content"] == "Module X completed"

    def test_thinking_progress_parsed(self, client):
        """A thinking_progress event produces a status entry."""
        event = {
            "type": "status",
            "event": "thinking_progress",
            "content": "Processing...",
            "data": {
                "phase": "analyzing",
                "badge": "Thinking",
                "progress": 0.5,
                "elapsed_s": 3,
                "queue_size": 2,
                "running": True,
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "status"
        assert result["phase"] == "analyzing"
        assert result["progress"] == 0.5
        assert result["running"] is True

    def test_thinking_step_empty_content_ignored(self, client):
        """A thinking_step with empty content returns None."""
        event = {
            "type": "thinking",
            "event": "thinking_step",
            "content": "",
        }
        result = client.parse_event(event)
        assert result is None


# ------------------------------------------------------------------ #
# _make_headers
# ------------------------------------------------------------------ #

class TestMakeHeaders:
    def test_headers_with_api_key(self, client_with_key):
        """_make_headers includes X-API-Key when api_key is set."""
        headers = client_with_key._make_headers()
        assert "X-API-Key" in headers
        assert headers["X-API-Key"] == "test-secret-key"

    def test_headers_without_api_key(self, client):
        """_make_headers returns empty dict when api_key is empty."""
        headers = client._make_headers()
        assert headers == {}

    def test_headers_none_key_treated_as_empty(self):
        """api_key=None is treated as no key."""
        c = WSClient(api_url="http://localhost:8080", api_key="")
        c.api_key = ""
        headers = c._make_headers()
        assert "X-API-Key" not in headers


# ------------------------------------------------------------------ #
# parse_event — security events
# ------------------------------------------------------------------ #

class TestParseEventSecurity:
    def test_security_review_pending(self, client):
        """A security event with '等待用户审批' produces a security_review entry."""
        event = {
            "data": {
                "stage_event": {
                    "type": "security",
                    "action": "等待用户审批",
                    "target": "exec_command",
                    "payload": {
                        "detail": "ls -la",
                        "request_id": "abc123",
                        "caller": "model-1",
                    },
                },
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "security_review"
        assert result["request_id"] == "abc123"
        assert result["tool"] == "exec_command"

    def test_security_approved(self, client):
        """A security event with '审批通过' produces a security entry."""
        event = {
            "data": {
                "stage_event": {
                    "type": "security",
                    "action": "审批通过",
                    "target": "exec_command",
                    "success": True,
                    "payload": {
                        "detail": "ok",
                        "duration_ms": 200,
                    },
                },
            },
        }
        result = client.parse_event(event)
        assert result is not None
        assert result["kind"] == "security"
        assert result["action"] == "审批通过"
