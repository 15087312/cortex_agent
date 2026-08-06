"""桌宠窗口 — 透明置顶小窗 + Live2D 角色（稳定方案）

窗口只占角色区域（不挡桌面，无需鼠标穿透）。
交互：页面内拖动 → QWebChannel 调 Qt 移动整个窗口；单击角色 → 圆环互动菜单。
语音触发走后端（F8 / 唤醒词"科特"→ 主会话对话）。
"""
import json
import os
import sys
import time
import urllib.request

from PyQt6.QtCore import QObject, QPoint, QTimer, Qt, QUrl, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWebChannel import QWebChannel
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


class _PetBridge(QObject):
    """页面 JS 经 QWebChannel 调用：拖动时移动整个窗口"""

    def __init__(self, pet: "PetWidget"):
        super().__init__()
        self._pet = pet

    @pyqtSlot(int, int)
    def moveWin(self, dx: int, dy: int):
        self._pet.move(self._pet.pos() + QPoint(dx, dy))


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
        # 用 defaultProfile（独立 QWebEngineProfile 的渲染进程在 macOS 无法建立
        # Mach 端口 rendezvous 而崩溃："No rendezvous client, terminating process"）
        _profile = QWebEngineProfile.defaultProfile()
        _profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        _profile.setHttpCacheMaximumSize(0)
        settings = self.view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)
        self.view.page().setBackgroundColor(QColor(0, 0, 0, 0))

        # QWebChannel（页面拖动移动窗口）——疑似 macOS 段错误源，暂时禁用定位
        # self._channel = QWebChannel(self.view.page())
        # self._channel.registerObject("petBridge", _PetBridge(self))
        # self.view.page().setWebChannel(self._channel)

        # 页面加载失败自动重试（cortex --qt 后端启动慢，桌宠早拉起时后端未就绪）
        self.view.page().loadFinished.connect(self._on_load_finished)
        self._load_page()

        self._cfg_timer = QTimer(self)
        self._cfg_timer.timeout.connect(self._check_pet_enabled)
        self._cfg_timer.start(5000)

        # 拖动：页面 fetch 后端累积位移 → Qt 轮询移动窗口（规避 QWebChannel 段错误）
        self._move_timer = QTimer(self)
        self._move_timer.timeout.connect(self._poll_move)
        self._move_timer.start(50)

        self._place_default()

    def _load_page(self):
        self.view.load(_pet_url(self.backend_url))

    def _on_load_finished(self, ok: bool):
        if not ok:
            print("[Pet] 页面加载失败，2s 后重试（等待后端就绪）", flush=True)
            QTimer.singleShot(2000, self._load_page)

    # ── 拖动：轮询后端累积位移，移动窗口 ──

    def _poll_move(self):
        try:
            req = urllib.request.Request(
                f"{self.backend_url}/stream/pet/move",
                headers={"Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=1) as resp:
                data = json.loads(resp.read().decode("utf-8")).get("data", {})
            dx = float(data.get("dx", 0))
            dy = float(data.get("dy", 0))
            if dx or dy:
                self.move(self.pos() + QPoint(int(dx), int(dy)))
        except Exception:
            pass

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
        if screen is None:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.center().x() - PET_W // 2, geo.bottom() - PET_H - 8)


def create_pet_widget(backend_url: str = BACKEND_URL) -> PetWidget:
    w = PetWidget(backend_url)
    w.show()
    w._place_default()  # show 后再定位（确保 screen 可用，避免窗口落到屏幕外）
    return w
