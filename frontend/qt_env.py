# @link:block
"""Qt 环境修复 — 强制 Qt 使用 PyQt6 自带的插件目录。

anaconda base 环境的 conda qt 包会在 /opt/anaconda3/bin/qt.conf 把 Qt Prefix
指向 /opt/anaconda3，导致 pip 安装的 PyQt6 去找 conda 的 cocoa 平台插件
（版本不匹配），QApplication 直接崩溃：
    qt.qpa.plugin: Could not find the Qt platform plugin "cocoa" in ""

必须在 import PyQt6 / 创建 QApplication 之前调用 fix_qt_plugin_path()。
main.py（主窗口）与 pet_launch.py（桌宠独立进程）共用。
"""
import os


def fix_qt_plugin_path() -> None:
    try:
        import PyQt6 as _pyqt6
        _qt_plugins = os.path.join(os.path.dirname(_pyqt6.__file__), "Qt6", "plugins")
        if os.path.isdir(_qt_plugins):
            os.environ.setdefault("QT_PLUGIN_PATH", _qt_plugins)
    except Exception:
        pass
