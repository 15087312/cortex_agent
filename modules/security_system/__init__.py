"""
独立安全系统 - 唯一对外入口

跨模块调用优先依赖 SecurityPort/get_security_port。
"""
from .interface import SecurityPort, get_security_port, set_security_port
from .api import SecurityAPI
from .security_level import SecurityLevel, FORBIDDEN_SYSTEM_COMMANDS, PROTECTED_CORE_MODULES
from .switch_manager import SecuritySwitchManager
from .audit_logger import SecurityAuditLogger
from .tool_security_gate import ToolSecurityGate, get_tool_security_gate

__all__ = [
    "SecurityPort",
    "get_security_port",
    "set_security_port",
    "SecurityAPI",
    "SecurityLevel",
    "SecuritySwitchManager",
    "SecurityAuditLogger",
    "FORBIDDEN_SYSTEM_COMMANDS",
    "PROTECTED_CORE_MODULES",
    "ToolSecurityGate",
    "get_tool_security_gate",
]
