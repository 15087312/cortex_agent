"""Cortex Agent —— Qt 桌面客户端
打开 Qt 窗口加载 Vue 构建产物，或连接 Vite 开发服务器。
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cortex Agent")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)
        self.setStyleSheet("QMainWindow { background-color: #0d1117; }")

        self.browser = QWebEngineView()
        base = os.path.dirname(os.path.abspath(__file__))
        vue_build = os.path.join(base, "frontend", "dist", "index.html")
        if os.path.isfile(vue_build):
            self.browser.setUrl(QUrl.fromLocalFile(os.path.abspath(vue_build)))
        else:
            self.browser.setUrl(QUrl("http://localhost:5173"))
        self.setCentralWidget(self.browser)

    def closeEvent(self, event):
        self.browser.stop()
        self.browser.page().deleteLater()
        event.accept()

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Cortex Agent")
    profile = QWebEngineProfile.defaultProfile()
    profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
    profile.setHttpCacheMaximumSize(0)
    window = MainWindow()
    window.show()
    print("[OK] Cortex Agent 桌面应用已启动")
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
