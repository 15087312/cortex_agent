"""
输出格式校验器 - L4级
"""
from typing import Tuple


class OutputValidator:
    def __init__(self):
        # 拦截所有 C0 控制字符（\x00-\x1f）除了 \n(0x0a) \r(0x0d) \t(0x09)
        # 以及 DEL(\x7f) 和 ANSI 转义序列起始 \x1b
        self._allowed_control = {'\n', '\r', '\t'}

    def validate(self, content: str) -> Tuple[bool, str]:
        for char in content:
            code = ord(char)
            # C0 控制字符 (\x00-\x1f) 或 DEL (\x7f) 或 ESC (\x1b)
            if (code < 0x20 or code == 0x7f) and char not in self._allowed_control:
                return False, f"[L4格式拦截] 包含非法控制字符 (U+{code:04X})"
            # ANSI 转义序列（ESC[...）
            if code == 0x1b:
                return False, "[L4格式拦截] 包含 ANSI 转义序列"

        return True, content
