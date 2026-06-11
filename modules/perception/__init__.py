"""
感知模块 - 动态感知外部变化

模块门面导出感知接口/工厂，以及本模块内部常用类型。
跨模块调用优先依赖 PerceptionPort/create_perception_port。
"""
from .interface import PerceptionPort, create_perception_port, get_perception_port

# 新的独立模块
from .change_event import ChangeEvent
from .file_perception import FilePerception
from .dialog_perception import DialogPerception

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

# 向后兼容：perception_manager 代理指向新系统
def _get_compat_proxy():
    """兼容旧代码：perception_manager.file_perception / .dialog_perception / .start_monitoring()"""
    ps = get_perception_system()

    class _CompatProxy:
        @property
        def _running(self):
            return ps._started

        @property
        def file_perception(self):
            return ps.file_perception

        @property
        def dialog_perception(self):
            return ps.dialog_perception

        @property
        def screen_perception(self):
            return None  # 已由新流水线接管

        def start_monitoring(self):
            if not ps._started:
                ps.setup()
                ps.start()

        def stop_monitoring(self):
            ps.stop()

    return _CompatProxy()

perception_manager = _get_compat_proxy()

__all__ = [
    "PerceptionPort",
    "create_perception_port",
    "get_perception_port",
    "get_perception_system",
    "ChangeEvent",
    "FilePerception",
    "DialogPerception",
    "PerceptionIntegrator",
    "RuleCompliancePerception",
    "ComplianceViolation",
    "perception_manager",
    "perception_integrator",
    "get_rule_compliance_perception",
]
