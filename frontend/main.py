"""Cortex Agent — Qt 桌面客户端
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
在后台启动 server.py，然后打开 Qt 窗口加载 Web UI。
关闭窗口时自动停止服务。
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sys
import os

# 解决 GBK 编码问题
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import threading
import time
import signal
import atexit

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtCore import QUrl, QTimer
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile

# 确保能导入 server.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

_server_instance = None
_server_thread = None


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cortex Agent")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)
        self.setStyleSheet("QMainWindow { background-color: #0d1117; }")

        self.browser = QWebEngineView()
        self.browser.setUrl(QUrl("http://localhost:8765"))
        self.setCentralWidget(self.browser)

    def closeEvent(self, event):
        self.browser.stop()
        self.browser.page().deleteLater()
        event.accept()


def _start_server():
    global _server_instance
    _server_instance = server.create_server()
    _server_instance.serve_forever()


def _stop_server():
    global _server_instance
    if _server_instance is not None:
        _server_instance.shutdown()
        print("[OK] 服务已停止")


def main():
    global _server_thread

    print("[..] 启动前端服务...")
    _server_thread = threading.Thread(target=_start_server, daemon=True)
    _server_thread.start()
    time.sleep(0.5)

    atexit.register(_stop_server)

    # 处理退出信号
    def _signal_handler(signum, frame):
        print("\n[OK] 收到终止信号，正在关闭...")
        _stop_server()
        sys.exit(0)

    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    # 启动 Qt
    app = QApplication(sys.argv)
    app.setApplicationName("Cortex Agent")

    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setHttpCacheMaximumSize(0)

    window = MainWindow()
    window.show()
    print("[OK] Cortex Agent 已启动")
    print("[..] 如果窗口未自动加载，请手动打开 http://localhost:8765")

    exit_code = app.exec()
    _stop_server()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
