"""list_windows 工具 — 列出当前所有应用窗口"""
import subprocess
from typing import Dict, List, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("list_windows_tool")

# 缓存 Electron 检测结果避免重复扫描
_electron_cache: Dict[str, bool] = {}


def _is_electron(app_name: str) -> bool:
    """检测应用是否为 Electron"""
    if app_name in _electron_cache:
        return _electron_cache[app_name]

    # 从 touchpoint window 找 PID，再查进程命令行
    try:
        import touchpoint as tp
        for w in tp.windows():
            if getattr(w, "app", "") == app_name:
                pid = getattr(w, "pid", 0)
                if pid:
                    try:
                        cmdline = subprocess.run(
                            ["ps", "-p", str(pid), "-o", "command="],
                            capture_output=True, text=True, timeout=3,
                        ).stdout.lower()
                        is_elec = "electron" in cmdline or "cef" in cmdline or "helper (renderer)" in cmdline
                        _electron_cache[app_name] = is_elec
                        return is_elec
                    except Exception:
                        pass
                break
    except Exception:
        pass

    # 降级：检查 .app bundle 结构
    try:
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        app_path = TouchpointDetector._find_app_path(app_name)
        if app_path:
            result = TouchpointDetector._is_electron_app(app_path)
            _electron_cache[app_name] = result
            return result
    except Exception:
        pass

    _electron_cache[app_name] = False
    return False


@ToolRegistry.register(
    "list_windows",
    description="列出当前所有运行中的桌面应用窗口，返回每个应用的名称和类型（native/electron）。"
    "可用于先查询当前打开了哪些应用，再对指定应用进行操作。\n\n"
    "【使用流程】\n"
    "1. list_windows() → 查看当前有哪些应用在运行及类型\n"
    "2. open_app(app_name=\"...\") → 打开或切换到目标应用\n"
    "3. detect_ui_elements(app=\"...\") → 扫描目标应用的 UI 元素\n"
    "4. mouse_click/keyboard_type 等 → 操作界面元素\n\n"
    "注意：Electron 应用（如微信、网易云音乐）需要 CDP 端口才能扫描内部元素，"
    "detect_ui_elements 会自动处理无需手动配置。",
    params={
        "only_active": {
            "type": "boolean",
            "description": "可选，仅返回当前活跃（前台）窗口（默认 false 返回全部窗口）",
        },
    },
    risk_level="LOW",
    category="query",
    tags=[],
    core=True,
)
def list_windows(only_active: bool = False) -> Dict[str, Any]:
    """列出当前所有运行中的应用窗口

    Args:
        only_active: 仅返回前台活跃窗口

    Returns:
        包含窗口列表的字典
    """
    try:
        import touchpoint as tp
    except ImportError:
        return {"success": False, "error": "touchpoint 未安装"}

    try:
        windows = tp.windows()
    except Exception as e:
        return {"success": False, "error": f"获取窗口列表失败: {e}"}

    if not windows:
        return {"success": True, "windows": [], "message": "未检测到任何窗口"}

    result: List[Dict[str, Any]] = []
    seen_apps: set = set()

    for w in windows:
        app_name = getattr(w, "app", "") or "未知"
        win_title = getattr(w, "title", "") or ""
        is_active = bool(getattr(w, "is_active", False))

        if only_active and not is_active:
            continue
        if not only_active and app_name in seen_apps:
            continue
        seen_apps.add(app_name)

        is_elec = _is_electron(app_name)

        result.append({
            "app": app_name,
            "title": win_title,
            "active": is_active,
            "type": "electron" if is_elec else "native",
        })

    result.sort(key=lambda x: (not x["active"], x["app"]))

    electron_apps = [w["app"] for w in result if w["type"] == "electron"]
    msg = "使用 detect_ui_elements(app=\"应用名\") 扫描指定窗口的 UI 元素"
    if electron_apps:
        msg += f"。Electron 应用（{'/'.join(electron_apps)}）会自动配置 CDP 扫描"

    return {
        "success": True,
        "windows": result,
        "count": len(result),
        "hint": msg,
    }
