"""
自进化安全校验器 - L3级
"""
from typing import Tuple, List


class EvolveValidator:
    def __init__(self):
        self.protected_paths = [
            # Unix
            "/etc/", "~/.ssh/", "/System/", "/Library/",
            # Windows
            "C:\\Windows\\", "C:\\Program Files\\", "C:\\Program Files (x86)\\",
            "C:\\ProgramData\\", "C:\\Users\\", "\\System32\\",
            # 项目安全模块
            "modules/security_system",
        ]

    def validate(self, code: str, target_module: str) -> Tuple[bool, str]:
        for path in self.protected_paths:
            if path in code:
                return False, f"[L3自进化拦截] 禁止修改受保护路径「{path}」"
        return True, code
