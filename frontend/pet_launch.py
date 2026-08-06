"""独立桌宠进程 — 由 Qt 前端（main.py）启动，随 Qt 退出而关闭

与主窗口解耦：桌宠崩溃不影响 Qt 前端；Qt 先启动，再拉起桌宠。
watchdog：父进程（Qt）退出/被强杀时，桌宠自动退出，避免残留。
"""
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication

from pet_widget import create_pet_widget


def _start_watchdog():
    parent = os.getppid()

    def _loop():
        while True:
            time.sleep(3)
            try:
                if os.getppid() != parent or os.getppid() == 1:
                    os._exit(0)
            except Exception:
                os._exit(0)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Cortex Pet")
    app.setQuitOnLastWindowClosed(False)

    _start_watchdog()
    create_pet_widget()

    signal.signal(signal.SIGTERM, lambda *a: app.quit())
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    app.exec()


if __name__ == "__main__":
    main()

