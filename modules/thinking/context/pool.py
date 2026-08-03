"""
TurnContext — 单轮上下文池 + 轮次生命周期

ContextFragment 来源 → TurnContext 池化 → view(role) 角色过滤输出
替代 ContextController.build_context(**sources) 的松散 dict 传入。
同时承载轮次生命周期状态（turn_id, state 转移, 统计）。
"""
import hashlib
import time
import uuid
from enum import Enum
from dataclasses import dataclass
from typing import Dict, Tuple, Optional


class TurnState(str, Enum):
    """轮次状态机"""
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    INTEGRATING = "integrating"
    COMPLETE = "complete"
    ERROR = "error"


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
    """单轮上下文池 + 生命周期

    add() 收集 Fragment → view(role) 产出角色定制文本
    内置去重（MD5 哈希）、优先级排序、token 预算压缩。

    生命周期：
    - turn_id: UUID 唯一标识
    - state: IDLE → PLANNING → EXECUTING → INTEGRATING → COMPLETE/ERROR
    - 统计：elapsed_seconds, round_count
    """

    def __init__(self, session_id: str = "", user_input: str = ""):
        self.fragments: Dict[str, ContextFragment] = {}
        self._hashes: set = set()

        # 生命周期字段
        self.turn_id: str = str(uuid.uuid4())
        self.session_id: str = session_id
        self.user_input: str = user_input
        self.state: TurnState = TurnState.IDLE
        self.start_ts: float = time.time()
        self.end_ts: Optional[float] = None
        self.elapsed_seconds: float = 0.0
        self.round_count: int = 0
        self.last_user_message_time: float = 0.0

    @property
    def is_active(self) -> bool:
        return self.state in (TurnState.PLANNING, TurnState.EXECUTING, TurnState.INTEGRATING)

    @property
    def is_complete(self) -> bool:
        return self.state in (TurnState.COMPLETE, TurnState.ERROR)

    def transition_to(self, new_state: TurnState) -> None:
        self.state = new_state
        if new_state in (TurnState.COMPLETE, TurnState.ERROR):
            self.end_ts = time.time()
            self.elapsed_seconds = self.end_ts - self.start_ts

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "user_input": self.user_input[:200],
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "elapsed_seconds": self.elapsed_seconds,
            "round_count": self.round_count,
        }

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

        # "large" 与 "orchestrator" 是同一角色（总指挥）的两种写法：
        # 若查看角色是二者之一，则能看到 target_roles 含任一别名的片段。
        # 否则（supervisor/expert）按精确匹配。
        aliases = {"large", "orchestrator"}

        for frag in sorted_frags:
            targets = set(frag.target_roles)
            if role not in targets and not (role in aliases and (targets & aliases)):
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
