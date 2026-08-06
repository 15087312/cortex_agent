"""Cortex Agent — macOS 桌面客户端
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
在后台启动 server.py，然后打开 Qt 窗口加载 Web UI。
关闭窗口时隐藏到 Dock，Cmd+Q 完全退出。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sys
import os
import threading
import time
import signal
import atexit
import re

# 防残留: 若启动本客户端的 cortex 父进程被强杀，自动退出避免孤儿进程
try:
    from cortex.watchdog import enable as _enable_orphan_watchdog
    _enable_orphan_watchdog()
except Exception:
    pass

from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PyQt6.QtCore import QUrl, QSettings, Qt, QRect
from PyQt6.QtGui import (
    QAction, QKeySequence, QIcon, QPixmap, QPainter,
    QColor, QFont, QPalette, QBrush, QPen, QLinearGradient,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage, QWebEngineSettings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_server_instance = None
_server_thread = None
_pet = None


# ── App Icon ──────────────────────────────────────────────

def _make_app_icon():
    # 优先使用前端 logo（favicon.jpg，页面左上角鹿图）
    icon_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "public", "favicon.jpg"
    )
    if os.path.isfile(icon_path):
        return QIcon(icon_path)
    # 回退：程序化绘制蓝色 "C" 图标
    size = 256
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor("#58a6ff"))
    gradient.setColorAt(1.0, QColor("#1f6feb"))
    p.setBrush(QBrush(gradient))
    p.setPen(QPen(QColor("#58a6ff"), 3))
    p.drawRoundedRect(8, 8, size - 16, size - 16, size // 4, size // 4)
    p.setPen(QPen(QColor("#ffffff"), 18))
    font = QFont("SF Pro Display, Helvetica Neue, Helvetica, Arial", 160, QFont.Weight.Bold)
    p.setFont(font)
    p.drawText(QRect(0, 20, size, size), Qt.AlignmentFlag.AlignCenter, "C")
    p.end()
    return QIcon(pixmap)


# ── Theme ─────────────────────────────────────────────────

def _is_dark_mode(app):
    return app.styleHints().colorScheme() == Qt.ColorScheme.Dark


def _window_bg(app):
    return QColor("#0d1117") if _is_dark_mode(app) else QColor("#ffffff")


# ── Main Window ───────────────────────────────────────────

class MainWindow(QMainWindow):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self._settings = QSettings("Cortex Agent", "Cortex Agent")
        self._dev_tools = None
        self._setup_window()
        self._setup_browser()
        self._setup_menus()
        self._restore_geometry()
        app.paletteChanged.connect(self._on_palette_change)

    def _setup_window(self):
        self.setWindowTitle("Cortex Agent")
        self.setMinimumSize(900, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self._apply_theme()

    def _apply_theme(self):
        bg = _window_bg(self._app)
        self.setStyleSheet(f"QMainWindow {{ background-color: {bg.name()}; }}")

    def _on_palette_change(self, palette):
        self._apply_theme()

    def _setup_menus(self):
        mb = self.menuBar()

        # ── App Menu ───────────────────────────────────
        # macOS: actions with AboutRole/QuitRole/HideRole
        # are automatically moved to the system app menu.
        app_menu = mb.addMenu("&Cortex Agent")

        a = QAction("About Cortex Agent", self)
        a.setMenuRole(QAction.MenuRole.AboutRole)
        a.triggered.connect(self._show_about)
        app_menu.addAction(a)

        app_menu.addSeparator()

        a = QAction("Preferences...", self)
        a.setMenuRole(QAction.MenuRole.PreferencesRole)
        a.setShortcut(QKeySequence("Ctrl+,"))
        a.triggered.connect(self._show_preferences)
        app_menu.addAction(a)

        app_menu.addSeparator()

        a = QAction("Quit Cortex Agent", self)
        a.setMenuRole(QAction.MenuRole.QuitRole)
        a.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
        a.triggered.connect(self._quit_app)
        app_menu.addAction(a)

        # ── File Menu ─────────────────────────────────
        fm = mb.addMenu("&File")

        a = QAction("Reload Page", self)
        a.setShortcut(QKeySequence(QKeySequence.StandardKey.Refresh))
        a.triggered.connect(self.browser.reload)
        fm.addAction(a)

        fm.addSeparator()

        a = QAction("Close Window", self)
        a.setShortcut(QKeySequence(QKeySequence.StandardKey.Close))
        a.triggered.connect(self.close)
        fm.addAction(a)

        # ── Edit Menu ─────────────────────────────────
        em = mb.addMenu("&Edit")
        self._add_web_action(em, "Undo", QKeySequence.StandardKey.Undo,
                             QWebEnginePage.WebAction.Undo)
        self._add_web_action(em, "Redo", QKeySequence.StandardKey.Redo,
                             QWebEnginePage.WebAction.Redo)
        em.addSeparator()
        self._add_web_action(em, "Cut", QKeySequence.StandardKey.Cut,
                             QWebEnginePage.WebAction.Cut)
        self._add_web_action(em, "Copy", QKeySequence.StandardKey.Copy,
                             QWebEnginePage.WebAction.Copy)
        self._add_web_action(em, "Paste", QKeySequence.StandardKey.Paste,
                             QWebEnginePage.WebAction.Paste)
        em.addSeparator()
        self._add_web_action(em, "Select All", QKeySequence.StandardKey.SelectAll,
                             QWebEnginePage.WebAction.SelectAll)

        # ── View Menu ─────────────────────────────────
        vm = mb.addMenu("&View")

        a = QAction("Zoom In", self)
        a.setShortcut(QKeySequence(QKeySequence.StandardKey.ZoomIn))
        a.triggered.connect(lambda: self.browser.setZoomFactor(
            self.browser.zoomFactor() + 0.1))
        vm.addAction(a)

        a = QAction("Zoom Out", self)
        a.setShortcut(QKeySequence(QKeySequence.StandardKey.ZoomOut))
        a.triggered.connect(lambda: self.browser.setZoomFactor(
            self.browser.zoomFactor() - 0.1))
        vm.addAction(a)

        a = QAction("Actual Size", self)
        a.setShortcut(QKeySequence("Ctrl+0"))
        a.triggered.connect(lambda: self.browser.setZoomFactor(1.0))
        vm.addAction(a)

        vm.addSeparator()

        a = QAction("Toggle Developer Tools", self)
        a.setShortcut(QKeySequence("Ctrl+Alt+I"))
        a.triggered.connect(self._toggle_dev_tools)
        vm.addAction(a)

        # ── Window Menu ───────────────────────────────
        wm = mb.addMenu("&Window")

        a = QAction("Minimize", self)
        a.setShortcut(QKeySequence("Ctrl+M"))
        a.triggered.connect(self.showMinimized)
        wm.addAction(a)

        a = QAction("Zoom", self)
        a.triggered.connect(
            lambda: (self.showNormal() if self.isMaximized()
                     else self.showMaximized())
        )
        wm.addAction(a)

    def _add_web_action(self, menu, label, shortcut, web_action):
        a = QAction(label, self)
        a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(
            lambda: self.browser.page().triggerAction(web_action)
        )
        menu.addAction(a)

    def _setup_browser(self):
        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("http://localhost:8765"))
        self.setCentralWidget(self.browser)

    def _restore_geometry(self):
        geom = self._settings.value("window/geometry")
        if geom:
            self.restoreGeometry(geom)
        else:
            screen = self._app.primaryScreen()
            if screen:
                center = screen.availableGeometry().center()
                self.resize(1280, 800)
                self.move(center.x() - 640, center.y() - 400)

    def _show_about(self):
        version = "v2.0.0"
        idx = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "index.html")
        if os.path.isfile(idx):
            m = re.search(r'<span class="version">([^<]+)</span>',
                          open(idx).read())
            if m:
                version = m.group(1)
        QMessageBox.about(
            self, "About Cortex Agent",
            f"<h3>Cortex Agent</h3><p>{version}</p>"
            f"<p>AI 智能体后端系统 — Web UI 桌面客户端</p>"
            f"<p style='color:gray; font-size:11px'>macOS 原生版</p>"
        )

    def _show_preferences(self):
        QMessageBox.information(
            self, "Preferences",
            "偏好设置将通过 Web UI 页面提供。"
        )

    def _toggle_dev_tools(self):
        if self._dev_tools is not None:
            self._dev_tools.close()
            self._dev_tools = None
        else:
            self._dev_tools = QWebEngineView()
            self.browser.page().setDevToolsPage(self._dev_tools)
            self._dev_tools.show()

    def _quit_app(self):
        global _pet
        if _pet is not None:
            try:
                _pet.close()
            except Exception:
                pass
        _stop_server()
        self._app.quit()

    def closeEvent(self, event):
        global _pet
        self._settings.setValue("window/geometry", self.saveGeometry())
        self.browser.stop()
        # 桌宠跟随前端：隐藏到 Dock 时桌宠一起隐藏
        if _pet is not None:
            try:
                _pet.hide()
            except Exception:
                pass
        self.hide()
        event.ignore()

    def showEvent(self, event):
        super().showEvent(event)
        global _pet
        if _pet is not None:
            try:
                _pet.show()
            except Exception:
                pass

    def changeEvent(self, event):
        if event.type() == event.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                self._settings.setValue("window/geometry", self.saveGeometry())
        super().changeEvent(event)


# ── Server Lifecycle ──────────────────────────────────────

def _start_server():
    global _server_instance
    _server_instance = server.create_server()
    _server_instance.serve_forever()


def _stop_server():
    global _server_instance
    if _server_instance is not None:
        try:
            _server_instance.shutdown()
            print("[OK] 服务已停止")
        except:
            pass


def _port_in_use(port=8765):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main():
    global _server_thread

    if _port_in_use():
        print("[OK] 前端服务已在运行 (端口 8765)")
    else:
        print("[..] 启动前端服务...")
        _server_thread = threading.Thread(target=_start_server, daemon=True)
        _server_thread.start()
        time.sleep(0.5)

    atexit.register(_stop_server)

    def _signal_handler(signum, frame):
        print("\n[OK] 收到终止信号，正在关闭...")
        _stop_server()
        sys.exit(0)

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    app = QApplication(sys.argv)
    app.setApplicationName("Cortex Agent")
    app.setOrganizationName("Cortex Agent")
    app.setWindowIcon(_make_app_icon())
    app.setQuitOnLastWindowClosed(False)

    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setHttpCacheMaximumSize(0)
    # 允许网页访问剪贴板，否则前端「复制」按钮的 navigator.clipboard 不可用
    # （必须在 QApplication 创建后调用 defaultProfile()，其 settings() 才是有效的全局设置）
    profile.settings().setAttribute(
        QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, True
    )

    window = MainWindow(app)
    window.show()
    print("[OK] Cortex Agent 已启动")
    print("[..] 如果窗口未自动加载，请手动打开 http://localhost:8765")

    # 桌宠窗口（无边框置顶透明小窗，语音触发与主会话对话）——跟随前端生命周期
    global _pet
    try:
        from pet_widget import create_pet_widget
        _pet = create_pet_widget()
        # 检测桌宠渲染进程崩溃（macOS 透明 WebEngine 偶发）
        try:
            _pet.view.page().renderProcessTerminated.connect(
                lambda status, code, desc: print(
                    f"[DBG] 桌宠渲染进程终止: status={status} code={code} {desc}", flush=True
                )
            )
        except Exception:
            pass
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[..] 桌宠启动失败: {e}")

    try:
        window.browser.page().renderProcessTerminated.connect(
            lambda status, code, desc: print(
                f"[DBG] 主窗口渲染进程终止: status={status} code={code} {desc}", flush=True
            )
        )
    except Exception:
        pass

    exit_code = app.exec()
    print(f"[DBG] app.exec 返回: {exit_code}", flush=True)
    _stop_server()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
