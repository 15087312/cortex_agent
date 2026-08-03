"""open_app 工具 — 打开/切换到指定应用"""
import os
import subprocess
import sys

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("open_app_tool")


@ToolRegistry.register(
    "open_app",
    description="打开或切换到指定的桌面应用。如果应用已运行则切换到其窗口，未运行则启动。支持 macOS 上的常见应用名（如 Safari、Chrome、微信、Terminal 等）和应用路径。",
    params={
        "app_name": "应用名或路径，如 Safari、Google Chrome、微信、Terminal、System Settings、/Applications/Sublime Text.app",
        "activate_only": {
            "type": "boolean",
            "description": "可选，仅切换到已运行的应用窗口，不启动新进程（默认 false）",
        },
    },
    risk_level="LOW",
    category="query",
    tags=[],
    core=True,
)
def open_app(app_name: str, activate_only: bool = False) -> dict:
    """打开或切换到应用

    Args:
        app_name: 应用名（如 "Safari"）或完整路径
        activate_only: 仅切换不启动

    Returns:
        执行结果
    """
    if not app_name or not app_name.strip():
        return {"success": False, "error": "app_name 不能为空"}

    app_name = app_name.strip()

    # 1. 优先用 Touchpoint 切换到已运行的应用（更快，无模型加载）
    try:
        import touchpoint as tp
        windows = tp.windows()
        for w in windows:
            w_app = getattr(w, "app", "") or ""
            if app_name.lower() in w_app.lower() or w_app.lower() in app_name.lower():
                tp.activate_window(w)
                logger.info(f"Touchpoint 切换到已有窗口: {w_app}")
                return {
                    "success": True,
                    "action": "activated",
                    "app": w_app,
                    "message": f"已切换到 {w_app}（应用已在运行）",
                }
    except Exception:
        pass

    # 2. 用 TouchpointDetector 查找应用路径（支持中文名→英文路径映射）
    app_path = ""
    try:
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        p = TouchpointDetector._find_app_path(app_name)
        if p:
            app_path = p
    except Exception:
        pass

    if activate_only:
        return {
            "success": False,
            "error": f"应用 {app_name} 未在运行，且 activate_only=True 不允许启动",
        }

    try:
        if sys.platform == "win32":
            if app_path:
                os.startfile(app_path)
            else:
                subprocess.run(["start", app_name], shell=True, capture_output=True, timeout=15)
            logger.info(f"Windows 启动: {app_name}")
            return {"success": True, "action": "launched", "app": app_name, "message": f"已打开 {app_name}"}
        elif sys.platform == "darwin":
            # 优先用完整路径启动（绕过 open -a 的中文名问题）
            if app_path:
                result = subprocess.run(
                    ["open", app_path],
                    capture_output=True, text=True, timeout=15,
                )
                if result.returncode == 0:
                    logger.info(f"open 路径启动成功: {app_path}")
                    return {
                        "success": True,
                        "action": "launched",
                        "app": app_name,
                        "message": f"已打开 {app_name}",
                    }

            # 尝试用 open -a（支持英文应用名）
            result = subprocess.run(
                ["open", "-a", app_name],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                logger.info(f"open -a 启动成功: {app_name}")
                return {
                    "success": True,
                    "action": "launched",
                    "app": app_name,
                    "message": f"已打开 {app_name}",
                }

            stderr = result.stderr or ""
            logger.warning(f"open -a '{app_name}' 失败: {stderr.strip()}")
        else:
            # Linux: 用 xdg-open 或 gtk-launch
            try:
                result = subprocess.run(["xdg-open", app_path or app_name], capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    return {"success": True, "action": "launched", "app": app_name, "message": f"已打开 {app_name}"}
            except FileNotFoundError:
                return {"success": False, "error": "当前系统不支持自动打开应用，请手动启动"}
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"打开 {app_name} 超时"}
    except FileNotFoundError:
        return {"success": False, "error": "系统不支持打开命令"}

    # 3. 尝试使用 Touchpoint 的 configure + CDP 发现（仅 Chrome 系浏览器）
    try:
        if "chrome" in app_name.lower() or "edge" in app_name.lower() or "brave" in app_name.lower():
            import touchpoint as tp
            tp.configure(cdp_discover=True)
            logger.info(f"已为 {app_name} 配置 CDP 发现")
    except Exception:
        pass

    import sys as _sys
    if _sys.platform == "darwin":
        hint = f"open /Applications/{app_name}.app"
    elif _sys.platform == "win32":
        hint = f"start {app_name}"
    else:
        hint = f"xdg-open {app_name}"
    return {
        "success": False,
        "error": f"无法打开 {app_name}，请尝试: {hint}，或确认应用名是否正确",
    }
