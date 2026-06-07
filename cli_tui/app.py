"""应用入口 — Textual App"""

from textual.app import App

from .screens.help_screen import HelpScreen
from .screens.repl import REPL
from .services.api_client import APIClient
from .services.ws_client import WSClient
from .state import AppState


class AICLIApp(App):
    """多模型协作 TUI App — 类似 Open-ClaudeCode 的 main.tsx"""

    BINDINGS = [
        ("ctrl+q", "quit", "退出"),
    ]

    def __init__(self, api_url: str = "http://localhost:8080", api_key: str = ""):
        super().__init__()
        self.api_url = api_url
        self.app_state = AppState(api_url=api_url)
        self.ws_client = WSClient(api_url=api_url, api_key=api_key)
        self.api_client = APIClient(api_url=api_url)

    def on_mount(self):
        """启动后安装 screen"""
        self.install_screen(HelpScreen(), name="help")
        self.push_screen(
            REPL(self.app_state, self.ws_client, self.api_client)
        )

    def action_quit(self):
        """退出应用"""
        self.app_state.connected = False
        self.exit()
