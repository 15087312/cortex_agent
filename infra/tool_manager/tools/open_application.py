"""
跨平台应用生命周期工具 — 统一接口打开/关闭软件

- open_app: 打开应用程序（macOS/Windows/Linux 自动适配）
  * macOS: open -a "应用名"
  * Windows: start 应用名或路径
  * Linux: xdg-open 或直接执行

- close_app: 关闭应用程序（按应用名自动查找进程并关闭）
  * macOS/Linux: pkill -f "应用名"
  * Windows: taskkill /IM 应用名.exe

接口统一，内部自动平台适配，无需关心系统差异
"""
import subprocess
import platform
import os
import time
from pathlib import Path
from typing import Optional, Dict
from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("open_app")

# 常见应用映射表（跨平台应用名称标准化）
# 用于处理不同平台上应用的不同名称
APP_NAME_MAP = {
    "chrome": {
        "darwin": "Google Chrome",
        "windows": "chrome.exe",
        "linux": "google-chrome",
    },
    "firefox": {
        "darwin": "Firefox",
        "windows": "firefox.exe",
        "linux": "firefox",
    },
    "vscode": {
        "darwin": "Visual Studio Code",
        "windows": "code.exe",
        "linux": "code",
    },
    "sublime": {
        "darwin": "Sublime Text",
        "windows": "sublime_text.exe",
        "linux": "subl",
    },
    "finder": {
        "darwin": "/System/Library/CoreServices/Finder.app",
        "windows": "explorer.exe",
        "linux": "nautilus",
    },
    "terminal": {
        "darwin": "Terminal",
        "windows": "cmd.exe",
        "linux": "gnome-terminal",
    },
}


def _normalize_app_name(app_identifier: str) -> str:
    """
    规范化应用名称，支持别名映射

    Args:
        app_identifier: 应用名称或别名

    Returns:
        标准化后的应用名称（根据当前平台）
    """
    system = platform.system().lower()
    if system == "darwin":
        system = "darwin"
    elif system == "windows":
        system = "windows"
    else:
        system = "linux"

    # 如果是别名，返回对应平台的应用名
    if app_identifier.lower() in APP_NAME_MAP:
        return APP_NAME_MAP[app_identifier.lower()].get(system, app_identifier)

    # 否则原样返回
    return app_identifier


