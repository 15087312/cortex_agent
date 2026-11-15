"""安全门面 — 提供方缺席时的降级语义（fail-closed / fail-open / no-op）

这是 thinking 与 security_system 之间唯一的通道，降级方向写反就是提权：
判定类接口必须拒绝，收窄类接口必须放行（否则所有工具都不可用），
通知类接口静默丢弃。每条都单独钉住。
"""
import pytest

from infra import ports
from infra import security_gate as gate


@pytest.fixture(autouse=True)
def _isolated_ports(monkeypatch):
    monkeypatch.setattr(ports, "_PROVIDERS", {})
    monkeypatch.setattr(gate, "_raw_emitter", None)
    monkeypatch.setattr(gate, "_event_subscribers", [])
    yield
    ports._PROVIDERS.clear()


# ── 提供方登记 ──

def test_register_helpers_wire_ports():
    gate.register_gate("GATE")
    gate.register_permission_controller("PC")
    gate.register_api("API")
    gate.register_identities("IDS")
    gate.register_model_factory("FACTORY")
    gate.register_provider(gate.PORT_CONTROL_TOOLS, "CT")

    assert ports.resolve(gate.PORT_GATE) == "GATE"
    assert ports.resolve(gate.PORT_PERMISSION) == "PC"
    assert ports.resolve(gate.PORT_API) == "API"
    assert ports.resolve(gate.PORT_IDENTITIES) == "IDS"
    assert ports.resolve(gate.PORT_MODEL_FACTORY) == "FACTORY"
    assert ports.resolve(gate.PORT_CONTROL_TOOLS) == "CT"


# ── 工具门控（缺省 fail-closed）──

async def test_missing_gate_denies_everything():
    g = gate.get_tool_security_gate()
    ok, reason = await g.check("file_write")
    assert ok is False
    assert "已拒绝" in reason and "file_write" in reason


async def test_missing_gate_reports_unknown_tool_name():
    ok, reason = await gate.get_tool_security_gate().check()
    assert ok is False and "<unknown>" in reason


def test_missing_gate_class_supports_review_flow():
    cls = gate.get_tool_security_gate_cls()
    assert cls is gate._DeniedGate
    assert cls.resolve_review("req", True) is False     # 无门控：未处理
    assert cls.reject_session_reviews("s") == 0
    assert cls._pending_reviews == {}


def test_registered_gate_instance_and_class():
    class RealGate:
        async def check(self, tool_name="", **kw):
            return True, ""

    gate.register_gate(RealGate())
    assert isinstance(gate.get_tool_security_gate(), RealGate)
    assert gate.get_tool_security_gate_cls() is RealGate


def test_registered_gate_class_is_returned_as_is():
    class RealGate:
        pass

    gate.register_gate(RealGate)
    assert gate.get_tool_security_gate_cls() is RealGate


def test_denied_gate_attribute_access_is_explicit():
    with pytest.raises(AttributeError):
        gate._DeniedGate().check_async        # noqa: B018


# ── 写操作关键词（惰性代理）──

def test_write_keywords_empty_without_provider():
    assert gate.get_delegate_write_keywords() == set()
    assert len(gate.DELEGATE_WRITE_KEYWORDS) == 0
    assert bool(gate.DELEGATE_WRITE_KEYWORDS) is False
    assert "rm" not in gate.DELEGATE_WRITE_KEYWORDS


def test_write_keywords_follow_provider_at_access_time():
    class Provider:
        words = {"rm", "mv"}

        def get_delegate_write_keywords(self):
            return self.words

    p = Provider()
    gate.register_provider(gate.PORT_WRITE_KEYWORDS, p)

    assert gate.get_delegate_write_keywords() == {"rm", "mv"}
    assert "rm" in gate.DELEGATE_WRITE_KEYWORDS
    assert len(gate.DELEGATE_WRITE_KEYWORDS) == 2
    assert sorted(gate.DELEGATE_WRITE_KEYWORDS) == ["mv", "rm"]

    p.words = {"chmod"}          # 惰性：不需要重新登记
    assert "rm" not in gate.DELEGATE_WRITE_KEYWORDS


def test_write_keywords_swallow_provider_errors():
    class Broken:
        def get_delegate_write_keywords(self):
            raise RuntimeError("boom")

    gate.register_provider(gate.PORT_WRITE_KEYWORDS, Broken())
    assert gate.get_delegate_write_keywords() == set()


# ── 安全事件 ──

def test_event_goes_to_installed_emitter():
    seen = []
    gate.register_event_emitter(lambda **kw: seen.append(kw))
    gate.emit_security_event("tool_denied", "shell", "model-1", False, "nope", 12, "req-1")
    assert seen[0]["event_type"] == "tool_denied"
    assert seen[0]["tool_name"] == "shell"
    assert seen[0]["success"] is False
    assert seen[0]["duration_ms"] == 12


def test_event_falls_back_to_subscribers_when_emitter_raises():
    def bad(**kw):
        raise RuntimeError("emitter down")

    gate.register_event_emitter(bad)
    seen = []
    gate.set_security_event_callback(seen.append)

    gate.emit_security_event("tool_ok", "read", success=True)
    assert seen and seen[0]["tool_name"] == "read"


def test_subscribers_receive_payload_and_errors_are_swallowed():
    seen = []

    def boom(payload):
        raise RuntimeError("subscriber down")

    gate.set_security_event_callback(boom)
    gate.set_security_event_callback(seen.append)      # 后者仍要收到事件

    gate.emit_security_event("e", extra_fields={"x": 1})
    assert seen[0]["extra_fields"] == {"x": 1}


def test_subscriber_registration_is_deduplicated():
    def cb(payload):
        pass

    gate.set_security_event_callback(cb)
    gate.set_security_event_callback(cb)
    assert gate.get_event_subscribers() == [cb]


