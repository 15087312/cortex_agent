"""
Tests for cli_tui/state.py — enhanced AppState behaviour.

Complements test_tui_state.py with deeper coverage of reset semantics,
deduplication, caps, and computed properties.
"""
import pytest
from cli_tui.state import AppState


# ---------------------------------------------------------------------------
# reset_for_new_input
# ---------------------------------------------------------------------------

class TestResetForNewInput:
    def test_clears_dialog_entries(self):
        s = AppState()
        s.dialog_entries = [{"role": "user", "content": "hi"}]
        s.reset_for_new_input()
        assert s.dialog_entries == []

    def test_clears_tool_calls(self):
        s = AppState()
        s.tool_calls = [{"name": "search"}]
        s.reset_for_new_input()
        assert s.tool_calls == []

    def test_clears_final_response(self):
        s = AppState()
        s.final_response = "some response"
        s.reset_for_new_input()
        assert s.final_response == ""

    def test_clears_tool_stats(self):
        s = AppState()
        s.tool_stats = {"total": 5, "success": 3, "failed": 2, "total_latency_ms": 100.0}
        s.reset_for_new_input()
        assert s.tool_stats == {"total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0}

    def test_clears_debug_events(self):
        s = AppState()
        s.debug_events = [{"event": "x"}]
        s.reset_for_new_input()
        assert s.debug_events == []

    def test_clears_active_experts(self):
        s = AppState()
        s.active_experts = ["code_review"]
        s.reset_for_new_input()
        assert s.active_experts == []

    def test_sets_processing_true(self):
        s = AppState()
        s.processing = False
        s.reset_for_new_input()
        assert s.processing is True

    def test_clears_trace_id(self):
        s = AppState()
        s.trace_id = "abc-123"
        s.reset_for_new_input()
        assert s.trace_id == ""

    def test_clears_error_chain(self):
        s = AppState()
        s.error_chain = [{"error": "boom"}]
        s.reset_for_new_input()
        assert s.error_chain == []

    def test_resets_cancel_requested(self):
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
        """Different round_num → entry is accepted."""
        s = AppState()
        s.add_dialog_entry({"tier": "large", "round_num": 1, "content": "same prefix"})
        s.add_dialog_entry({"tier": "large", "round_num": 2, "content": "same prefix"})
        assert len(s.dialog_entries) == 2

    def test_different_tier_accepted(self):
        """Different tier → entry is accepted."""
        s = AppState()
        s.add_dialog_entry({"tier": "large", "round_num": 1, "content": "same prefix"})
        s.add_dialog_entry({"tier": "medium", "round_num": 1, "content": "same prefix"})
        assert len(s.dialog_entries) == 2

    def test_different_prefix_accepted(self):
        """Different content prefix → entry is accepted."""
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
        # The last 3 should be retained
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
        """Verify the expected truncation pattern for debug_events."""
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
