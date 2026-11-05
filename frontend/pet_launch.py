# @link:block
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
# Qt 平台插件路径修复（与 main.py 共用逻辑，见 frontend/qt_env.py）：
# anaconda 的 conda qt 包经 bin/qt.conf 劫持插件路径会导致 cocoa 加载失败，
# 桌宠独立进程必须在 import PyQt6 之前修复，否则起即崩（exit=-6 SIGABRT）。
try:
    from qt_env import fix_qt_plugin_path as _fix_qt_plugin_path
    _fix_qt_plugin_path()
except Exception:
    pass
# 防残留: 除父进程（Qt）监控外，再绑定后端进程（env > ~/.cortex/backend_pid.json）——
# 后端退出时桌宠随之退出，避免任何启动路径下的残留。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from infra.watchdog import bind_backend as _bind_backend
    _bind_backend(fail_after=120)  # 冷启动时后端就绪需 1~2 分钟，等足再判定
except Exception:
    pass

# macOS 透明 QWebEngineView 需要共享 OpenGL 上下文 + Chromium 透明视觉 flag
# （could not create image from display 的修复）。必须在 QApplication 实例创建前设置。
# 注意：不能禁用 GPU——Live2D 渲染依赖 WebGL。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS", "--enable-transparent-visuals"
)
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

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


# @link:block Pet_Launch = Emit
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

