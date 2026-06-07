"""输入框 — 用户输入 + 历史 + 命令检测"""

from typing import Optional

from textual.widgets import Input

from ..state import AppState


class PromptInput(Input):
    """带历史记录和命令检测的输入框"""

    def __init__(self, state: AppState):
        super().__init__(placeholder="输入消息… (/) 命令, Ctrl+C 退出")
        self._state = state
        self._history_index = -1

    def on_mount(self):
        self.border_title = "输入"

    def history_back(self) -> Optional[str]:
        """回退到上一条历史，返回历史文本或 None"""
        if not self._state.input_history:
            return None
        if self._history_index == -1:
            self._history_index = len(self._state.input_history) - 1
        elif self._history_index > 0:
            self._history_index -= 1
        return self._state.input_history[self._history_index]

    def history_forward(self):
        """前进到下一条历史，返回历史文本或空串或 None"""
        if not self._state.input_history or self._history_index == -1:
            return None
        self._history_index += 1
        if self._history_index >= len(self._state.input_history):
            self._history_index = -1
            return ""
        return self._state.input_history[self._history_index]

    def reset_history(self):
        """重置历史索引"""
        self._history_index = -1
