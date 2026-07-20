"""工具调用追踪面板"""

from rich.panel import Panel
from rich.table import Table
from rich import box
from textual.widgets import Static

from ..state import AppState

TOOL_COLORS = {
    "calc": "cyan", "memory_match": "yellow",
    "write_file": "magenta", "probe_start": "bold blue", "probe_stop": "bold red",
}


class ToolPanel(Static):
    """工具调用追踪和统计面板"""

    def __init__(self, state: AppState):
        super().__init__("")
        self._state = state

    def render(self) -> Panel:
        s = self._state

        if not s.show_tools or not s.tool_calls:
            return Panel(
                "[dim]暂无工具调用 ([green]/tools[/green] 切换)[/dim]",
                title="[bold]🔧 工具[/bold]",
                border_style="cyan",
            )

        table = Table(
            box=box.SIMPLE, show_header=True, header_style="bold",
            expand=True, padding=(0, 1),
        )
        table.add_column("时间", width=8)
        table.add_column("工具", width=12)
        table.add_column("状态", width=4)
        table.add_column("延迟", width=7)
        table.add_column("详情", width=30)

        for record in s.tool_calls[-15:]:
            tool = record.get("tool", "?")
            t_color = TOOL_COLORS.get(tool, "blue")
            success = record.get("success", True)
            status = "[green]✓[/green]" if success else "[red]✗[/red]"
            latency = f"{record.get('latency_ms', 0):.1f}ms"
            detail = str(record.get("params", ""))[:35] if record.get("params") else ""
            result = str(record.get("result", ""))[:25] if record.get("result") else ""
            if result and result != "null":
                detail += f" → {result}"
            error = record.get("error", "")
            if error:
                detail += f" [red]{error[:15]}[/red]"

            from datetime import datetime
            ts = datetime.fromtimestamp(record.get("timestamp", 0)).strftime("%H:%M:%S")
            table.add_row(ts, f"[{t_color}]{tool}[/{t_color}]", status, latency, detail)

        # 统计
        lines = [table]
        if s.tool_stats["total"]:
            avg = s.avg_latency_ms
            by_tool = {}
            for record in s.tool_calls:
                t = record.get("tool", "unknown")
                by_tool.setdefault(t, {"total": 0, "success": 0})
                by_tool[t]["total"] += 1
                if record.get("success"):
                    by_tool[t]["success"] += 1

            stats_lines = [
                f"\n[bold]总计: {s.tool_stats['total']}[/bold] | "
                f"[green]✓{s.tool_stats['success']}[/green] "
                f"[red]✗{s.tool_stats['failed']}[/red] | "
                f"均延: {avg:.1f}ms",
            ]
            for tool, counts in sorted(by_tool.items()):
                c = TOOL_COLORS.get(tool, "blue")
                stats_lines.append(f"  [{c}]{tool}[/{c}]: {counts['total']}")
            lines.append("\n".join(stats_lines))

        return Panel(
            "\n".join(lines),
            title="[bold]🔧 工具调用[/bold]",
            border_style="cyan",
        )
