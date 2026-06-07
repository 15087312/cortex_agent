"""底栏 — 模型信息 + 快捷键提示"""

import time
from rich.panel import Panel
from textual.widgets import Static

from ..state import AppState


class StatusLine(Static):
    """底部状态栏"""

    def __init__(self, state: AppState):
        super().__init__("")
        self._state = state

    def render(self) -> Panel:
        s = self._state
        parts = []

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

        parts.append("/help 帮助 | /exit 退出 | Ctrl+X 取消")

        return Panel("  │  ".join(parts), border_style="dim")
