"""桌宠窗口 — 透明置顶小窗 + Live2D 角色（稳定方案）

窗口只占角色区域（不挡桌面，无需鼠标穿透），角色区域内可交互
（单击开圆环互动菜单 / 拖动角色）。语音触发走后端（F8 / 唤醒词"科特"）。
"""
import json
import os
import sys
import time
import urllib.request

from PyQt6.QtCore import QTimer, Qt, QUrl
from PyQt6.QtGui import QColor
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QWidget

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_URL = os.environ.get("CORTEX_BACKEND_URL", "http://localhost:8080")
_PET_DIR = os.path.dirname(os.path.abspath(__file__))

PET_W, PET_H = 420, 680


def _pet_url(backend_url: str) -> QUrl:
    """Live2D wasm 需经 http 加载（file:// 被 Chromium 阻止），经后端 /pet/ 静态服务；
    加版本参数避免 QWebEngine 缓存旧页面"""
    return QUrl(f"{backend_url.rstrip('/')}/pet/index.html?v={int(time.time())}")


class PetWidget(QWidget):
    """桌宠：透明置顶小窗 + Live2D 角色 + 圆环互动 + 状态栏"""

    def __init__(self, backend_url: str = BACKEND_URL):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(PET_W, PET_H)
        self.backend_url = backend_url.rstrip("/")

        self.view = QWebEngineView(self)
        self.view.setGeometry(0, 0, PET_W, PET_H)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent;")
        _profile = QWebEngineProfile.defaultProfile()
        _profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        _profile.setHttpCacheMaximumSize(0)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.load(_pet_url(self.backend_url))

        self._cfg_timer = QTimer(self)
        self._cfg_timer.timeout.connect(self._check_pet_enabled)
        self._cfg_timer.start(5000)

        self._place_default()

    # ── 桌宠开关（DESKTOP_PET_ENABLED）实时控制窗口显示 ──

    def _check_pet_enabled(self):
        try:
            req = urllib.request.Request(
                f"{self.backend_url}/config",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                cfg = json.loads(resp.read().decode("utf-8")).get("data", {})
            want = bool(cfg.get("DESKTOP_PET_ENABLED", True))
        except Exception:
            want = True  # 后端不可达时默认显示
        if want != self.isVisible():
            self.setVisible(want)

    def _place_default(self):
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - PET_W // 2, geo.bottom() - PET_H - 8)


def create_pet_widget(backend_url: str = BACKEND_URL) -> PetWidget:
    w = PetWidget(backend_url)
    w.show()
    return w
