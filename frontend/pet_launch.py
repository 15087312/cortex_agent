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

# macOS 透明 WebEngine 窗口 GPU 合成问题（could not create image from display）——
# 改用软件渲染，避免渲染进程崩溃导致模型加载失败
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-software-rasterizer")

from PyQt6.QtWidgets import QApplication

from pet_widget import create_pet_widget


def _start_watchdog():
    if os.environ.get("CORTEX_PET_NO_WATCHDOG", "0") == "1":
        return  # 测试用：禁用 watchdog
    parent = os.getppid()

    def _loop():
        while True:
            time.sleep(3)
            try:
                os.kill(parent, 0)  # 父进程仍存活？
            except OSError:
                os._exit(0)  # 父进程已退出，桌宠随之退出，避免残留

    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def main():
    import faulthandler
    faulthandler.enable()  # 段错误时输出 Python 调用栈（定位崩溃点）

    app = QApplication(sys.argv)
    app.setApplicationName("Cortex Pet")
    app.setQuitOnLastWindowClosed(False)

    _start_watchdog()
    pet = create_pet_widget()

    # 诊断：窗口可见性 / 页面加载 / 渲染进程
    def _log(*args):
        print("[PET]", *args, flush=True)
    try:
        _log("窗口 visible =", pet.isVisible(), "size =", pet.width(), "x", pet.height())
        pet.view.page().javaScriptConsoleMessage = (
            lambda level, msg, line, sid: _log(f"[JS:{level}] {msg}")
        )
        pet.view.page().loadFinished.connect(
            lambda ok: _log("页面加载完成 ok =", ok)
        )
        pet.view.page().renderProcessTerminated.connect(
            lambda status, code, desc: _log(f"渲染进程终止 status={status} code={code} {desc}")
        )
    except Exception as e:
        _log("诊断设置失败:", e)

    signal.signal(signal.SIGTERM, lambda *a: app.quit())
    signal.signal(signal.SIGINT, lambda *a: app.quit())
    app.exec()


if __name__ == "__main__":
    main()

