"""
感知模块 - 动态感知外部变化

模块门面导出感知接口/工厂，以及本模块内部常用类型。
跨模块调用优先依赖 PerceptionPort/create_perception_port。
"""
from .interface import PerceptionPort, create_perception_port, get_perception_port

# 旧系统（向后兼容）
try:
    from .manager import (
        PerceptionManager,
        ChangeEvent,
        AttentionItem,
        FilePerception,
        DialogPerception,
        ScreenPerception,
        perception_manager,
    )
except Exception:
    PerceptionManager = None
    ChangeEvent = None
    perception_manager = None

try:
    from .integration import PerceptionIntegrator, perception_integrator
except Exception:
    PerceptionIntegrator = None
    perception_integrator = None

try:
    from .rule_compliance_perception import (
        RuleCompliancePerception,
        ComplianceViolation,
        get_rule_compliance_perception,
    )
except Exception:
    RuleCompliancePerception = None
    ComplianceViolation = None
    get_rule_compliance_perception = None

# 新系统（按需导入）
def get_perception_system():
    from .setup import get_perception_system as _get
    return _get()

__all__ = [
    "PerceptionPort",
    "create_perception_port",
    "get_perception_port",
    "get_perception_system",
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
