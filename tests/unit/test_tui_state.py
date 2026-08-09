"""Tests for cli_tui/state.py — AppState behaviour."""
import pytest
from cli_tui.state import AppState


# ---------------------------------------------------------------------------
# Defaults & simple setters
# ---------------------------------------------------------------------------

class TestAppState:
    def test_defaults(self):
        s = AppState()
        assert s.processing is False
        assert s.connected is False
        assert s.context_tokens == 0
        assert s.context_window_size == 0

    def test_session_id_set(self):
        s = AppState()
        s.session_id = "test-session"
        assert s.session_id == "test-session"

    def test_show_thinking_is_bool(self):
        s = AppState()
        assert isinstance(s.show_thinking, bool)


# ---------------------------------------------------------------------------
# reset_for_new_input — parametrised over each field
# ---------------------------------------------------------------------------

RESET_FIELDS = [
    ("dialog_entries", [{"role": "user", "content": "hi"}], []),
    ("tool_calls", [{"name": "search"}], []),
    ("final_response", "some response", ""),
    (
        "tool_stats",
        {"total": 5, "success": 3, "failed": 2, "total_latency_ms": 100.0},
        {"total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0},
    ),
    ("debug_events", [{"event": "x"}], []),
    ("active_experts", ["code_review"], []),
    ("trace_id", "abc-123", ""),
    ("error_chain", [{"error": "boom"}], []),
]


class TestResetForNewInput:
    @pytest.mark.parametrize("field,initial,expected", RESET_FIELDS)
    def test_field_cleared(self, field, initial, expected):
        s = AppState()
        setattr(s, field, initial)
        s.reset_for_new_input()
        assert getattr(s, field) == expected, f"{field} 未被正确重置"

    def test_processing_set_to_true(self):
        s = AppState()
        s.processing = False
        s.reset_for_new_input()
        assert s.processing is True

    def test_cancel_requested_set_to_false(self):
        s = AppState()
        s.cancel_requested = True
        s.reset_for_new_input()
        assert s.cancel_requested is False


# ---------------------------------------------------------------------------
# add_dialog_entry — deduplication
# ---------------------------------------------------------------------------

class TestDialogEntryDedup:
    def test_duplicate_entry_skipped(self):
        """Same tier + round_num + content prefix → second entry is skipped."""
        s = AppState()
        entry = {"tier": "large", "round_num": 1, "content": "Hello world, this is a long message"}
        s.add_dialog_entry(entry)
        s.add_dialog_entry(entry.copy())
        assert len(s.dialog_entries) == 1

    def test_different_round_accepted(self):
        s = AppState()
        s.add_dialog_entry({"tier": "large", "round_num": 1, "content": "same prefix"})
        s.add_dialog_entry({"tier": "large", "round_num": 2, "content": "same prefix"})
        assert len(s.dialog_entries) == 2

    def test_different_tier_accepted(self):
        s = AppState()
        s.add_dialog_entry({"tier": "large", "round_num": 1, "content": "same prefix"})
        s.add_dialog_entry({"tier": "medium", "round_num": 1, "content": "same prefix"})
        assert len(s.dialog_entries) == 2

    def test_different_prefix_accepted(self):
        s = AppState()
        s.add_dialog_entry({"tier": "large", "round_num": 1, "content": "Alpha bravo charlie"})
        s.add_dialog_entry({"tier": "large", "round_num": 1, "content": "Delta echo foxtrot"})
        assert len(s.dialog_entries) == 2


# ---------------------------------------------------------------------------
# add_dialog_entry — max_entries cap
# ---------------------------------------------------------------------------

class TestDialogEntryCap:
    def test_respects_max_entries_cap(self):
        s = AppState(max_entries=5)
        for i in range(10):
            s.add_dialog_entry({"tier": "t", "round_num": i, "content": f"msg {i}"})
        assert len(s.dialog_entries) == 5

    def test_keeps_latest_entries_after_cap(self):
        s = AppState(max_entries=3)
        for i in range(6):
            s.add_dialog_entry({"tier": "t", "round_num": i, "content": f"msg {i}"})
        assert all(e["content"] == f"msg {i}" for e, i in zip(s.dialog_entries, range(3, 6)))


# ---------------------------------------------------------------------------
# add_tool_call — stats and cap
# ---------------------------------------------------------------------------

class TestToolCallStats:
    def test_increments_total_on_success(self):
        s = AppState()
        s.add_tool_call({"name": "a", "success": True, "latency_ms": 100})
        assert s.tool_stats["total"] == 1
        assert s.tool_stats["success"] == 1
        assert s.tool_stats["failed"] == 0

    def test_increments_failed(self):
        s = AppState()
        s.add_tool_call({"name": "a", "success": False, "latency_ms": 50})
        assert s.tool_stats["total"] == 1
        assert s.tool_stats["success"] == 0
        assert s.tool_stats["failed"] == 1

    def test_accumulates_latency(self):
        s = AppState()
        s.add_tool_call({"name": "a", "success": True, "latency_ms": 100})
        s.add_tool_call({"name": "b", "success": True, "latency_ms": 200})
        assert s.tool_stats["total_latency_ms"] == 300.0

    def test_respects_100_cap(self):
        s = AppState()
        for i in range(110):
            s.add_tool_call({"name": f"t{i}", "success": True, "latency_ms": 10})
        assert len(s.tool_calls) == 100
        # Stats still reflect all 110 calls
        assert s.tool_stats["total"] == 110


# ---------------------------------------------------------------------------
# add_input_history — max_history cap
# ---------------------------------------------------------------------------

class TestInputHistoryCap:
    def test_respects_max_history(self):
        s = AppState(max_history=5)
        for i in range(10):
            s.add_input_history(f"input {i}")
        assert len(s.input_history) == 5

    def test_keeps_latest_after_cap(self):
        s = AppState(max_history=3)
        for i in range(6):
            s.add_input_history(f"input {i}")
        assert s.input_history == ["input 3", "input 4", "input 5"]


# ---------------------------------------------------------------------------
# debug_events — max_debug_events field
# ---------------------------------------------------------------------------

class TestDebugEventsCap:
    def test_max_debug_events_defaults_to_200(self):
        s = AppState()
        assert s.max_debug_events == 200

    def test_debug_events_list_can_be_managed_with_cap(self):
        s = AppState(max_debug_events=10)
        for i in range(15):
            s.debug_events.append({"event": f"e{i}"})
            if len(s.debug_events) > s.max_debug_events:
                s.debug_events = s.debug_events[-s.max_debug_events:]
        assert len(s.debug_events) == 10
        assert s.debug_events[0]["event"] == "e5"


# ---------------------------------------------------------------------------
# avg_latency_ms
# ---------------------------------------------------------------------------

class TestAvgLatency:
    def test_returns_zero_when_no_tools(self):
        s = AppState()
        assert s.avg_latency_ms == 0

    def test_calculates_correctly(self):
        s = AppState()
        s.add_tool_call({"name": "a", "success": True, "latency_ms": 100})
        s.add_tool_call({"name": "b", "success": True, "latency_ms": 200})
        assert s.avg_latency_ms == pytest.approx(150.0)

    def test_single_tool_latency(self):
        s = AppState()
        s.add_tool_call({"name": "a", "success": False, "latency_ms": 42.5})
        assert s.avg_latency_ms == pytest.approx(42.5)
