"""消息列表 — 共享对话框 + AI 回复（滚动）"""

from textual.widgets import RichLog


TIER_COLORS = {
    "large": "bold blue",
    "supervisor": "bold yellow",
    "expert": "bold green",
    "user": "bold cyan",
    "thinking": "italic magenta",
    "system": "dim",
}
TIER_ICONS = {"large": "🧠", "supervisor": "📊", "expert": "🔧", "user": "👤", "thinking": "💭", "system": "⚙️"}
TIER_LABELS = {"large": "总指挥", "supervisor": "主管", "expert": "专家", "user": "用户", "thinking": "思考", "system": "系统"}
REFLECTION_COLORS = {"retry": "yellow", "rollback": "magenta", "terminate": "red", "ask_user": "cyan"}


class MessageList(RichLog):
    """显示共享对话框条目和 AI 最终回复，内置滚动"""

    def __init__(self):
        super().__init__(highlight=True, markup=True, wrap=True, max_lines=10000)
        self._total = 0

    def on_mount(self):
        self.border_title = "对话"
        self.write("[dim]等待模型对话...[/dim]")
        self._total = 0

    def add_dialog_entry(self, entry: dict):
        """添加一条对话框条目（去重后写入）"""
        tier = entry.get("tier", "?")
        entry_type = entry.get("entry_type", "")

        # 过滤系统消息，除非是特殊类型
        if tier == "system" and entry_type not in ("summary", "status"):
            return

        icon = TIER_ICONS.get(tier, "❓")
        label = TIER_LABELS.get(tier, tier)
        color = TIER_COLORS.get(tier, "dim")
        text = entry.get("content", "")
        rn = entry.get("round_num", 0)

        if entry_type == "thought":
            tag = f"R{rn}" if rn > 0 else ""
        elif entry_type == "response":
            tag = "回复"
        elif entry_type == "user_input":
            tag = "输入"
        else:
            tag = ""

        header = f"[{color}]{icon} {label}[/{color}]"
        if tag:
            header += f" [dim][{tag}][/dim]"

        lines = text.split("\n")
        self.write(f"{header} {lines[0] if lines else ''}")
        for line in lines[1:]:
            if line.strip():
                self.write(f"       [{color}]{line}[/{color}]")
        self._total += 1

    def add_response(self, text: str):
        """添加 AI 最终回复"""
        if not text:
            return
        self.write("")
        self.write("[bold green]🤖 AI 回复[/bold green]")
        self.write("─" * 40)
        self.write(text)
        self._total += 1

    def add_error(self, text: str):
        """添加错误消息"""
        self.write(f"[red]❌ {text}[/red]")
        self._total += 1

    def add_reflection_event(self, entry: dict):
        """添加反思诊断事件"""
        decision = entry.get("decision", "")
        color = REFLECTION_COLORS.get(decision, "dim")
        icon = {"retry": "🔄", "rollback": "↩️", "terminate": "🛑", "ask_user": "❓"}.get(decision, "🔍")
        reason = entry.get("error_reason", "")
        suggestion = entry.get("suggestion", "")
        retry = entry.get("retry_count", 0)
        node = entry.get("node", "")

        label = {"retry": "反思-重试", "rollback": "反思-回退", "terminate": "反思-终止", "ask_user": "反思-询问"}.get(decision, "反思")
        self.write(f"[{color}]{icon} {label}[/{color}] [dim]节点: {node} 重试: {retry}[/dim]")
        if reason:
            self.write(f"  [{color}]原因: {reason[:100]}[/{color}]")
        if suggestion:
            self.write(f"  [{color}]建议: {suggestion[:100]}[/{color}]")
        self._total += 1

    def reset_for_new_input(self):
        """新对话开始，添加分隔符（不清屏）"""
        if self._total > 0:
            self.write("[dim]" + "─" * 60 + "[/dim]")
        self._total = 0
