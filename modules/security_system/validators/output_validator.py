"""
输出格式校验器 - L4级
"""
from typing import Tuple


class OutputValidator:
    def __init__(self):
        self.forbidden_chars = ['\x00', '\x01', '\x02', '\x03']

    def validate(self, content: str) -> Tuple[bool, str]:
        for char in self.forbidden_chars:
            if char in content:
                return False, f"[L4格式拦截] 包含非法控制字符"

        return True, content
