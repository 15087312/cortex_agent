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

# ── 端口注册（供 output_system / management 取用，避免其 import 本模块）──
from infra.ports import as_factory as _as_factory, register as _register_port
from . import interface as _security_interface
from .validators import (
    ContentValidator as _ContentValidator,
    CoreValidator as _CoreValidator,
    OutputValidator as _OutputValidator,
)

def _register_security_ports() -> None:
    """登记本模块对外端口。import 时执行一次，测试隔离 fixture 亦可重放
    （端口表为进程级全局状态；见 tests/conftest.py）。"""
    _register_port(
        "security.validators",
        _as_factory(lambda: (_CoreValidator(), _ContentValidator(), _OutputValidator())),
    )
    _register_port("security.audit_logger", SecurityAuditLogger)
    _register_port(
        "security.port",
        _as_factory(lambda: _security_interface.get_security_port()),
    )


_register_security_ports()

# ── 门面端口注册（供 thinking 取用，避免其 import 本模块 → 消除 E302 双向边）──
from infra.security_gate import (  # noqa: E402
    register_api as _reg_gate_api,
    register_event_emitter as _reg_event_emitter,
    register_gate as _reg_gate,
    register_permission_controller as _reg_perm,
)


def _gate_provider():
    from .tool_security_gate import get_tool_security_gate as _g
    return _g()


def _perm_provider():
    from .tool_permission_controller import get_tool_permission_controller as _g
    return _g()


def _api_provider():
    from .api import get_security_api as _g
    return _g()


def _emit_provider(**payload):
    from .tool_security_gate import _emit_security_event as _e
    return _e(**payload)


def _register_security_facade_ports() -> None:
    """登记门面端口（供 thinking 取用）。同样可被测试隔离 fixture 重放。"""
    _reg_gate(_as_factory(_gate_provider))
    _reg_perm(_as_factory(_perm_provider))
    _reg_gate_api(_as_factory(_api_provider))
    _reg_event_emitter(_emit_provider)


_register_security_facade_ports()


def _keywords_provider():
    from .tool_security_gate import DELEGATE_WRITE_KEYWORDS as _kw
    return _kw


class _KeywordsProvider:
    @staticmethod
    def get_delegate_write_keywords():
        return _keywords_provider()


from infra.security_gate import register_provider as _reg_provider  # noqa: E402
from infra.security_gate import PORT_WRITE_KEYWORDS as _PORT_KW  # noqa: E402

def _register_keywords_port() -> None:
    _reg_provider(_PORT_KW, _KeywordsProvider())


_register_keywords_port()


class _NarrowingProvider:
    """AI 工具收窄的模块级函数（注意：不是控制器的实例方法）。

    这三个函数操作的是 tool_permission_controller 的模块级收窄表，
    控制器实例上并没有同名方法 —— 门面若按实例方法转发会静默 no-op，
    导致「收窄」看似登记成功、实际不生效。
    """

    @staticmethod
    def set_ai_narrowed_tools(model_id, tools, session_id="", task_id=""):
        from .tool_permission_controller import set_ai_narrowed_tools as _f
        return _f(model_id, tools, session_id, task_id)

    @staticmethod
    def get_ai_narrowed_tools(model_id, session_id="", task_id=""):
        from .tool_permission_controller import get_ai_narrowed_tools as _f
        return _f(model_id, session_id, task_id)

    @staticmethod
    def clear_ai_narrowed_tools(model_id, session_id="", task_id=""):
        from .tool_permission_controller import clear_ai_narrowed_tools as _f
        return _f(model_id, session_id, task_id)


from infra.security_gate import PORT_NARROWING as _PORT_NARROW  # noqa: E402

def _register_narrowing_port() -> None:
    _reg_provider(_PORT_NARROW, _NarrowingProvider())


_register_narrowing_port()
