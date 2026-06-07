"""
感知模块 - 动态感知外部变化

模块门面导出感知接口/工厂，以及本模块内部常用类型。
跨模块调用优先依赖 PerceptionPort/create_perception_port。
"""
from .interface import PerceptionPort, create_perception_port, get_perception_port
from .manager import (
    PerceptionManager,
    ChangeEvent,
    AttentionItem,
    FilePerception,
    DialogPerception,
    ScreenPerception,
    perception_manager,
)
from .integration import (
    PerceptionIntegrator,
    perception_integrator,
)
from .rule_compliance_perception import (
    RuleCompliancePerception,
    ComplianceViolation,
    get_rule_compliance_perception,
)

__all__ = [
    "PerceptionPort",
    "create_perception_port",
    "get_perception_port",
    "PerceptionManager",
    "ChangeEvent",
    "AttentionItem",
    "FilePerception",
    "DialogPerception",
    "ScreenPerception",
    "PerceptionIntegrator",
    "RuleCompliancePerception",
    "ComplianceViolation",
    "perception_manager",
    "perception_integrator",
    "get_rule_compliance_perception",
]
