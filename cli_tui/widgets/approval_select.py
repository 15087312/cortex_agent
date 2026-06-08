"""安全审批选择器 — Claude Code 风格的选项列表

方向键选择 Yes/No，Tab 切换自定义文本输入，Enter 确认，Esc 取消。
"""
from __future__ import annotations

from typing import Optional, List, Callable

from textual.app import ComposeResult
from textual.containers import Vertical, Horizontal
from textual.widget import Widget
from textual.widgets import Static, Input
from textual.reactive import reactive
from textual import on


class ApprovalOption(Static):
    """单个选项行"""

    def __init__(self, label: str, value: str, index: int, **kwargs):
        super().__init__(**kwargs)
        self.label_text = label
        self.value = value
        self.index = index
        self._focused = False

    def set_focused(self, focused: bool):
        self._focused = focused
        self.refresh()

    def render(self):
        pointer = "▸" if self._focused else " "
        style = "bold green" if self._focused else "dim"
        num = f"{self.index + 1}."
        return f"[{style}]{pointer} {num} {self.label_text}[/{style}]"


class ApprovalSelect(Widget):
    """安全审批选择器

    支持：
    - 方向键 Up/Down 选择选项
    - 数字键 1/2/3 直接选择
    - Enter 确认
    - Esc 取消
    - Tab 切换到自定义文本输入
    """

    DEFAULT_CSS = """
    ApprovalSelect {
        height: auto;
        max-height: 12;
        border: heavy $warning;
        padding: 0 1;
        margin: 1 0;
    }
    ApprovalSelect .approval-title {
        color: $warning;
        text-style: bold;
        margin-bottom: 0;
    }
    ApprovalSelect .approval-detail {
        color: $text-muted;
        margin-bottom: 1;
    }
    ApprovalSelect .approval-option {
        height: 1;
    }
    ApprovalSelect .approval-input {
        margin-top: 1;
        height: 3;
    }
    ApprovalSelect .approval-hint {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("up", "previous", "上一个"),
        ("down", "next", "下一个"),
        ("enter", "confirm", "确认"),
        ("escape", "cancel", "取消"),
        ("tab", "toggle_input", "自定义"),
        # 透传 Screen 级快捷键 — 避免焦点抢占
        ("ctrl+a", "approve", "批准"),
        ("ctrl+d", "reject", "拒绝"),
        ("shift+tab", "cycle_mode", "切换模式"),
    ]

    focus_index: reactive[int] = reactive(0)
    input_mode: reactive[bool] = reactive(False)

    def __init__(
        self,
        tool_name: str,
        tool_detail: str = "",
        options: Optional[List[dict]] = None,
        on_confirm: Optional[Callable[[str, str], None]] = None,
        on_cancel: Optional[Callable[[], None]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self.tool_detail = tool_detail
        self.options = options or [
            {"label": "Yes, approve", "value": "yes"},
            {"label": "No, reject", "value": "no"},
        ]
        self._on_confirm = on_confirm
        self._on_cancel = on_cancel
        self._option_widgets: List[ApprovalOption] = []
        self._title_widget: Optional[Static] = None
        self._detail_widget: Optional[Static] = None
        self._hint_widget: Optional[Static] = None

    def compose(self) -> ComposeResult:
        self._title_widget = Static(
            f"🔒 [bold]安全审批[/bold] — [red]{self.tool_name}[/red]",
            classes="approval-title",
        )
        yield self._title_widget
        self._detail_widget = Static(
            f"  {self.tool_detail}" if self.tool_detail else "",
            classes="approval-detail",
        )
        yield self._detail_widget

        for i, opt in enumerate(self.options):
            w = ApprovalOption(opt["label"], opt["value"], i, classes="approval-option")
            self._option_widgets.append(w)
            yield w

        self._hint_widget = Static(
            "  ↑↓ 选择 · Enter 确认 · Tab 自定义 · Esc 取消",
            classes="approval-hint",
        )
        yield self._hint_widget

    def on_mount(self):
        self._update_focus()

    def rebuild_options(self, new_options: List[dict], new_title: str = "", new_detail: str = ""):
        """动态重建选项列表（安全审批 → 模式切换 → 用户意图 复用同一组件）"""
        self.options = new_options
        self.focus_index = 0
        self.input_mode = False

        # 更新标题和详情
        if new_title:
            self.tool_name = new_title
        if new_detail:
            self.tool_detail = new_detail

        # 移除已有选项 widget 和自定义输入
        for w in list(self._option_widgets):
            w.remove()
        self._option_widgets.clear()
        for w in self.query(".approval-input"):
            w.remove()

        # 创建新选项 widget 并挂载到 hint 之前
        for i, opt in enumerate(new_options):
            w = ApprovalOption(opt["label"], opt["value"], i, classes="approval-option")
            self._option_widgets.append(w)
            self.mount(w, before=self._hint_widget)

        # 更新标题和详情文本
        if self._title_widget:
            self._title_widget.update(f"🔒 [bold]{self.tool_name}[/bold]")
        if self._detail_widget:
            self._detail_widget.update(f"  {self.tool_detail}" if self.tool_detail else "")

        self._update_focus()

    def watch_focus_index(self, old: int, new: int):
        self._update_focus()

    def _update_focus(self):
        for i, w in enumerate(self._option_widgets):
            w.set_focused(i == self.focus_index)

    def action_previous(self):
        if self.input_mode:
            return
        self.focus_index = (self.focus_index - 1) % len(self.options)

    def action_next(self):
        if self.input_mode:
            return
        self.focus_index = (self.focus_index + 1) % len(self.options)

    def action_confirm(self):
        if self.input_mode:
            return  # 输入模式下由 Input 的 on_submit 处理
        value = self.options[self.focus_index]["value"]
        if self._on_confirm:
            self._on_confirm(value, "")

    def action_cancel(self):
        if self.input_mode:
            self.input_mode = False
            return
        if self._on_cancel:
            self._on_cancel()

    def action_toggle_input(self):
        self.input_mode = not self.input_mode
        if self.input_mode:
            # 切换到自定义输入模式
            self._show_input()
        else:
            self._hide_input()

    def _show_input(self):
        # 移除已有的 input（如果有）
        self._hide_input()
        inp = Input(placeholder="输入自定义理由...", classes="approval-input")
        self.mount(inp)
        inp.focus()

    def _hide_input(self):
        for w in self.query(".approval-input"):
            w.remove()

    @on(Input.Submitted, ".approval-input")
    def _on_custom_input(self, event: Input.Submitted):
        text = event.value.strip()
        if text and self._on_confirm:
            self._on_confirm("custom", text)
        elif self._on_cancel:
            self._on_cancel()

    def on_key(self, event):
        """处理数字键直接选择"""
        if self.input_mode:
            return
        if event.key in ("1", "2", "3", "4", "5", "6", "7", "8", "9"):
            idx = int(event.key) - 1
            if 0 <= idx < len(self.options):
                self.focus_index = idx
                value = self.options[idx]["value"]
                if self._on_confirm:
                    self._on_confirm(value, "")
