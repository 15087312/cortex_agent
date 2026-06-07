"""
领域事件定义 — 替代松散的 MessageType + action 字符串

事件是状态转移的驱动力：
User Input → EVENT.USER_INPUT → SessionLifecycle.start_turn → CognitiveBlackboard.set_goal
EVENT.PLAN_NEEDED → ModelRunner(large) 激活
EVENT.DELEGATION_CREATED → ModelRunner(supervisor) 激活
...
"""

import time
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class DomainEventType(str, Enum):
    """领域事件类型枚举"""

    # 用户交互
    USER_INPUT = "user_input"  # 用户输入新消息

    # 大模型（总指挥）
    PLAN_NEEDED = "plan_needed"  # 需要大模型制定计划
    PLAN_READY = "plan_ready"  # 大模型计划完成
    LARGE_DONE = "large_done"  # 大模型完成本轮

    # 委托流程
    DELEGATION_CREATED = "delegation_created"  # 大模型创建新委托
    REPLAN_NEEDED = "replan_needed"  # 需要重新规划

    # 主管（Supervisor）
    EXPERT_TASK_READY = "expert_task_ready"  # 主管分解任务，准备分配专家
    SUPERVISOR_DONE = "supervisor_done"  # 主管完成本轮

    # 专家（Expert）
    EXPERT_DONE = "expert_done"  # 专家完成执行

    # 全局状态
    TURN_COMPLETE = "turn_complete"  # 一轮对话完成
    ERROR = "error"  # 错误发生


@dataclass
class DomainEvent:
    """领域事件基类

    所有事件必须包含：
    - event_type：事件类型
    - session_id：会话ID，用于隔离多用户
    - turn_id：轮次ID，唯一标识本轮对话（替代 _turn_counter 哈希）
    - timestamp：事件发生时间戳
    - payload：事件载荷（vary by event_type）
    """

    event_type: DomainEventType
    session_id: str
    turn_id: str
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self):
        """验证必填字段"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if not self.turn_id:
            raise ValueError("turn_id 不能为空")

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "event_type": self.event_type.value,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DomainEvent":
        """从字典反序列化"""
        return cls(
            event_type=DomainEventType(data["event_type"]),
        session_id=data["session_id"],
            turn_id=data["turn_id"],
            payload=data.get("payload", {}),
            timestamp=data.get("timestamp", time.time()),
        )
