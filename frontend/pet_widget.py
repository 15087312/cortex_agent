"""桌宠窗口（借鉴 airi / codex 桌宠）

无边框置顶透明小窗：宠物形象 + 聊天气泡 + 说话浮动动画。
轮询后端 /stream/pet/last-reply 展示桌宠回复；语音输入走后端唤醒词。
"""
import json
import os
import sys
import time
import urllib.request

from PyQt6.QtCore import QTimer, QPropertyAnimation, QPoint, QEasingCurve, Qt
from PyQt6.QtGui import QPixmap, QPainter, QColor, QBrush, QPen
from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout, QSizePolicy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKEND_URL = os.environ.get("CORTEX_BACKEND_URL", "http://localhost:8080")
_PET_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ICON = os.path.join(_PET_DIR, "public", "favicon.jpg")


class PetWidget(QWidget):
    """桌宠：透明置顶小窗，可拖动，气泡显示回复"""

    def __init__(self, backend_url: str = BACKEND_URL):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.backend_url = backend_url.rstrip("/")
        self._last_time = 0.0
        self._drag_offset = None

        self._build_ui()
        self._start_float_animation()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(1500)

        self._place_default()

    # ── UI ──

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.bubble = QLabel("你好呀，我是桌宠～ 对我说：\u201c科特\u201d 即可聊天")
        self.bubble.setWordWrap(True)
        self.bubble.setMaximumWidth(240)
        self.bubble.setStyleSheet(
            "QLabel {"
            " background: rgba(255,255,255,235);"
            " color: #333;"
            " border-radius: 12px;"
            " padding: 10px 12px;"
            " font-size: 13px;"
            "}"
        )
        self.bubble.setVisible(False)
        bubble_row = QHBoxLayout()
        bubble_row.addStretch(1)
        bubble_row.addWidget(self.bubble, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(bubble_row)

        pet_row = QHBoxLayout()
        pet_row.addStretch(1)
        self.pet = QLabel()
        self.pet.setFixedSize(132, 132)
        self.pet.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._load_pet_pixmap()
        pet_row.addWidget(self.pet, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(pet_row)

        self.setFixedSize(260, 220)

    def _load_pet_pixmap(self):
        path = _DEFAULT_ICON
        if not os.path.exists(path):
            path = None
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self._draw_fallback()
            return
        self.pet.setPixmap(
            pixmap.scaled(120, 120, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
        )

    def _draw_fallback(self):
        # 无图标时画一个圆脸宠物
        pixmap = QPixmap(120, 120)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor(245, 166, 35)))
        p.setPen(QPen(QColor(245, 166, 35)))
        p.drawEllipse(10, 10, 100, 100)
        p.setPen(QPen(QColor(60, 60, 60), 4))
        p.drawPoint(45, 50)
        p.drawPoint(75, 50)
        p.setBrush(QBrush(QColor(60, 60, 60)))
        p.drawChord(35, 55, 50, 42, 200 * 16, 140 * 16)
        p.end()
        self.pet.setPixmap(pixmap)

    def _start_float_animation(self):
        self._float_anim = QPropertyAnimation(self.pet, b"pos", self)
        self._float_anim.setDuration(1600)
        self._float_anim.setLoopCount(-1)
        self._float_anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._float_anim.setStartValue(QPoint(118, 56))
        self._float_anim.setKeyValueAt(0.5, QPoint(118, 46))
        self._float_anim.setEndValue(QPoint(118, 56))
        self._float_anim.start()

    # ── 轮询 ──

    def _poll(self):
        try:
            req = urllib.request.Request(
                f"{self.backend_url}/stream/pet/last-reply", headers={"Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read().decode("utf-8")).get("data")
            if not data:
                return
            t = float(data.get("time", 0))
            if t and t != self._last_time:
                self._last_time = t
                self.show_bubble(data.get("text", ""))
        except Exception:
            pass

    def show_bubble(self, text: str):
        if not text:
            return
        self.bubble.setText(text)
        self.bubble.adjustSize()
        self.bubble.setVisible(True)
        self.bubble.raise_()
        QTimer.singleShot(9000, lambda: self.bubble.setVisible(False))

    # ── 拖动 ──

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event):
        self.setVisible(not self.isVisible())

    def _place_default(self):
        screen = self.screen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.right() - self.width() - 30, geo.bottom() - self.height() - 40)


def create_pet_widget(backend_url: str = BACKEND_URL) -> PetWidget:
    w = PetWidget(backend_url)
    w.show()
    return w
