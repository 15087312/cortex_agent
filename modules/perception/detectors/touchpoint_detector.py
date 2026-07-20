"""Touchpoint 工具类 — macOS 应用路径查找 + Electron 检测

仅保留 open_app / list_windows 工具调用的静态方法。
detect() / detect_elements() / CDP 管线已删除——UI 事件由 ScreenMonitorSource 产出。
"""
import os
import platform
from typing import Optional

_IS_MAC = platform.system() == "Darwin"


class TouchpointDetector:
    """应用路径查找 + Electron 检测工具"""

    @staticmethod
    def _find_app_path(app_name: str) -> Optional[str]:
        """查找 macOS 应用 .app 路径"""
        if not _IS_MAC:
            return None
        import subprocess
        import plistlib

        try:
            import touchpoint as tp
            for w in tp.windows():
                if getattr(w, "app", "") == app_name:
                    pid = getattr(w, "pid", 0)
                    if pid:
                        result = subprocess.run(
                            ["lsappinfo", "info", "-only", "bundlepath", str(pid)],
                            capture_output=True, text=True, timeout=5,
                        )
                        if result.returncode == 0:
                            for line in result.stdout.splitlines():
                                if "bundle path" in line.lower():
                                    path = line.split("=")[-1].strip().strip('"')
                                    if os.path.exists(path):
                                        return path
        except Exception:
            pass

        # 方法2: mdfind
        try:
            result = subprocess.run(
                ["mdfind", f"kMDItemDisplayName == '{app_name}' && kMDItemKind == 'Application'"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.splitlines():
                path = line.strip()
                if path.endswith(".app") and os.path.isdir(path):
                    try:
                        with open(os.path.join(path, "Contents", "Info.plist"), "rb") as f:
                            plist = plistlib.load(f)
                        name = plist.get("CFBundleDisplayName", plist.get("CFBundleName", ""))
                        if name == app_name or app_name.lower() in path.lower():
                            return path
                    except Exception:
                        pass
        except Exception:
            pass

        # 方法3: /Applications 扫描
        for root in ["/Applications", os.path.expanduser("~/Applications")]:
            if not os.path.isdir(root):
                continue
            try:
                for item in os.listdir(root):
                    full = os.path.join(root, item)
                    if os.path.isdir(full) and item.endswith(".app"):
                        if app_name.lower() in item.lower():
                            return full
            except PermissionError:
                pass

        return None

    @staticmethod
    def _is_electron_app(app_path: str) -> bool:
        """检测 .app 是否为 Electron/CEF 应用"""
        frameworks = os.path.join(app_path, "Contents", "Frameworks")
        if not os.path.isdir(frameworks):
            return False
        try:
            for item in os.listdir(frameworks):
                if "Helper (Renderer).app" in item or " Helper" in item:
                    return True
        except PermissionError:
            pass
        return False
