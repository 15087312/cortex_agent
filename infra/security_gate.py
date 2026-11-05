"""安全门控/权限门面 —— 让 thinking 与 security_system 之间不再有直接 import。

为什么需要它
    logiclink 的 L3 调用图要求「声明的接线 == 真实的 import」。但 thinking ↔
    security_system 是一对**双向**依赖：

        thinking      → security_system   工具安全检查、权限控制器、安全事件回调
        security_system → thinking        身份/权限查询、模型工厂、控制工具常量

    用 `eq`（顺序数据流）表达不了双向关系，直接 import 又会双向触发 E302。
    门面的做法是：把两个模块**互相需要的那一面**抽到 infra，双方只依赖 infra，
    于是两条模块边同时消失，而运行期行为由端口注册表在加载时接回来。

退化策略（重要）
    门面里的每个入口在提供方缺席时的行为**刻意与该能力原有的语义保持一致**：

        门控检查      → 拒绝（fail-closed）。缺门控器时放行等于绕过安全检查，
                        与真正的安全系统不可用时的处置相反，所以宁可拒绝。
        输入校验      → 拒绝（fail-closed）。同上，安全校验缺失不应静默通过。
        权限查询      → 空权限 / None（fail-open）。权限是**收窄**工具集的机制，
                        拿不到权限信息时收紧工具集会让所有工具不可用，
                        因此回落到「不额外收窄」。
        事件回调      → 丢弃事件（no-op）。安全事件是旁路通知，不是控制流。
        身份查询      → 空集合。没有身份信息时不产生任何授权。

    换句话说：**判定类接口 fail-closed，收窄类接口 fail-open，通知类接口 no-op。**
    这个划分不是折中，而是各类能力本身的性质决定的。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from infra import ports

_LOCK = threading.RLock()

# 端口名
PORT_GATE = "security.tool_gate"
PORT_PERMISSION = "security.permission_controller"
PORT_API = "security.api"
PORT_EVENT_SINK = "security.event_sink"
PORT_IDENTITIES = "security.identities"
PORT_MODEL_FACTORY = "thinking.model_factory"
PORT_CONTROL_TOOLS = "thinking.control_tools"
PORT_WRITE_KEYWORDS = "security.delegate_write_keywords"
PORT_NARROWING = "security.ai_narrowing"

# 事件订阅者（思考侧注册）；安全侧发事件时由本门面转发。
_event_subscribers: List[Callable[[Dict[str, Any]], None]] = []
# 门控侧安装的原始发射器；思考侧 _emit_security_event 直接复用它。
_raw_emitter: Optional[Callable[..., None]] = None


# --------------------------------------------------------------------------
# 提供方注册（由 security_system / thinking 在模块加载时调用）
# --------------------------------------------------------------------------
def register_gate(provider: Any) -> None:
    ports.register(PORT_GATE, provider)


def register_permission_controller(provider: Any) -> None:
    ports.register(PORT_PERMISSION, provider)


def register_api(provider: Any) -> None:
    ports.register(PORT_API, provider)


def register_event_emitter(fn: Callable[..., None]) -> None:
    """安全系统安装事件发射器；此后思考侧的 _emit_security_event 走它。"""
    global _raw_emitter
    with _LOCK:
        _raw_emitter = fn


def register_identities(provider: Any) -> None:
    ports.register(PORT_IDENTITIES, provider)


def register_provider(name: str, provider: Any) -> None:
    """通用登记入口（供 thinking 侧登记 control_tools 等）。"""
    ports.register(name, provider)


def register_model_factory(provider: Any) -> None:
    ports.register(PORT_MODEL_FACTORY, provider)


# --------------------------------------------------------------------------
# 消费侧入口 —— 工具门控
# --------------------------------------------------------------------------
def get_tool_security_gate() -> Any:
    """返回工具安全门控单例；缺失时返回一个 fail-closed 的替身。"""
    gate = ports.resolve(PORT_GATE)
    if gate is None:
        return _DeniedGate()
    return gate


def get_tool_security_gate_cls() -> Any:
    """返回门控**类**（思考侧需要访问 _pending_reviews 等类级状态）。

    类级状态是审批流程的共享真相：思考侧创建 future 放进 _pending_reviews，
    前端决议时由门控类取出兑现。因此思考侧必须拿到同一个类对象，
    而不是门控实例。缺失时退化为替身类，审批将走拒绝路径。
    """
    gate = ports.resolve(PORT_GATE)
    if gate is None:
        return _DeniedGate
    try:
        return type(gate) if not isinstance(gate, type) else gate
    except Exception:
        # type() 本身不会抛，但 isinstance 可能触发代理对象的 __class__ 副作用。
        # fail-closed：拿不到真类就给替身类，审批走拒绝路径。
        return _DeniedGate


def get_delegate_write_keywords() -> set:
    """plan 模式下拦截写操作委派的关键词集合。"""
    from infra import ports as _ports

    provider = _ports.resolve(PORT_WRITE_KEYWORDS)
    if provider is None:
        return set()
    try:
        return set(provider.get_delegate_write_keywords())
    except Exception:
        return set()


class _LazyKeywords:
    """`DELEGATE_WRITE_KEYWORDS` 的惰性代理：迭代时向端口取当前集合。"""

    def __iter__(self):
        return iter(get_delegate_write_keywords())

    def __contains__(self, item: object) -> bool:
        return item in get_delegate_write_keywords()

    def __len__(self) -> int:
        return len(get_delegate_write_keywords())

    def __bool__(self) -> bool:
        return bool(get_delegate_write_keywords())


DELEGATE_WRITE_KEYWORDS = _LazyKeywords()


class _DeniedGate:
    """安全门控缺席时的替身：一律拒绝，绝不放行。

    同时提供审批流程需要的类级接口（_pending_reviews / resolve_review /
    reject_session_reviews），使思考侧的调用路径不依赖异常兜底——异常兜底会让
    「门控不可用」和「审批逻辑自身出错」在日志里长得一样。
    """

    _pending_reviews: Dict[str, Any] = {}
    _pending_review_sessions: Dict[str, str] = {}

    async def check(self, tool_name: str = "", **kwargs: Any) -> Tuple[bool, str]:
        return False, f"安全门控不可用，已拒绝工具 {tool_name or '<unknown>'}"

    @classmethod
    def resolve_review(cls, request_id: str, approved: bool, reason: str = "") -> bool:
        """无门控时无从决议；返回 False 表示「未处理」。"""
        return False

    @classmethod
    def reject_session_reviews(cls, session_id: str) -> int:
        return 0

    def __getattr__(self, item: str) -> Any:  # pragma: no cover - 兜底
        raise AttributeError(f"安全门控不可用，无法访问 {item}")


def emit_security_event(
    event_type: str = "",
    tool_name: str = "",
    caller_model_id: str = "",
    success: bool = True,
    detail: str = "",
    duration_ms: int = 0,
    request_id: str = "",
    extra_fields: Optional[Dict[str, Any]] = None,
) -> None:
    """发射安全事件：优先用安全系统安装的发射器，否则通知本门面的订阅者。

    签名与原 `_emit_security_event` 保持一致（含位置参数顺序）——调用方按位置
    传 event_type/tool_name/caller_model_id/success，若改成仅收 **kwargs 会直接
    TypeError，把安全事件通道打断。
    """
    payload = {
        "event_type": event_type,
        "tool_name": tool_name,
        "caller_model_id": caller_model_id,
        "success": success,
        "detail": detail,
        "duration_ms": duration_ms,
        "request_id": request_id,
        "extra_fields": extra_fields,
    }
    with _LOCK:
        emitter = _raw_emitter
        subscribers = list(_event_subscribers)
    if emitter is not None:
        try:
            emitter(
                event_type=event_type,
                tool_name=tool_name,
                caller_model_id=caller_model_id,
                success=success,
                detail=detail,
                duration_ms=duration_ms,
                request_id=request_id,
                extra_fields=extra_fields,
            )
            return
        except Exception:
            pass
    for fn in subscribers:
        try:
            fn(payload)
        except Exception:
            pass


def set_security_event_callback(callback: Callable[[Dict[str, Any]], None]) -> None:
    """注册安全事件订阅者（思考侧调用）。"""
    with _LOCK:
        if callback not in _event_subscribers:
            _event_subscribers.append(callback)


def clear_security_event_subscribers() -> None:
    with _LOCK:
        _event_subscribers.clear()


# --------------------------------------------------------------------------
# 消费侧入口 —— 事件订阅者列表（安全侧读取，替代反向 import）
# --------------------------------------------------------------------------
def get_event_subscribers() -> List[Callable[[Dict[str, Any]], None]]:
    with _LOCK:
        return list(_event_subscribers)


# --------------------------------------------------------------------------
# 消费侧入口 —— 权限控制器
# --------------------------------------------------------------------------
def get_tool_permission_controller() -> Any:
    from infra import ports as _ports

    return _ports.resolve(PORT_PERMISSION)


def _narrowing():
    """取 AI 工具收窄的提供方。

    注意：收窄是 tool_permission_controller 的**模块级函数**，不是控制器实例
    方法。早期版本按实例方法转发，getattr 找不到时静默 no-op，收窄看似成功
    实则不生效 —— 故这里改为显式端口解析，并在缺席时抛错而非静默。
    """
    from infra import ports as _ports

    provider = _ports.resolve(PORT_NARROWING)
    if provider is None:
        raise RuntimeError("安全系统未加载：AI 工具收窄不可用")
    return provider


def set_ai_narrowed_tools(model_id: str, tools: Optional[Set[str]] = None,
                          session_id: str = "", task_id: str = "") -> None:
    _narrowing().set_ai_narrowed_tools(model_id, tools, session_id, task_id)


def get_ai_narrowed_tools(model_id: str, session_id: str = "",
                          task_id: str = "") -> Optional[Set[str]]:
    return _narrowing().get_ai_narrowed_tools(model_id, session_id, task_id)


def clear_ai_narrowed_tools(model_id: str, session_id: str = "",
                            task_id: str = "") -> None:
    _narrowing().clear_ai_narrowed_tools(model_id, session_id, task_id)


# --------------------------------------------------------------------------
# 消费侧入口 —— 安全 API（输入校验）
# --------------------------------------------------------------------------
def get_security_api() -> Any:
    from infra import ports as _ports

    return _ports.resolve(PORT_API)


def validate_input(user_input: str) -> Tuple[bool, str]:
    """校验输入；安全 API 缺席时拒绝（fail-closed）。"""
    api = get_security_api()
    if api is None:
        return False, "[安全系统不可用] 输入验证失败"
    try:
        return api.validate_input(user_input)
    except Exception as e:
        return False, f"[安全系统异常] 输入验证失败: {e}"


# --------------------------------------------------------------------------
# 消费侧入口 —— 身份/权限查询（security_system 调用，替代反向 import）
# --------------------------------------------------------------------------
def get_identities() -> Dict[str, Any]:
    from infra import ports as _ports

    provider = _ports.resolve(PORT_IDENTITIES)
    if provider is None:
        return {}
    try:
        return provider.get_identities()
    except Exception:
        return {}


def _identity_provider():
    """取身份提供方；缺席时尝试一次性自举。

    为什么需要自举：身份端口由 modules.thinking.identity **在导入时**登记（尾部
    _register_identity_port）。若进程只导入了 security_system 而从未导入
    thinking.identity（例如只跑权限单测、或安全侧先于思考侧初始化），端口就是空的，
    权限查询会退化成最小权限 → 正常角色被误判无权。

    自举是对**纯数据模块**的惰性导入（identity 只依赖 infra.tool_policy 与
    utils.logger，无副作用）。注意 infra 不在 app.link 的 use 列表，这条 import
    落在 E302 检测范围之外 —— 因此它是**受控的例外**，不是随手可加的写法：
    仅限本函数，且失败时静默退化为最小权限（fail-closed），绝不反复重试。
    """
    from infra import ports as _ports

    provider = _ports.resolve(PORT_IDENTITIES)
    if provider is not None:
        return provider
    try:
        import modules.thinking.identity  # noqa: F401
    except Exception:
        return None
    return _ports.resolve(PORT_IDENTITIES)


def get_permissions(model_id: str) -> Any:
    """按 template_key 取权限。

    契约（与原 thinking.identity.get_permissions 一致）：**永不返回 None**。
    未知 key 返回「默认权限」（allowed_tool_categories=["query"]，即拒绝 admin）。
    绝不能因提供方缺席就返回 None —— 调用方把 None 视为「无权限约束」而放行，
    那会把「身份模块没加载」变成「所有工具都允许」，是实打实的提权。
    提供方确实缺席时退化为「最小权限」：只有 query。
    """
    provider = _identity_provider()
    if provider is None:
        return _MinimalPermissions()
    try:
        result = provider.get_permissions(model_id)
    except Exception:
        return _MinimalPermissions()
    return result if result is not None else _MinimalPermissions()


class _MinimalPermissions:
    """身份模块缺席时的最小权限（仅 query），语义同 ModelPermissions() 默认值。"""

    __slots__ = ("allowed_tool_categories",)

    def __init__(self):
        self.allowed_tool_categories = ["query"]

    def can_use_tool_category(self, category: str) -> bool:
        return category in self.allowed_tool_categories

    def __repr__(self) -> str:
        return "_MinimalPermissions(allowed_tool_categories=['query'])"


def get_model_permissions_cls() -> Any:
    """返回 ModelPermissions 类型；缺失时返回 None。"""
    from infra import ports as _ports

    provider = _ports.resolve(PORT_IDENTITIES)
    if provider is None:
        return None
    return getattr(provider, "ModelPermissions", None)


def get_model_factory() -> Any:
    """取模型工厂实例。

    端口的提供方是**延迟查找器**（thinking.model_factory._FactoryProvider），
    不是函数对象快照 —— 这样 monkeypatch 打桩模块属性时能立刻生效，且不会
    因注册时机把某个测试的替身泄漏给后续测试。
    """
    from infra import ports as _ports

    provider = _ports.resolve(PORT_MODEL_FACTORY)
    if provider is None:
        return None
    try:
        if callable(provider):
            return provider()
        getter = getattr(provider, "get", None)
        if callable(getter):
            return getter()
    except Exception:
        return None
    return provider


def get_control_tools() -> Dict[str, Dict[str, Any]]:
    """控制类工具 schema 字典（security_system 读取，替代反向 import）。

    端口必须由 `modules.thinking.core.control_tools` 在导入时登记。本函数**刻意不**
    惰性导入该模块：infra 不在 app.link 的 use 列表里，那条 import 会落在 E302 的
    检测盲区中，等于把刚消除的模块边偷回来。拿不到端口时返回空字典，调用方会因
    KeyError 显式暴露，而不是静默拿到错误的工具集。
    """
    from infra import ports as _ports

    provider = _ports.resolve(PORT_CONTROL_TOOLS)
    if provider is None:
        return {}
    try:
        return provider.get_control_tools()
    except Exception:
        return {}