def _open_macos(app_identifier: str) -> Dict[str, str]:
    """macOS 打开应用"""
    try:
        # 支持应用名、应用路径、URL
        if app_identifier.startswith("http://") or app_identifier.startswith("https://"):
            # URL
            subprocess.run(["open", app_identifier], check=True, timeout=5)
            return {"status": "success", "message": f"已在浏览器打开: {app_identifier}"}

        # 先尝试作为应用名（open -a "xxx"）
        try:
            subprocess.run(["open", "-a", app_identifier], check=True, timeout=5)
            return {"status": "success", "message": f"已打开应用: {app_identifier}"}
        except subprocess.CalledProcessError:
            # 如果失败，尝试作为路径或命令
            if os.path.exists(app_identifier):
                subprocess.run(["open", app_identifier], check=True, timeout=5)
                return {"status": "success", "message": f"已打开: {app_identifier}"}
            else:
                # 尝试直接执行
                subprocess.Popen([app_identifier])
                return {"status": "success", "message": f"已启动: {app_identifier}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "应用启动超时（>5秒）"}
    except FileNotFoundError:
        return {"status": "error", "message": f"应用不存在: {app_identifier}"}
    except Exception as e:
        return {"status": "error", "message": f"打开应用失败: {str(e)}"}


def _open_windows(app_identifier: str) -> Dict[str, str]:
    """Windows 打开应用"""
    try:
        # 处理URL
        if app_identifier.startswith("http://") or app_identifier.startswith("https://"):
            os.startfile(app_identifier)
            return {"status": "success", "message": f"已在浏览器打开: {app_identifier}"}

        # 处理可执行文件或应用名
        if app_identifier.endswith(".exe") or app_identifier.endswith(".lnk"):
            # 完整路径或可执行文件名
            if os.path.exists(app_identifier):
                os.startfile(app_identifier)
                return {"status": "success", "message": f"已启动: {app_identifier}"}
            else:
                # 尝试在 PATH 中查找
                try:
                    subprocess.Popen(app_identifier)
                    return {"status": "success", "message": f"已启动: {app_identifier}"}
                except FileNotFoundError:
                    return {"status": "error", "message": f"应用不存在: {app_identifier}"}
        else:
            # 应用名（不含扩展名）— 尝试用 start 命令
            try:
                subprocess.Popen(f"start {app_identifier}", shell=True)
                return {"status": "success", "message": f"已启动: {app_identifier}"}
            except Exception as e:
                return {"status": "error", "message": f"启动应用失败: {str(e)}"}
    except Exception as e:
        return {"status": "error", "message": f"打开应用失败: {str(e)}"}


def _open_linux(app_identifier: str) -> Dict[str, str]:
    """Linux 打开应用"""
    try:
        # 处理URL
        if app_identifier.startswith("http://") or app_identifier.startswith("https://"):
            # 使用 xdg-open 打开URL（会用默认浏览器）
            subprocess.Popen(["xdg-open", app_identifier])
            return {"status": "success", "message": f"已用默认应用打开: {app_identifier}"}

        # 处理文件或文件夹
        if os.path.exists(app_identifier):
            subprocess.Popen(["xdg-open", app_identifier])
            return {"status": "success", "message": f"已用默认应用打开: {app_identifier}"}

        # 尝试作为应用名直接执行
        try:
            subprocess.Popen([app_identifier])
            return {"status": "success", "message": f"已启动: {app_identifier}"}
        except FileNotFoundError:
            return {"status": "error", "message": f"应用不存在: {app_identifier}（检查是否已安装）"}
    except Exception as e:
        return {"status": "error", "message": f"打开应用失败: {str(e)}"}


@ToolRegistry.register(
    name="open_app",
    description="打开应用程序或文件（跨平台自动适配，macOS/Windows/Linux 接口统一）",
    params={
        "app_identifier": "应用名称、可执行文件路径、或 URL（如 'Chrome'、'/path/to/app.exe'、'https://example.com'）",
        "wait": "是否等待应用关闭（仅在指定时有效，默认后台运行）",
    },
    source="builtin",
    risk_level="MEDIUM",  # 打开应用有一定风险，但比执行任意命令低
    category="mutation",  # 修改系统状态
    tags=["system", "app_launch"],
    priority=1,  # 常用工具
    core=True,
)
def open_app(app_identifier: str, wait: bool = False) -> Dict[str, str]:
    """
    打开应用程序或文件（跨平台）

    参数：
        app_identifier: 应用名、路径或 URL
            - 应用名: "Chrome", "Firefox", "VS Code" 等
            - 路径: "/path/to/app.exe", "C:\\Program Files\\App\\app.exe"
            - URL: "https://example.com"
            - 文件路径: "/path/to/document.pdf" （用默认应用打开）

        wait: 是否阻塞等待应用关闭（默认后台运行）

    返回：
        {"status": "success"/"error", "message": "详细信息"}

    示例：
        >>> open_app("Chrome")  # 打开 Chrome 浏览器
        >>> open_app("https://example.com")  # 用默认浏览器打开网址
        >>> open_app("/path/to/document.pdf")  # 用默认应用打开文件
        >>> open_app("C:\\\\Program Files\\\\MyApp\\\\app.exe")  # Windows 路径
    """
    if not app_identifier or not isinstance(app_identifier, str):
        return {"status": "error", "message": "app_identifier 必须是非空字符串"}

    # 规范化应用名称（处理别名和平台差异）
    normalized_identifier = _normalize_app_name(app_identifier.strip())

    logger.info(f"打开应用: {app_identifier} (规范化为: {normalized_identifier})")

    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            result = _open_macos(normalized_identifier)
        elif system == "Windows":
            result = _open_windows(normalized_identifier)
        else:  # Linux 及其他
            result = _open_linux(normalized_identifier)

        if result["status"] == "success":
            logger.info(result["message"])
            # 尝试加载该 app 的已学工具 Skill
            try:
                from modules.toolbuilder.skill_generator import SkillGenerator
                SkillGenerator.load_for_app(app_identifier.strip())
            except Exception:
                pass  # 非致命，skill 不存在时跳过
        else:
            logger.warning(result["message"])

        return result
    except Exception as e:
        error_msg = f"未预期的错误: {str(e)}"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}


