"""命令建议下拉框 — 输入 / 时显示可用命令列表

参考 Open-ClaudeCode 的 PromptInputFooterSuggestions.tsx：
- 模糊匹配命令名和别名
- 键盘上下导航 + Enter/Tab 选择
- 实时过滤
"""

import logging
from typing import List, Optional

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from ..commands import Command, get_all

logger = logging.getLogger("tui_command_suggestions")


class SuggestionItem(Static):
    """单个命令建议项"""

    def __init__(self, cmd: Command, index: int, selected: bool = False):
        self.cmd = cmd
        self.index = index
        self.selected = selected
        super().__init__(self._build_markup())

    def _build_markup(self) -> str:
        name = self.cmd.name
        aliases = ", ".join(self.cmd.aliases) if self.cmd.aliases else ""
        desc = self.cmd.description

        if self.selected:
            return f"[bold cyan]▸[/bold cyan] [bold white on dark_cyan]{name:<14}[/] [dim]{aliases:<12}[/] [white]{desc}[/]"
        else:
            return f"  [bold white]{name:<14}[/] [dim]{aliases:<12}[/] [dim]{desc}[/]"

    def set_selected(self, selected: bool):
        self.selected = selected
        self.update(self._build_markup())


class CommandSuggestions(VerticalScroll):
    """命令建议下拉框"""

    BINDINGS = [
        Binding("up", "navigate_up", "上", show=False),
        Binding("down", "navigate_down", "下", show=False),
        Binding("enter", "select", "选择", show=False),
        Binding("tab", "select", "选择", show=False),
        Binding("escape", "dismiss", "关闭", show=False),
    ]

    DEFAULT_CSS = """
    CommandSuggestions {
        height: auto;
        max-height: 12;
        background: $surface;
        border: tall $primary;
        margin: 0 1;
    }
    SuggestionItem {
        height: 1;
        padding: 0 1;
    }
    SuggestionItem:hover {
        background: $accent 30%;
    }
    """

    def __init__(self):
        super().__init__()
        self._commands: List[Command] = get_all()
        self._filtered: List[Command] = []
        self._selected_index: int = 0
        self._query: str = ""
        self._on_select_callback = None
        # 初始隐藏
        self.styles.display = "none"

    def on_mount(self):
        pass

    def set_on_select(self, callback):
        """设置选中回调: callback(command: Command)"""
        self._on_select_callback = callback

    def update_query(self, query: str):
        """根据输入更新过滤列表"""
        self._query = query.lower().strip()
        self._selected_index = 0
        self._filter_commands()
        self._rebuild_items()

    def _filter_commands(self):
        """模糊匹配命令名和别名"""
        if not self._query:
            self._filtered = list(self._commands)
            return

        q = self._query
        scored = []
        for cmd in self._commands:
            score = 0
            name = cmd.name.lower()
            aliases = [a.lower() for a in cmd.aliases]

            # 精确匹配
            if name == f"/{q}" or f"/{q}" in aliases:
                score = 100
            # 前缀匹配
            elif name.startswith(f"/{q}"):
                score = 80
            elif any(a.startswith(f"/{q}") for a in aliases):
                score = 70
            # 名称包含
            elif q in name:
                score = 50
            # 别名包含
            elif any(q in a for a in aliases):
                score = 40
            # 描述包含
            elif q in cmd.description.lower():
                score = 20

            if score > 0:
                scored.append((score, cmd))

        scored.sort(key=lambda x: -x[0])
        self._filtered = [cmd for _, cmd in scored]

    def _rebuild_items(self):
        """重建建议列表"""
        self.remove_children()
        for i, cmd in enumerate(self._filtered):
            item = SuggestionItem(cmd, i, selected=(i == self._selected_index))
            self.mount(item)

        # 直接控制 display 属性
        if self._filtered:
            self.styles.display = "block"
            self.styles.height = "auto"
        else:
            self.styles.display = "none"

    def _update_selection(self):
        """更新选中状态的视觉"""
        items = self.query(SuggestionItem)
        for item in items:
            item.set_selected(item.index == self._selected_index)
        # 滚动到选中项
        if self._filtered:
            try:
                selected_item = items[self._selected_index] if self._selected_index < len(items) else None
                if selected_item:
                    self.scroll_to_widget(selected_item)
            except Exception as e:
                logger.debug("Failed to scroll to selected suggestion: %s", e)

    def action_navigate_up(self):
        if self._filtered:
            self._selected_index = (self._selected_index - 1) % len(self._filtered)
            self._update_selection()

    def action_navigate_down(self):
        if self._filtered:
            self._selected_index = (self._selected_index + 1) % len(self._filtered)
            self._update_selection()

    def action_select(self):
        """选中当前命令"""
        if self._filtered and self._selected_index < len(self._filtered):
            cmd = self._filtered[self._selected_index]
            self._dismiss_and_execute(cmd)

    def action_dismiss(self):
        """关闭建议框"""
        self._dismiss()

    def _dismiss_and_execute(self, cmd: Command):
        """关闭建议框并执行命令"""
        self._dismiss()
        if self._on_select_callback:
            self._on_select_callback(cmd)

    def _dismiss(self):
        """关闭建议框"""
        self._filtered = []
        self._selected_index = 0
        self.remove_children()
        self.styles.display = "none"
