"""
内容合规校验器 - L1级
"""
from typing import Tuple, List

# 默认敏感词 — 运行时可通过 add_keyword()/remove_keyword() 动态调整
_DEFAULT_SENSITIVE_KEYWORDS = [
    "system prompt",
    "系统提示词",
    "内部指令",
    "ignore previous instructions",
    "忽略之前的指令",
    "jailbreak",
    "越狱模式",
]


class ContentValidator:
    def __init__(self):
        self.sensitive_keywords: List[str] = list(_DEFAULT_SENSITIVE_KEYWORDS)
        self.min_length = 1
        self.max_length = 50000

    def validate(self, content: str) -> Tuple[bool, str]:
        if not content or len(content.strip()) < self.min_length:
            return False, "[L1内容拦截] 内容为空"
        
        if len(content) > self.max_length:
            return False, f"[L1内容拦截] 内容超长({len(content)}>{self.max_length})"
        
        content_lower = content.lower()
        for keyword in self.sensitive_keywords:
            if keyword.lower() in content_lower:
                return False, f"[L1内容拦截] 检测到敏感词「{keyword}」"
        
        return True, content

    def add_keyword(self, keyword: str) -> None:
        if keyword not in self.sensitive_keywords:
            self.sensitive_keywords.append(keyword)

    def remove_keyword(self, keyword: str) -> None:
        if keyword in self.sensitive_keywords:
            self.sensitive_keywords.remove(keyword)

    def get_keywords(self) -> List[str]:
        return self.sensitive_keywords.copy()
