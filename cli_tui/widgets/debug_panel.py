"""调试面板 — 显示阶段、卡点和最近事件"""

from datetime import datetime
from rich.panel import Panel
from rich.table import Table
from rich import box
from textual.widgets import Static

from ..state import AppState


class DebugPanel(Static):
    def __init__(self, state: AppState):
        super().__init__("")
        self._state = state

    def render(self) -> Panel:
        s = self._state
        if not s.debug_enabled:
            return Panel(
                "[dim]调试关闭 ([green]/debug[/green] 打开)[/dim]",
                title="[bold]🧪 调试[/bold]",
                border_style="dim",
            )

        lines = []
        if s.debug_phase:
            badge = s.debug_badge or s.debug_phase
            lines.append(f"[bold cyan]阶段:[/bold cyan] {badge}  [dim]({s.debug_phase})[/dim]")
        if s.thinking_hint:
            lines.append(f"[magenta]提示:[/magenta] {s.thinking_hint}")
        if s.last_error:
            lines.append(f"[bold red]最后错误:[/bold red] {s.last_error[:180]}")
        if s.processing_start_time:
            elapsed = int(datetime.now().timestamp() - s.processing_start_time)
            lines.append(f"[yellow]运行:[/yellow] {elapsed}s")
        if s.last_event_time:
            silence = int(datetime.now().timestamp() - s.last_event_time)
            lines.append(f"[yellow]静默:[/yellow] {silence}s")

        if s.debug_card:
            card = s.debug_card
            lines.append(
                "\n".join(
                    [
                        f"[green]来源:[/green] {card.get('source', '-')}",
                        f"[green]卡点:[/green] {card.get('bottleneck', '-')}",
                        f"[green]最后事件:[/green] {card.get('last_event', '-')}",
                    ]
                )
            )

        events = s.debug_events[-6:]
        if events:
            table = Table(box=box.SIMPLE, show_header=True, header_style="bold", expand=True)
            table.add_column("时间", width=8)
            table.add_column("阶段", width=12)
            table.add_column("内容", width=38)
            for ev in events:
                ts = datetime.fromtimestamp(ev.get("timestamp", 0)).strftime("%H:%M:%S")
                table.add_row(ts, ev.get("phase", "-"), str(ev.get("content", ""))[:40])
            lines.append("")
            lines.append(str(table))

        return Panel(
            "\n".join(lines) if lines else "[dim]暂无调试信息[/dim]",
            title="[bold]🧪 调试[/bold]",
            border_style="magenta",
        )
