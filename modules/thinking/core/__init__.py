"""
思维核心业务逻辑

提供思考相关的核心功能：
- ContinuousThinker: 连续思考器，支持多轮短思考循环
- ModelManager: 模型调度管理器，负责大中小模型的层级调用
- ThinkingProcessCollector: 思考过程收集抽象接口

所有类均通过外部注入的思考函数调用模型，
不直接依赖模型客户端。
"""
from .continuous_thinker import ContinuousThinker
from .model_manager import model_manager
from .delegation_port import (
    DelegationPort,
    DelegationRequest,
    DelegationResult,
    ProbeDelegationAdapter,
    create_delegation_port,
)
from .process_collector import (
    DefaultThinkingProcessCollectorFactory,
    InMemoryThinkingProcessCollector,
    ThinkingProcessCollector,
    ThinkingProcessCollectorFactory,
    ThinkingProcessSnapshot,
    ThinkingStepRecord,
    create_thinking_process_collector,
    get_thinking_process_collector_factory,
    set_thinking_process_collector_factory,
)

__all__ = [
    "ContinuousThinker",
    "model_manager",
    "ThinkingProcessCollector",
    "ThinkingProcessCollectorFactory",
    "ThinkingProcessSnapshot",
    "ThinkingStepRecord",
    "InMemoryThinkingProcessCollector",
    "DefaultThinkingProcessCollectorFactory",
    "create_thinking_process_collector",
    "get_thinking_process_collector_factory",
    "set_thinking_process_collector_factory",
    "DelegationPort",
    "DelegationRequest",
    "DelegationResult",
    "ProbeDelegationAdapter",
    "create_delegation_port",
]
