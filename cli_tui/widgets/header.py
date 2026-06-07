"""顶栏 — 连接状态 + session + 统计"""

from datetime import datetime

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from ..state import AppState


class Header(Static):
    """顶栏：显示连接状态、session ID、对话/工具统计"""

    def __init__(self, state: AppState):
        super().__init__("")
        self._state = state

    def render(self) -> Panel:
        s = self._state
        color = "green" if s.connected else "red"
        dot = f"[{color}]●[/{color}]"
        status = f"{dot} {'已连接' if s.connected else '断开'}"

        parts = [status, f"会话: {s.session_id[:10] if s.session_id else '---'}..."]

        if s.dialog_entries:
            parts.append(f"对话: {len(s.dialog_entries)}")
        if s.elapsed_ms:
            parts.append(f"耗时: {s.elapsed_ms:.0f}ms")
        if s.tool_stats["total"]:
            parts.append(
                f"工具: {s.tool_stats['total']} "
                f"([green]{s.tool_stats['success']}✓[/green]/"
                f"[red]{s.tool_stats['failed']}✗[/red])"
            )
        if s.processing:
            parts.append("[yellow]处理中…[/yellow]")

        # 活跃专家/主管
        if s.active_experts:
            experts_str = ", ".join(s.active_experts[:3])
            if len(s.active_experts) > 3:
                experts_str += f" +{len(s.active_experts) - 3}"
            parts.append(f"[cyan]👥 {experts_str}[/cyan]")

        # 上下文窗口占用
        if s.context_tokens > 0:
            pct = s.context_tokens / s.context_window_size * 100 if s.context_window_size else 0
            used_k = s.context_tokens / 1000
            total_k = s.context_window_size / 1000
            if pct >= 80:
                ctx_str = f"[bold red]CTX {used_k:.1f}K/{total_k:.0f}K ({pct:.0f}%)[/bold red]"
            elif pct >= 50:
                ctx_str = f"[bold yellow]CTX {used_k:.1f}K/{total_k:.0f}K ({pct:.0f}%)[/bold yellow]"
            else:
                ctx_str = f"[green]CTX {used_k:.1f}K/{total_k:.0f}K ({pct:.0f}%)[/green]"
            parts.append(ctx_str)

        return Panel(
            "  │  ".join(parts),
            title="[bold]AI CLI[/bold]",
            border_style="blue",
        )
