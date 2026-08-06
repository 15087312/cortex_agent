"""桌宠窗口 — 全屏透明覆盖层（借鉴 airi desktop-overlay）

全屏置顶透明，Live2D 角色 + 气泡（QWebEngineView）。
鼠标默认穿透（不影响桌面操作），仅在角色区域可交互。
语音触发走后端（F8 / 唤醒词"科特"→ 主会话对话 → TTS + 气泡）。
"""
import json
import os
import sys
import time
import urllib.request

from PyQt6.QtCore import QTimer, Qt, QUrl, QRect
from PyQt6.QtGui import QColor, QCursor
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QWidget

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_URL = os.environ.get("CORTEX_BACKEND_URL", "http://localhost:8080")
_PET_DIR = os.path.dirname(os.path.abspath(__file__))
_PET_HTML = os.path.join(_PET_DIR, "pet", "index.html")

# 角色可交互区域（紧贴角色默认大小：底部中间；拖动判定在页面内仅角色身上）
PET_ZONE_W, PET_ZONE_H = 190, 420


def _pet_url(backend_url: str) -> QUrl:
    """Live2D wasm 需经 http 加载（file:// 被 Chromium 阻止），经后端 /pet/ 静态服务；
    加版本参数避免 QWebEngine 缓存旧页面"""
    return QUrl(f"{backend_url.rstrip('/')}/pet/index.html?v={int(time.time())}")


class PetWidget(QWidget):
    """桌宠：全屏透明置顶覆盖层 + Live2D 角色 + 气泡 + 鼠标穿透"""

    def __init__(self, backend_url: str = BACKEND_URL):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.backend_url = backend_url.rstrip("/")
        self._passthrough = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.view = QWebEngineView(self)
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

        self._pt_timer = QTimer(self)
        self._pt_timer.timeout.connect(self._update_passthrough)
        self._pt_timer.start(120)

        self._cfg_timer = QTimer(self)
        self._cfg_timer.timeout.connect(self._check_pet_enabled)
        self._cfg_timer.start(5000)

        self._place_fullscreen()

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

    # ── 鼠标穿透：仅角色区域可交互 ──

    def _pet_zone(self) -> QRect:
        return QRect(
            int(self.width() / 2) - int(PET_ZONE_W / 2),
            self.height() - PET_ZONE_H,
            PET_ZONE_W,
            PET_ZONE_H,
        )

    def _update_passthrough(self):
        if not self.isVisible():
            return
        local = self.mapFromGlobal(QCursor.pos())
        want = not self._pet_zone().contains(local)
        if want != self._passthrough:
            self._passthrough = want
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, want)

    def _place_fullscreen(self):
        screen = self.screen()
        if screen is not None:
            self.setGeometry(screen.geometry())
            self.view.setGeometry(0, 0, self.width(), self.height())
        else:
            self.showMaximized()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "view"):
            self.view.setGeometry(0, 0, self.width(), self.height())


def create_pet_widget(backend_url: str = BACKEND_URL) -> PetWidget:
    w = PetWidget(backend_url)
    w.show()
    return w
