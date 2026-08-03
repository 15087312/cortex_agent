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
  /session          查看会话列表并选择切换
  /history          查看对话历史
  /history edit <n>  编辑第 n 条对话（保存后下次生效）
  /tools, /t        切换工具调用面板
  /debug, /d        切换调试面板
  /thinking, /th    切换思考过程显示
  /clear, /c        清空显示
  /mode <模式>      切换执行模式 (plan / edit / yolo / control)
  /config           查看或修改配置
  /stop             停止当前思考
  /exit, /q, /quit  退出
  Ctrl+Y            重试上一次请求
  Shift+Tab         循环切换执行模式

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
