"""历史编辑屏幕 — 查看和编辑对话历史条目"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import TextArea, Button, Static
from textual.containers import Horizontal, Vertical


class HistoryEditor(ModalScreen):
    """编辑单条对话历史"""

    def __init__(self, index: int, content: str, on_save):
        super().__init__()
        self._idx = index
        self._original = content
        self._on_save = on_save

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(f"【编辑第 {self._idx} 条对话 — 点击按钮保存/取消】", id="editor-title")
            yield TextArea(self._original, id="history-textarea", soft_wrap=True)
            with Horizontal(id="editor-buttons"):
                yield Button("保存", variant="primary", id="save-btn")
                yield Button("取消", variant="default", id="cancel-btn")

    def on_button_pressed(self, event):
        if event.button.id == "save-btn":
            self._save()
        elif event.button.id == "cancel-btn":
            self.app.pop_screen()

    def on_key(self, event):
        if event.key == "escape":
            self.app.pop_screen()

    def _save(self):
        ta = self.query_one("#history-textarea", TextArea)
        new_text = ta.text
        if new_text != self._original:
            self._on_save(self._idx, new_text)
        self.app.pop_screen()
