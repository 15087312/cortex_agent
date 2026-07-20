"""
思维核心业务逻辑

提供思考相关的核心功能：
- ContinuousThinker: 连续思考器，支持多轮短思考循环
- ThinkingProcessCollector: 思考过程收集抽象接口
- DelegationPort: 委托端口抽象

模型生命周期由 modules.thinking.model_factory.ModelInstanceFactory 统一管理。
"""
from .continuous_thinker import ContinuousThinker
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
