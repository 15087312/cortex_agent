"""开机启动（macOS LaunchAgent）——让 launch_at_startup 配置真实生效。

启用时写入 ~/Library/LaunchAgents/com.cortex.agent.plist，通过 launchctl 加载，
登录后自动拉起 scripts/autostart_launcher.py（后端 + 前端桌面窗口）。
关闭时卸载并删除 plist。
"""
import os
import plistlib
import subprocess
import sys

from utils.logger import setup_logger

logger = setup_logger("autostart")

LABEL = "com.cortex.agent"


def _launch_agent_dir() -> str:
    return os.path.expanduser("~/Library/LaunchAgents")


def _plist_path() -> str:
    return os.path.join(_launch_agent_dir(), f"{LABEL}.plist")


def _launcher_script() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "scripts", "autostart_launcher.py")


def _build_plist() -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, _launcher_script()],
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": "/tmp/cortex_autostart.log",
        "StandardErrorPath": "/tmp/cortex_autostart.err",
    }


def apply(enabled: bool) -> bool:
    """应用开机启动设置。返回是否成功。"""
    if sys.platform != "darwin":
        logger.debug("[开机启动] 仅 macOS 支持")
        return False
    try:
        os.makedirs(_launch_agent_dir(), exist_ok=True)
        path = _plist_path()
        if enabled:
            with open(path, "wb") as f:
                plistlib.dump(_build_plist(), f)
            subprocess.run(["launchctl", "load", path], capture_output=True, timeout=10)
            logger.info("[开机启动] 已启用 (LaunchAgent)")
        else:
            if os.path.exists(path):
                subprocess.run(["launchctl", "unload", path], capture_output=True, timeout=10)
                try:
                    os.unlink(path)
                except OSError:
                    pass
            logger.info("[开机启动] 已关闭")
        return True
    except Exception as e:
        logger.warning(f"[开机启动] 应用失败: {e}")
        return False


def is_enabled() -> bool:
    return sys.platform == "darwin" and os.path.exists(_plist_path())
