"""
轮次上下文 — 替代 ContinuousThinker._turn_counter + _turn_context_cache

TurnContext 代表一次对话的完整元数据：
- turn_id：UUID，每轮唯一，不再用 hash(session_id + question + round) 容易碰撞
- 生命周期跟踪：从 IDLE 到 PLANNING 到 EXECUTING 到 INTEGRATING 到 COMPLETE/ERROR
"""

import time
import uuid
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class TurnState(str, Enum):
    """轮次状态机"""

    IDLE = "idle"  # 待命，未开始
    PLANNING = "planning"  # 大模型规划中
    EXECUTING = "executing"  # 专家执行中
    INTEGRATING = "integrating"  # 整合结果中
    COMPLETE = "complete"  # 完成
    ERROR = "error"  # 出错


@dataclass
class TurnContext:
    """
    一次对话轮次的完整上下文

    替代现在分散的：
    - ContinuousThinker._turn_counter（用 turn_id 替代）
    - ContinuousThinker._turn_context_cache 的 key（用 turn_id 替代）
    - SessionManager.Session 的元数据

    特点：
    - turn_id 作为唯一标识符，永不碰撞（UUID）
    - state 显式跟踪生命周期
    - 所有时间戳精确到秒
    """

    # ── 标识 ──
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    user_input: str = ""

    # ── 生命周期 ──
    state: TurnState = TurnState.IDLE
    start_ts: float = field(default_factory=time.time)
    end_ts: Optional[float] = None

    # ── 统计 ──
    elapsed_seconds: float = 0.0  # 总耗时
    round_count: int = 0  # 轮数（若有多轮思考）

    def __post_init__(self):
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if not self.user_input:
            raise ValueError("user_input 不能为空")

    @property
    def is_active(self) -> bool:
        """是否仍在处理中"""
        return self.state in (TurnState.PLANNING, TurnState.EXECUTING, TurnState.INTEGRATING)

    @property
    def is_complete(self) -> bool:
        """是否已完成"""
        return self.state in (TurnState.COMPLETE, TurnState.ERROR)

    def transition_to(self, new_state: TurnState) -> None:
        """转移到新状态"""
        self.state = new_state
        if new_state in (TurnState.COMPLETE, TurnState.ERROR):
            self.end_ts = time.time()
            self.elapsed_seconds = self.end_ts - self.start_ts

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "user_input": self.user_input[:200],  # 截断
            "start_ts": self.start_ts,
            "end_ts": self.end_ts,
            "elapsed_seconds": self.elapsed_seconds,
            "round_count": self.round_count,
        }
