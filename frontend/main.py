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

from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon
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
_pet_proc = None


# ── App Icon ──────────────────────────────────────────────

def _make_app_icon():
    # 优先使用前端 logo（icon.png，透明背景 + 去水印）
    base = os.path.dirname(os.path.abspath(__file__))
    icon_path = os.path.join(base, "public", "icon.png")
    if os.path.isfile(icon_path):
        return QIcon(icon_path)
    # 回退: 旧版 JPEG (无透明, 有白边)
    icon_path = os.path.join(base, "public", "favicon.jpg")
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
        # Windows/Linux：关窗隐藏到托盘，可从托盘恢复（macOS 用 Dock，无需托盘）
        if sys.platform != "darwin":
            self._setup_tray()
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

    # ── 系统托盘（Windows/Linux：关窗后仍有入口恢复）──

    def _setup_tray(self):
        """创建系统托盘图标。关闭窗口只隐藏（closeEvent），从托盘可恢复/退出。"""
        try:
            self._tray = QSystemTrayIcon(self)
            self._tray.setIcon(self._app.windowIcon() or _make_app_icon())
            self._tray.setToolTip("Cortex Agent")
            menu = QMenu(self)
            show_action = menu.addAction("显示主窗口")
            show_action.triggered.connect(self._restore_from_tray)
            menu.addSeparator()
            quit_action = menu.addAction("退出")
            quit_action.triggered.connect(self._quit_app)
            self._tray.setContextMenu(menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
        except Exception:
            self._tray = None

    def _restore_from_tray(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._restore_from_tray()

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
        _stop_pet()
        _stop_server()
        self._app.quit()

    def closeEvent(self, event):
        self._settings.setValue("window/geometry", self.saveGeometry())
        self.browser.stop()
        self.hide()
        event.ignore()

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


def _stop_pet():
    """终止独立桌宠进程（Qt 退出时关闭宠物）"""
    global _pet_proc
    if _pet_proc is not None:
        try:
            if _pet_proc.poll() is None:
                _pet_proc.terminate()
                try:
                    _pet_proc.wait(timeout=3)
                except Exception:
                    _pet_proc.kill()
        except Exception:
            pass
        _pet_proc = None


atexit.register(_stop_pet)


def _port_in_use(port=8765):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def _ensure_backend(port=8080):
    """确保后端 API 已运行，未运行则拉起。

    - 打包版（PyInstaller）：启动同目录的 AI_Backend(.exe)
    - 开发版：以当前解释器起 uvicorn 子进程（cwd=项目根，保证 api.main 可导入）
    """
    if _port_in_use(port):
        return
    try:
        import subprocess
        if getattr(sys, "frozen", False):
            exe = os.path.join(
                os.path.dirname(sys.executable),
                "AI_Backend.exe" if sys.platform == "win32" else "AI_Backend",
            )
            if os.path.isfile(exe):
                subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            print("[..] 未找到同目录 AI_Backend，跳过后端启动", flush=True)
        else:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cmd = [sys.executable, "-m", "uvicorn", "api.main:app",
                   "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
            subprocess.Popen(cmd, cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[..] 后端启动失败: {e}", flush=True)


def main():
    global _server_thread

    # 未捕获异常打印（定位"自动退出"根因）
    def _excepthook(tp, val, tb):
        import traceback
        traceback.print_exception(tp, val, tb)
        print("[DBG] 未捕获异常导致退出", flush=True)
    sys.excepthook = _excepthook

    # 确保后端 API (8080) 已运行（Qt 页面 /api 请求都代理到它）
    print("[..] 检查后端 API 服务 (8080)...")
    _ensure_backend()

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
        _stop_pet()
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

    # 桌宠独立进程：先启动 Qt，再延迟拉起桌宠；Qt 退出时终止（_stop_pet / atexit / 信号）
    def _create_pet_later():
        global _pet_proc
        if os.environ.get("CORTEX_DISABLE_PET", "0") == "1":
            print("[..] 桌宠已禁用 (CORTEX_DISABLE_PET=1)")
            return
        if getattr(sys, "frozen", False):
            # 打包版不内置桌宠进程（避免额外打包一个 Qt 可执行文件）
            print("[..] 打包版跳过独立桌宠进程")
            return
        try:
            import subprocess as _sp
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pet_launch.py")
            _pet_proc = _sp.Popen([sys.executable, script])
            print("[OK] 桌宠已启动（独立进程）", flush=True)
        except Exception as e:
            print(f"[..] 桌宠启动失败: {e}")

    from PyQt6.QtCore import QTimer
    QTimer.singleShot(2500, _create_pet_later)

    exit_code = app.exec()
    _stop_pet()
    _stop_server()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
