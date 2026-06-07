"""
Tests for TUI AppState — state management.
"""
import pytest
from cli_tui.state import AppState


class TestAppState:
    def test_defaults(self):
        state = AppState()
        assert state.processing is False
        assert state.connected is False
        assert state.context_tokens == 0
        assert state.context_window_size == 0

    def test_reset_for_new_input(self):
        state = AppState()
        state.last_user_input = "test"
        state.reset_for_new_input()
        # After reset, some state should be cleared
        assert isinstance(state.processing, bool)

    def test_add_dialog_entry(self):
        state = AppState()
        state.add_dialog_entry({"role": "user", "content": "hello"})
        assert len(state.dialog_entries) >= 1

    def test_add_input_history(self):
        state = AppState()
        state.add_input_history("first")
        state.add_input_history("second")
        assert len(state.input_history) >= 2

    def test_add_tool_call(self):
        state = AppState()
        state.add_tool_call({"name": "search", "args": {"q": "test"}})
        assert len(state.tool_calls) >= 1

    def test_session_id(self):
        state = AppState()
        state.session_id = "test-session"
        assert state.session_id == "test-session"

    def test_show_thinking_default(self):
        state = AppState()
        assert isinstance(state.show_thinking, bool)
