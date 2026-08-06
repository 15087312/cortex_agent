"""桌宠窗口 — Live2D 动漫角色（借鉴 airi）

QWebEngineView 加载本地 Live2D 桌宠页（透明置顶无边框），
Qt 轮询后端 /stream/pet/last-reply，注入 JS 显示气泡与说话动画。
语音触发走后端（F8 / 唤醒词"科特"→ 主会话对话）。
"""
import json
import os
import sys
import time
import urllib.request

from PyQt6.QtCore import QTimer, Qt, QUrl, QPoint
from PyQt6.QtGui import QColor
from PyQt6.QtWebEngineCore import QWebEngineSettings
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QWidget

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_URL = os.environ.get("CORTEX_BACKEND_URL", "http://localhost:8080")
_PET_DIR = os.path.dirname(os.path.abspath(__file__))
_PET_HTML = os.path.join(_PET_DIR, "pet", "index.html")


class _DragView(QWebEngineView):
    """可拖动 + 双击隐藏的 WebEngine 视图"""

    def __init__(self, parent):
        super().__init__(parent)
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.parentWidget().frameGeometry().topLeft()
            )
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.parentWidget().move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        self.parentWidget().setVisible(not self.parentWidget().isVisible())
        super().mouseDoubleClickEvent(event)


class PetWidget(QWidget):
    """桌宠：透明置顶小窗 + Live2D 角色 + 气泡"""

    def __init__(self, backend_url: str = BACKEND_URL):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(320, 540)
        self.backend_url = backend_url.rstrip("/")
        self._last_time = 0.0
        self._bubble_timer = None

        self.view = _DragView(self)
        self.view.setGeometry(0, 0, 320, 540)
        self.view.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.view.setStyleSheet("background: transparent;")
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))
        self.view.load(QUrl.fromLocalFile(_PET_HTML))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(1200)

        self._place_default()

    # ── 轮询后端 → JS 注入 ──

    def _poll(self):
        try:
            req = urllib.request.Request(
                f"{self.backend_url}/stream/pet/last-reply",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8")).get("data")
            if not data:
                return
            t = float(data.get("time", 0))
            if t and t != self._last_time:
                self._last_time = t
                text = (data.get("text") or "").replace("\\", "\\\\").replace("'", "\\'")
                self.view.page().runJavaScript(
                    f"showReply('{text}')" if text else "speak(false)"
                )
        except Exception:
            pass

    def _place_default(self):
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.width() - 10, geo.bottom() - self.height() - 10)


def create_pet_widget(backend_url: str = BACKEND_URL) -> PetWidget:
    w = PetWidget(backend_url)
    w.show()
    return w
