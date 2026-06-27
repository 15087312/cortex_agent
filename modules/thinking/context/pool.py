"""
TurnContext — 单轮上下文池

ContextFragment 来源 → TurnContext 池化 → view(role) 角色过滤输出
替代 ContextController.build_context(**sources) 的松散 dict 传入。
"""
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Tuple


@dataclass
class ContextFragment:
    """上下文片段 — 每个 Source 产出一个"""
    source: str                          # "memory" | "perception" | "conscience" | ...
    content: str                         # 格式化后的纯文本
    target_roles: Tuple[str, ...]        # 哪些角色应看到此片段
    section_title: str                   # prompt 中显示的标题
    priority: int = 0                    # 排序权重，越小越靠前
    ttl_turns: int = 0                   # 0=仅本轮, -1=永久


class TurnContext:
    """单轮上下文池

    add() 收集 Fragment → view(role) 产出角色定制文本
    内置去重（MD5 哈希）、优先级排序、token 预算压缩。
    """

    def __init__(self):
        self.fragments: Dict[str, ContextFragment] = {}
        self._hashes: set = set()

    def add(self, fragment: ContextFragment) -> None:
        if not fragment.content:
            return
        h = hashlib.md5(fragment.content.encode()).hexdigest()[:16]
        if h in self._hashes:
            return
        self._hashes.add(h)
        self.fragments[fragment.source] = fragment

    def view(self, role: str, max_tokens: int = 8000) -> str:
        parts = []
        sorted_frags = sorted(self.fragments.values(), key=lambda f: f.priority)

        for frag in sorted_frags:
            if role not in frag.target_roles:
                continue
            parts.append(f"【{frag.section_title}】\n{frag.content}")

        combined = "\n\n".join(parts)
        return self._compact(combined, max_tokens)

    def _compact(self, text: str, max_tokens: int) -> str:
        if not text:
            return ""
        try:
            from modules.thinking.context.compression import get_compression_engine
            engine = get_compression_engine()
            estimated = engine.estimate_tokens(text)
            if estimated <= max_tokens:
                return text
            return engine.compress(text, max_tokens=max_tokens)
        except Exception:
            return text
