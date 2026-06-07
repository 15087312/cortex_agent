"""帮助屏幕"""

from textual.app import ComposeResult
from textual.containers import Vertical, Container
from textual.screen import ModalScreen
from textual.widgets import Static, Button


HELP_TEXT = """
[bold]命令帮助[/bold]

[bold]对话[/bold]
  直接输入文字与 AI 对话

[bold]命令[/bold]
  /help, /h, /?     查看帮助
  /status, /s       查看系统状态
  /memory, /mem     查看记忆状态
  /context, /ctx    加载并显示当前上下文
  /search <query>   搜索长期记忆 (示例: /search 编程)
  /session <主管>   查看副会话内容
  /tools, /t        切换工具调用面板
  /debug, /d        切换调试面板
  /thinking, /th    切换思考过程显示
  /clear, /c        清空显示
  /export, /e       导出工具调用为 JSON
  /exit, /q, /quit  退出

[bold]功能说明[/bold]
  • 每次对话时自动加载相关上下文和个性配置
  • 系统提示词已隐去，仅显示核心对话内容
  • 使用 /context 查看当前加载的记忆和配置
  • 使用 /search 搜索过去的对话记录和思考

[bold]层级说明[/bold]
  🧠 总指挥 - 大模型 (统筹全局)
  📊 主管   - 监督模型 (分配任务)
  🔧 专家   - 专家模型 (执行具体工作)
"""


class HelpScreen(ModalScreen[None]):
    """帮助弹窗 — ModalScreen"""

    CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-dialog {
        width: 50;
        height: auto;
        max-height: 30;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(HELP_TEXT)
            yield Button("关闭 [Esc]", variant="primary", id="close")

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(None)

    def on_key(self, event):
        if event.key == "escape":
            self.dismiss(None)
