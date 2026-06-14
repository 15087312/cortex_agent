"""顶栏 — 连接状态 + session + 统计 + 版本"""

from datetime import datetime

from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from ..state import AppState


def _get_version() -> str:
    """获取版本信息"""
    try:
        from cortex.version import __version__, __version_name__
        return f"{__version__} ({__version_name__})"
    except ImportError:
        return "unknown"


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
        # 大模型身份
        if s.large_model_identity.get("name"):
            identity_name = s.large_model_identity["name"]
            parts.append(f"[bold]🧠 {identity_name}[/bold]")
        # 激活的 skill
        if s.active_skill:
            parts.append(f"[magenta]🎯 {s.active_skill}[/magenta]")

        if s.processing:
            parts.append("[yellow]处理中…[/yellow]")

        # 活跃主管
        if s.active_supervisors:
            sv_names = ", ".join(sv.get("name", "") for sv in s.active_supervisors[:2])
            if len(s.active_supervisors) > 2:
                sv_names += f" +{len(s.active_supervisors) - 2}"
            parts.append(f"[cyan]主管: {sv_names}[/cyan]")

        # 活跃专家（带所属主管）
        if s.active_experts:
            expert_items = []
            for exp in s.active_experts[:4]:
                name = exp.get("name", "")
                sup = exp.get("supervisor", "")
                if sup:
                    expert_items.append(f"{name}({sup})")
                else:
                    expert_items.append(name)
            experts_str = ", ".join(expert_items)
            if len(s.active_experts) > 4:
                experts_str += f" +{len(s.active_experts) - 4}"
            parts.append(f"[dim]👥 {experts_str}[/dim]")

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

        version = _get_version()
        return Panel(
            "  │  ".join(parts),
            title=f"[bold]AI CLI[/bold] [dim]{version}[/dim]",
            border_style="blue",
        )
