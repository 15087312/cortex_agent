"""会话操作菜单 — 选择会话后的操作选项"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import Static, Button
from textual.containers import Vertical


class SessionActionMenu(ModalScreen):
    """会话操作弹窗 — 删除 / 回滚+删除 / 分叉"""

    CSS = """
    SessionActionMenu {
        align: center middle;
    }
    #action-dialog {
        width: 56;
        height: auto;
        max-height: 20;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #action-dialog Static {
        width: 100%;
        margin-bottom: 1;
    }
    #action-dialog Button {
        width: 100%;
        margin-bottom: 0;
    }
    """

    def __init__(self, session_id: str, session_title: str = ""):
        super().__init__()
        self._session_id = session_id
        self._session_title = session_title
        self._on_action = None

    def set_on_action(self, callback):
        """设置操作回调: callback(action: str, session_id: str)"""
        self._on_action = callback

    def compose(self) -> ComposeResult:
        with Vertical(id="action-dialog"):
            title = self._session_title or self._session_id[:20]
            yield Static(f"[bold]会话操作[/bold]\n[dim]{title}[/dim]")
            yield Button("🗑  删除对话", variant="error", id="btn-delete")
            yield Button("⏪ 回滚文件 + 删除（用户消息→剪贴板）", variant="warning", id="btn-rollback")
            yield Button("▶ 继续对话", variant="primary", id="btn-continue")
            yield Button("取消", variant="default", id="btn-cancel")

    def _do_action(self, action: str):
        if self._on_action:
            self._on_action(action, self._session_id)
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed):
        event.stop()
        action_map = {
            "btn-delete": "delete",
            "btn-rollback": "rollback",
            "btn-continue": "continue",
            "btn-cancel": "cancel",
        }
        action = action_map.get(event.button.id, "cancel")
        self._do_action(action)

    def on_key(self, event):
        if event.key == "escape":
            self._do_action("cancel")