def test_clear_subscribers():
    gate.set_security_event_callback(lambda p: None)
    gate.clear_security_event_subscribers()
    assert gate.get_event_subscribers() == []


# ── 权限 / 输入校验 ──

def test_permissions_bootstrap_identity_module_when_port_absent():
    """端口为空但 identity 可导入时自举，未知模型拿到「只有 query」

    这是真实运行路径（security_system 先于 thinking 初始化）。绝不能返回 None，
    否则调用方会把「无权限约束」当放行。
    """
    perms = gate.get_permissions("unknown-model")
    assert perms is not None
    assert "query" in perms.allowed_tool_categories
    assert perms.can_use_tool_category("admin") is False


def test_permissions_fall_back_to_minimal_when_identity_unimportable(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "modules.thinking.identity", None)
    perms = gate.get_permissions("unknown-model")
    assert perms.allowed_tool_categories == ["query"]
    assert perms.can_use_tool_category("query") is True
    assert perms.can_use_tool_category("admin") is False
    assert "_MinimalPermissions" in repr(perms)


def test_permissions_from_provider():
    class Provider:
        def get_permissions(self, model_id):
            assert model_id == "m1"
            return "PERMS"

    gate.register_identities(Provider())
    assert gate.get_permissions("m1") == "PERMS"


def test_permissions_provider_returning_none_is_minimal():
    class Provider:
        def get_permissions(self, model_id):
            return None

    gate.register_identities(Provider())
    assert gate.get_permissions("m1").allowed_tool_categories == ["query"]


def test_permissions_provider_error_is_minimal():
    class Provider:
        def get_permissions(self, model_id):
            raise RuntimeError("identity db locked")

    gate.register_identities(Provider())
    assert gate.get_permissions("m1").can_use_tool_category("admin") is False


def test_permission_controller_resolves_or_none():
    assert gate.get_tool_permission_controller() is None
    gate.register_permission_controller(object())
    assert gate.get_tool_permission_controller() is not None


def test_validate_input_fails_closed_without_api():
    ok, msg = gate.validate_input("hello")
    assert ok is False and "安全系统不可用" in msg


def test_validate_input_delegates_and_wraps_errors():
    class Api:
        def validate_input(self, text):
            if text == "boom":
                raise ValueError("bad")
            return True, ""

    gate.register_api(Api())
    assert gate.validate_input("hi") == (True, "")
    ok, msg = gate.validate_input("boom")
    assert ok is False and "安全系统异常" in msg


# ── 身份 / 模型工厂 / 控制工具 ──

def test_identities_empty_when_missing_or_broken():
    assert gate.get_identities() == {}

    class Broken:
        def get_identities(self):
            raise RuntimeError("x")

    gate.register_identities(Broken())
    assert gate.get_identities() == {}


def test_identities_from_provider():
    class Provider:
        def get_identities(self):
            return {"owner": {"tier": "admin"}}

    gate.register_identities(Provider())
    assert gate.get_identities()["owner"]["tier"] == "admin"


def test_model_permissions_class_from_provider():
    assert gate.get_model_permissions_cls() is None

    class Provider:
        ModelPermissions = "CLS"

    gate.register_identities(Provider())
    assert gate.get_model_permissions_cls() == "CLS"


def test_model_factory_supports_callable_getter_and_plain_value():
    assert gate.get_model_factory() is None

    gate.register_model_factory(lambda: "FACTORY")
    assert gate.get_model_factory() == "FACTORY"

    class WithGet:
        def get(self):
            return "VIA_GET"

    gate.register_model_factory(WithGet())
    assert gate.get_model_factory() == "VIA_GET"

    gate.register_model_factory("PLAIN")
    assert gate.get_model_factory() == "PLAIN"


def test_model_factory_errors_return_none():
    def boom():
        raise RuntimeError("factory down")

    gate.register_model_factory(boom)
    assert gate.get_model_factory() is None


def test_control_tools_empty_or_delegated():
    assert gate.get_control_tools() == {}

    class Provider:
        def get_control_tools(self):
            return {"spawn": {"name": "spawn"}}

    gate.register_provider(gate.PORT_CONTROL_TOOLS, Provider())
    assert gate.get_control_tools()["spawn"]["name"] == "spawn"

    class Broken:
        def get_control_tools(self):
            raise RuntimeError("x")

    gate.register_provider(gate.PORT_CONTROL_TOOLS, Broken())
    assert gate.get_control_tools() == {}


# ── AI 工具收窄 ──

def test_narrowing_without_provider_raises_instead_of_silently_noop():
    with pytest.raises(RuntimeError) as ei:
        gate.set_ai_narrowed_tools("m1", {"read"})
    assert "收窄不可用" in str(ei.value)


def test_narrowing_delegates_to_provider():
    calls = []

    class Provider:
        def set_ai_narrowed_tools(self, model_id, tools, session_id, task_id):
            calls.append(("set", model_id, tools, session_id, task_id))

        def get_ai_narrowed_tools(self, model_id, session_id, task_id):
            calls.append(("get", model_id, session_id, task_id))
            return {"read"}

        def clear_ai_narrowed_tools(self, model_id, session_id, task_id):
            calls.append(("clear", model_id, session_id, task_id))

    gate.register_provider(gate.PORT_NARROWING, Provider())
    gate.set_ai_narrowed_tools("m1", {"read"}, "s1", "t1")
    assert gate.get_ai_narrowed_tools("m1", "s1", "t1") == {"read"}
    gate.clear_ai_narrowed_tools("m1", "s1", "t1")

    assert calls == [
        ("set", "m1", {"read"}, "s1", "t1"),
        ("get", "m1", "s1", "t1"),
        ("clear", "m1", "s1", "t1"),
    ]