def _close_macos(app_identifier: str) -> Dict[str, str]:
    """macOS 关闭应用"""
    try:
        # 规范化应用名称
        app_name = _normalize_app_name(app_identifier).split("/")[-1].replace(".app", "")

        # 尝试用 pkill 查找并关闭进程
        # pkill -f 可以匹配进程完整命令行
        result = subprocess.run(
            ["pkill", "-f", app_name],
            check=False,
            timeout=5,
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            return {"status": "success", "message": f"已关闭应用: {app_identifier}"}
        else:
            # pkill 返回码 1 表示没有匹配的进程
            return {"status": "error", "message": f"应用 {app_identifier} 未运行"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "关闭超时（>5秒）"}
    except Exception as e:
        return {"status": "error", "message": f"关闭应用失败: {str(e)}"}


def _close_windows(app_identifier: str) -> Dict[str, str]:
    """Windows 关闭应用"""
    try:
        # 规范化应用名称
        app_name = _normalize_app_name(app_identifier)

        # 如果不以 .exe 结尾，添加后缀
        if not app_name.lower().endswith(".exe"):
            app_name = app_name + ".exe"

        # 提取应用名（去掉路径）
        app_exe = os.path.basename(app_name)

        # 使用 taskkill 关闭应用
        result = subprocess.run(
            ["taskkill", "/IM", app_exe, "/F"],
            check=False,
            timeout=5,
            capture_output=True,
            text=True
        )

        # taskkill 返回码: 0=成功, 128=进程不存在, 其他=错误
        if result.returncode == 0:
            return {"status": "success", "message": f"已关闭应用: {app_identifier}"}
        elif result.returncode == 128 or "not found" in result.stderr.lower():
            return {"status": "error", "message": f"应用 {app_identifier} 未运行"}
        else:
            return {"status": "error", "message": f"关闭应用失败: {result.stderr or result.stdout}"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "关闭超时（>5秒）"}
    except Exception as e:
        return {"status": "error", "message": f"关闭应用失败: {str(e)}"}


def _close_linux(app_identifier: str) -> Dict[str, str]:
    """Linux 关闭应用"""
    try:
        # 规范化应用名称
        app_name = _normalize_app_name(app_identifier).split("/")[-1]

        # 使用 pkill 查找并关闭进程
        result = subprocess.run(
            ["pkill", "-f", app_name],
            check=False,
            timeout=5,
            capture_output=True,
            text=True
        )

        if result.returncode == 0:
            return {"status": "success", "message": f"已关闭应用: {app_identifier}"}
        else:
            # pkill 返回码 1 表示没有匹配的进程
            return {"status": "error", "message": f"应用 {app_identifier} 未运行"}
    except subprocess.TimeoutExpired:
        return {"status": "error", "message": "关闭超时（>5秒）"}
    except Exception as e:
        return {"status": "error", "message": f"关闭应用失败: {str(e)}"}


@ToolRegistry.register(
    name="close_app",
    description="关闭应用程序（跨平台自动适配，按应用名自动查找进程并关闭）",
    params={
        "app_identifier": "应用名称或进程名（如 'Chrome', 'Firefox', 'code'）",
        "force": "是否强制关闭（默认 True，使用 SIGKILL）",
    },
    source="builtin",
    risk_level="MEDIUM",  # 关闭应用修改系统状态，但不如 kill_process 危险
    category="mutation",  # 修改系统状态
    tags=["system", "app_lifecycle"],
    priority=1,  # 常用工具
    core=True,
)
def close_app(app_identifier: str, force: bool = True) -> Dict[str, str]:
    """
    关闭应用程序（跨平台）

    参数：
        app_identifier: 应用名或进程名
            - 应用名: "Chrome", "Firefox", "VS Code" 等
            - 进程名: "python", "node", "chrome" 等
            - 支持应用别名自动转换

        force: 是否强制关闭（默认 True）
            - True: 使用 SIGKILL (macOS/Linux) 或 /F (Windows)
            - False: 使用 SIGTERM (macOS/Linux) 或正常终止 (Windows)

    返回：
        {"status": "success"/"error", "message": "详细信息"}

    示例：
        >>> close_app("Chrome")  # 关闭 Chrome 浏览器
        >>> close_app("python")  # 关闭 Python 进程
        >>> close_app("code", force=False)  # 温和关闭（允许应用保存）
    """
    if not app_identifier or not isinstance(app_identifier, str):
        return {"status": "error", "message": "app_identifier 必须是非空字符串"}

    app_identifier = app_identifier.strip()
    logger.info(f"关闭应用: {app_identifier} (force={force})")

    system = platform.system()

    try:
        if system == "Darwin":  # macOS
            result = _close_macos(app_identifier)
        elif system == "Windows":
            result = _close_windows(app_identifier)
        else:  # Linux 及其他
            result = _close_linux(app_identifier)

        if result["status"] == "success":
            logger.info(result["message"])
        else:
            logger.warning(result["message"])

        return result
    except Exception as e:
        error_msg = f"未预期的错误: {str(e)}"
        logger.error(error_msg)
        return {"status": "error", "message": error_msg}
