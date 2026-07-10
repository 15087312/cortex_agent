"""会话选择器 — 选中后弹出操作菜单"""

from textual.app import ComposeResult
from textual.screen import ModalScreen
from textual.widgets import ListView, ListItem, Label, Static
from textual.containers import Vertical


class SessionPicker(ModalScreen):
    """会话选择弹窗"""

    def __init__(self, sessions: list, on_switch, on_actions=None):
        super().__init__()
        self._sessions = sessions
        self._on_switch = on_switch
        self._on_actions = on_actions

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("【选择会话 — 上下键导航，Enter 操作】", id="picker-title")
            items = []
            for i, s in enumerate(self._sessions):
                sid = s.get("session_id", "?")[:20]
                created = s.get("created_at", "")[:16]
                n = s.get("dialog_size", s.get("message_count", 0))
                is_main = "★ " if s.get("is_main", False) else "  "
                title = s.get("title", "")
                title_display = f" 「{title[:30]}」" if title else ""
                label = f"{is_main}{sid}{title_display}  ({created})  {n}条消息"
                items.append(ListItem(Label(label)))
            yield ListView(*items, id="session-list")

    def on_list_view_selected(self, event):
        idx = event.list_view.index
        if 0 <= idx < len(self._sessions):
            target = self._sessions[idx]
            session_id = target["session_id"]
            session_title = target.get("title", "")
            if self._on_actions:
                # 先关闭 picker，再弹出 action menu
                self.dismiss()
                self._on_actions(session_id, session_title)
            else:
                self._on_switch(session_id)
                self.dismiss()
