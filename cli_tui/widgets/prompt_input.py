"""输入框 — 用户输入 + 历史 + 命令检测 + 安全审批模式"""

from typing import Optional

from textual.widgets import Input

from ..state import AppState


class PromptInput(Input):
    """带历史记录、命令检测和安全审批模式的输入框"""

    def __init__(self, state: AppState):
        super().__init__(placeholder="输入消息… (/) 命令, Ctrl+C 退出")
        self._state = state
        self._history_index = -1
        self._approval_mode = False
        self._original_placeholder = "输入消息… (/) 命令, Ctrl+C 退出"

    def on_mount(self):
        self.border_title = "输入"

    def set_approval_mode(self, enabled: bool):
        """切换安全审批模式 — 改变输入框视觉状态"""
        self._approval_mode = enabled
        if enabled:
            self.placeholder = "输入 y 批准 / n 拒绝 / 自定义理由，或 Ctrl+A/D"
            self.border_title = "🔒 安全审批"
            self.add_class("approval-mode")
        else:
            self.placeholder = self._original_placeholder
            self.border_title = "输入"
            self.remove_class("approval-mode")

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
