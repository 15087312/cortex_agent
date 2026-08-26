"""cortex — macOS 桌面客户端
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

# 防残留: 若启动本客户端的 cortex 父进程被强杀，自动退出避免孤儿进程
try:
    from cortex.watchdog import enable as _enable_orphan_watchdog
    _enable_orphan_watchdog()
except Exception:
    pass

# ── 打包版（PyInstaller）运行时数据路径 ──
# 在 settings 单例实例化前设置环境变量，确保：
#  - embedding 模型从内置 _MEIPASS/data 加载（不联网下载）
#  - EMBEDDING_LOCAL_FILES_ONLY=True（避免网络依赖）
if getattr(sys, "frozen", False):
    _meipass = getattr(sys, "_MEIPASS", "") or os.path.dirname(sys.executable)
    _embed_dir = os.path.join(_meipass, "data", "memory", "embeddings", "models")
    if os.path.isdir(_embed_dir):
        os.environ.setdefault("EMBEDDING_CACHE_FOLDER", _embed_dir)
        os.environ.setdefault("EMBEDDING_LOCAL_FILES_ONLY", "True")
        print(f"[..] Embedding 模型内置: {_embed_dir}")

from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon
from PyQt6.QtCore import QUrl, QSettings, Qt, QRect, QLockFile
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
        self._settings = QSettings("cortex", "cortex")
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
        self.setWindowTitle("cortex")
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
            self._tray.setToolTip("cortex")
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
        app_menu = mb.addMenu("&cortex")

        a = QAction("About cortex", self)
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

        a = QAction("Quit cortex", self)
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
        # 从 frontend/package.json 读版本（与前端 __APP_VERSION__ 同源），不硬编码
        version = "v2.0.0"
        try:
            import json as _json
            pkg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "package.json")
            with open(pkg_path, encoding="utf-8") as _f:
                version = "v" + str(_json.load(_f).get("version", "2.0.0"))
        except Exception:
            pass
        QMessageBox.about(
            self, "About cortex",
            f"<h3>cortex</h3><p>{version}</p>"
            f"<p>AI 智能体后端系统 — Web UI 桌面客户端</p>"
            f"<p style='color:gray; font-size:11px'>桌面客户端</p>"
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


def _start_backend_thread(port):
    """在后台线程中启动 uvicorn 后端（单 exe 方案：不再拉起独立 AI_Backend.exe）。

    打包版里 api.main 等模块已由 PyInstaller 收集进同一 PYZ，可直接以字符串导入。
    端口选择 + 落盘逻辑与后端启动保持一致。
    """
    import uvicorn
    from utils.port_discovery import pick_free_port, save_backend_port

    _port = pick_free_port(port)
    save_backend_port(_port)
    if _port != port:
        print(f"[Cortex] 端口 {port} 被占用，已自动改用端口 {_port}", flush=True)
    uvicorn.run("api.main:app", host="127.0.0.1", port=_port, log_level="warning")


def _ensure_backend(port=8080):
    """确保后端 API 已运行，未运行则启动；端口被占时自动回退到空闲端口。

    - 打包版（PyInstaller 单 exe）：在本进程后台线程启动 uvicorn
    - 开发版：以当前解释器起 uvicorn 子进程（cwd=项目根，保证 api.main 可导入）
    """
    try:
        from utils.port_discovery import pick_free_port, probe_health, read_backend_port
        # 已有一个健康后端（按发现文件 / 探测）→ 直接用，不重复启动
        if probe_health(read_backend_port()):
            return
        port = pick_free_port(port)
        if probe_health(port):
            return
    except Exception:
        pass

    try:
        env = dict(os.environ)
        env["SERVER_PORT"] = str(port)
        if getattr(sys, "frozen", False):
            _backend_thread = threading.Thread(
                target=_start_backend_thread, args=(port,), daemon=True
            )
            _backend_thread.start()
            print("[..] 已在后台线程启动后端 API", flush=True)
        else:
            import subprocess
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cmd = [sys.executable, "-m", "uvicorn", "api.main:app",
                   "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
            subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[..] 后端启动失败: {e}", flush=True)


def _setup_qtwebengine_paths():
    """PyInstaller 打包版显式指定 QtWebEngine 的 process/资源/语言路径。

    PyInstaller 收集 PyQt6 的 QtWebEngineCore.framework 后，Qt 默认路径推断
    找不到 QtWebEngineProcess（黑屏/空白窗口）。macOS framework 布局与
    Windows/Linux 不同，需按平台分别处理。
    """
    if not getattr(sys, "frozen", False):
        return
    base = getattr(sys, "_MEIPASS", "") or os.path.dirname(sys.executable)
    if sys.platform == "darwin":
        fw = os.path.join(base, "PyQt6", "Qt6", "lib", "QtWebEngineCore.framework")
        proc = os.path.join(fw, "Versions", "A", "Helpers",
                            "QtWebEngineProcess.app", "Contents", "MacOS", "QtWebEngineProcess")
        res = os.path.join(fw, "Versions", "A", "Resources")
        loc = os.path.join(res, "qtwebengine_locales")
    else:
        # Windows/Linux：PyQt6 常规布局
        proc = os.path.join(base, "PyQt6", "Qt6", "bin",
                            "QtWebEngineProcess.exe" if sys.platform == "win32" else "QtWebEngineProcess")
        res = os.path.join(base, "PyQt6", "Qt6", "resources")
        loc = os.path.join(res, "translations", "qtwebengine_locales")

    if os.path.isfile(proc):
        os.environ["QTWEBENGINEPROCESS_PATH"] = proc
        print(f"[..] QTWEBENGINEPROCESS_PATH={proc}")
    if os.path.isdir(res):
        os.environ["QTWEBENGINE_RESOURCES_PATH"] = res
        print(f"[..] QTWEBENGINE_RESOURCES_PATH={res}")
    if os.path.isdir(loc):
        os.environ["QTWEBENGINE_LOCALES_PATH"] = loc
        print(f"[..] QTWEBENGINE_LOCALES_PATH={loc}")


def main():
    global _server_thread

    # 未捕获异常打印（定位"自动退出"根因）
    def _excepthook(tp, val, tb):
        import traceback
        traceback.print_exception(tp, val, tb)
        print("[DBG] 未捕获异常导致退出", flush=True)
    sys.excepthook = _excepthook

    # 打包版：预先设置 QtWebEngine 路径（必须在 QApplication/WebEngine 初始化前）
    _setup_qtwebengine_paths()

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
    app.setApplicationName("cortex")
    app.setOrganizationName("cortex")
    app.setWindowIcon(_make_app_icon())
    app.setQuitOnLastWindowClosed(False)

    # ── 单实例锁：同一时间只允许一个实例，避免重复启动多个窗口 ──
    # 用 QLockFile 检测是否已有实例在运行；有则静默退出（不新开窗口、不弹窗）。
    import tempfile
    _lock_path = os.path.join(tempfile.gettempdir(), "cortex_single_instance.lock")
    _instance_lock = QLockFile(_lock_path)
    _instance_lock.setStaleLockTime(0)
    if not _instance_lock.tryLock(100):
        print("[..] cortex 已在运行，本次启动退出", flush=True)
        sys.exit(0)

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
    print("[OK] cortex 已启动")
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
    # PyInstaller + multiprocessing：spawn 子进程（resource_tracker/worker）也会进入
    # __main__。必须只在真正的主进程创建 GUI，否则会重复创建窗口/弹"已在运行"。
    try:
        import multiprocessing
        if multiprocessing.current_process().name != "MainProcess":
            # 这是被 multiprocessing spawn 出的子进程：不创建 GUI，直接退出。
            # （后端 API 由主进程内的后台线程承载，无需子进程。）
            print("[..] 检测到非主进程，跳过 GUI 启动", flush=True)
            sys.exit(0)
    except Exception:
        pass
    main()
