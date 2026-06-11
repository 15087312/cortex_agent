"""底栏 — 模型信息 + 快捷键提示"""

import time
from rich.text import Text
from textual.widgets import Static

from ..state import AppState

_MODE_LABELS = {
    "plan": ("📋", "Plan", "dim"),
    "edit": ("✏️ ", "Edit", "bold yellow"),
    "yolo": ("🚀", "YOLO", "bold red"),
    "control": ("🔐", "Control", "bold cyan"),
}


class StatusLine(Static):
    """底部状态栏（纯文本，无边框）"""

    def __init__(self, state: AppState):
        super().__init__("")
        self._state = state

    def render(self) -> Text:
        s = self._state
        parts = []

        # 执行模式
        mode = s.execution_mode
        icon, label, style = _MODE_LABELS.get(mode, ("✏️ ", "Edit", "bold yellow"))
        parts.append(f"[{style}]{icon} {label}[/{style}]")

        # 学习模式进度
        lp = s.learn_progress
        if lp["active"]:
            phase = lp.get("phase", "")
            phase_icons = {
                "start": "🚀",
                "open_app": "📂",
                "screenshot": "📷",
                "element_detect": "🔍",
                "planning": "📋",
                "executing": "🖱",
                "generating": "📦",
                "done": "✅",
            }
            icon = phase_icons.get(phase, "●")
            done = lp.get("steps_done", 0)
            total = lp.get("steps_total", 0)
            if phase == "executing" and total > 0:
                pct = int(done / total * 100)
                parts.append(f"[bold green]{icon} [{phase}] {done}/{total} ({pct}%)[/bold green]")
            else:
                parts.append(f"[bold green]{icon} [{phase}][/bold green]")

        if s.trace_id:
            parts.append(f"trace: {s.trace_id[:12]}")
        if s.tool_stats["total"]:
            parts.append(
                f"工具: {s.tool_stats['total']} "
                f"✓{s.tool_stats['success']}/✗{s.tool_stats['failed']}"
            )

        if s.processing and s.processing_start_time:
            elapsed_s = int(time.time() - s.processing_start_time)
            silence_s = int(time.time() - s.last_event_time) if s.last_event_time else 0

            if s.thinking_hint:
                parts.append(f"[magenta]{s.thinking_hint}[/magenta]")
            if silence_s >= 30:
                parts.append(
                    f"[bold yellow]处理中 {elapsed_s}s  静默 {silence_s}s ⚠[/bold yellow]"
                )
            else:
                parts.append(f"[cyan]处理中 {elapsed_s}s[/cyan]")
            if s.retry_count > 0:
                parts.append(f"[yellow]重试 {s.retry_count}/2[/yellow]")
        elif s.elapsed_ms:
            parts.append(f"耗时: {s.elapsed_ms:.0f}ms")

        parts.append("Shift+Tab 切换模式 │ ESC 停止思考 │ / 命令 │ Ctrl+C 退出")

        return Text.from_markup("  │  ".join(parts))
