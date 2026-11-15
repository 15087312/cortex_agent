"""插件契约层（modules.shared.plugin_api）+ 两个可撤销注册面原语。

判据是「卸载之后，进程和挂载之前一模一样」：注册表回到原实现（逐对象身份）、
能力端口回到原提供者、ROOT 的效应栈不因为挂/卸过 N 次而变长。
"""
import asyncio

import pytest

import infra.tool_manager.service_registry as service_registry
from infra import ports
from infra.tool_manager.tool_registry import DuplicateToolError, ToolInfo, ToolRegistry
from modules.shared.plugin_api import (
    ROOT,
    Ctx,
    DuplicateServiceError,
    Plugin,
    UnknownServiceError,
    install,
    mounted,
    unmount,
)


@pytest.fixture(autouse=True)
def contract_snapshot():
    """注册表 / 能力端口 / 横切端口 / ROOT 都是进程级单例：逐用例还原，避免测试污染。"""
    tools_before = dict(ToolRegistry._tools)
    caps_before = dict(service_registry._capabilities)
    ports_before = ports.snapshot()
    effects_before = list(ROOT._effects)
    children_before = dict(ROOT._children)
    yield
    ToolRegistry._tools.clear()
    ToolRegistry._tools.update(tools_before)
    service_registry._capabilities.clear()
    service_registry._capabilities.update(caps_before)
    # 端口表整体换回去而不是逐条 unregister：用例里新登记的端口名字也要消失，
    # 否则「卸载后和挂载前一模一样」这句话在第二套注册表上没人兜底。
    ports.clear()
    for name, provider in ports_before.items():
        ports.register(name, provider)
    ROOT._effects[:] = effects_before
    ROOT._children.clear()
    ROOT._children.update(children_before)


def _func(tag: str):
    def tool(a: str = "") -> str:
        return tag
    tool.__name__ = f"tool_{tag}"
    return tool


# ── Ctx：作用域链与效应栈 ────────────────────────────────────────────────


def test_child_scope_resolves_through_parents():
    root = Ctx("root")
    root.provide("db", "shared")
    scope = root.child("tagger")

    assert scope.get("db") == "shared"
    scope.provide("db", "own")
    assert scope.get("db") == "own"          # 由内向外，本层遮蔽
    assert root.get("db") == "shared"        # 父层不受影响
    assert scope.path == "root/tagger"
    assert "db" in scope and scope.visible() == {"db"}


def test_duplicate_claim_in_same_scope_raises():
    scope = Ctx("root")
    scope.provide("db", "a")

    with pytest.raises(DuplicateServiceError):
        scope.provide("db", "b")
    with pytest.raises(UnknownServiceError):
        scope.get("nope")
    assert scope.get("nope", "fallback") == "fallback"


def test_duplicate_child_name_raises():
    root = Ctx("root")
    root.child("x")
    with pytest.raises(DuplicateServiceError):
        root.child("x")


def test_effects_unwind_last_in_first_out():
    order = []
    scope = Ctx("root")
    scope.effect(lambda: order.append(1))

    async def _late():
        order.append(2)

    scope.effect(_late)                        # 异步 disposer 与同步同栈
    child = scope.child("plugin")
    child.effect(lambda: order.append(3))

    asyncio.run(scope.aclose())

    assert order == [3, 2, 1]                  # 子作用域随父关闭，后进先出
    assert scope.closed and child.closed


def _boom() -> None:
    raise RuntimeError("撤销失败")


def test_unwind_runs_remaining_disposers_after_one_raises():
    """半撤销的作用域比报错的作用域更糟：剩下的必须走完。"""
    done = []
    scope = Ctx("root")
    scope.effect(lambda: done.append("ok"))
    scope.effect(_boom)                        # LIFO：先炸的其实是最后压栈的

    with pytest.raises(RuntimeError):
        asyncio.run(scope.aclose())

    assert done == ["ok"]
    assert scope._effects == []


def test_closed_scope_refuses_new_bindings():
    scope = Ctx("root")
    asyncio.run(scope.aclose())

    with pytest.raises(RuntimeError):
        scope.provide("db", "x")
    with pytest.raises(RuntimeError):
        scope.effect(lambda: None)


def test_remount_does_not_leak_parent_effects():
    """挂/卸如今是对活服务反复做的动作，父层栈不能一次比一次长。"""
    class _P(Plugin):
        name = "p"

        def apply(self, ctx):
            ctx.provide("slot", 1)

    baseline = len(ROOT._effects)
    for _ in range(5):
        ctx = asyncio.run(install(_P()))
        assert len(ROOT._effects) == baseline + 1
        asyncio.run(unmount(ctx))
    assert len(ROOT._effects) == baseline
    assert ROOT._children == {}


# ── ToolRegistry：接管与声明 ─────────────────────────────────────────────


def test_provide_shadows_builtin_and_undo_restores_identity():
    builtin = ToolRegistry.get_tool("exec_command")
    assert builtin is not None, "内置工具集应已由扫描注册"

    undo = ToolRegistry.provide("exec_command", _func("mine"), plugin_name="root/p1")
    try:
        info = ToolRegistry.get_tool("exec_command")
        assert info.func is not builtin.func
        assert info.source == "plugin"          # 来源字段第一次真的被喂上
        assert "root/p1" in ToolRegistry.get_plugins()
    finally:
        undo()

    assert ToolRegistry.get_tool("exec_command") is builtin   # 逐对象还原


def test_provide_new_name_undo_removes_it():
    undo = ToolRegistry.provide("brand_new_tool", _func("n"))
    assert ToolRegistry.get_tool("brand_new_tool") is not None
    undo()
    assert ToolRegistry.get_tool("brand_new_tool") is None


def test_provide_refuses_second_plugin_on_same_name():
    undo1 = ToolRegistry.provide("shared_name", _func("a"), plugin_name="root/p1")
    try:
        with pytest.raises(DuplicateToolError):
            ToolRegistry.provide("shared_name", _func("b"), plugin_name="root/p2")
        # 同一插件重写自己的槽是常规操作（改一版实现），不拒绝
        undo2 = ToolRegistry.provide("shared_name", _func("c"), plugin_name="root/p1")
        assert ToolRegistry.get_func("shared_name")() == "c"
        undo2()
        assert ToolRegistry.get_func("shared_name")() == "a"
    finally:
        undo1()
    assert ToolRegistry.get_tool("shared_name") is None


def test_undo_restores_the_chain_in_lifo_order():
    """撤销恢复的是自己遮蔽的那个值，所以只有按 LIFO 弹栈才成链。

    效应栈保证了这一点（后绑的先撤）。反过来说，调用方拿着两个 undo 乱序调用
    就会复活一个陈旧挂载——契约层的正确用法只有 ``ctx.effect`` / ``aclose``
    这一条路，手动持把手是调用方自己的责任。
    """
    ToolRegistry._tools["slot_order"] = ToolInfo(
        name="slot_order", func=_func("builtin"), source="builtin"
    )
    undo1 = ToolRegistry.provide("slot_order", _func("p1"), plugin_name="root/p1")
    undo2 = ToolRegistry.provide("slot_order", _func("p2"), plugin_name="root/p1")

    undo2()
    assert ToolRegistry.get_func("slot_order")() == "p1"
    undo1()
    assert ToolRegistry.get_func("slot_order")() == "builtin"


def test_undo_of_a_superseded_provide_does_nothing():
    """身份守卫：这个名字后来被别处改写时，先前那次的 undo 不能把它换回去。

    ``register_tool``（自创工具恢复、MCP 加载）就是这样一个「别处」——它不走
    作用域，也不受插件占用规则约束。
    """
    ToolRegistry._tools["slot_order"] = ToolInfo(
        name="slot_order", func=_func("builtin"), source="builtin"
    )
    undo1 = ToolRegistry.provide("slot_order", _func("p1"), plugin_name="root/p1")
    ToolRegistry.register_tool("slot_order", _func("later"), source="dynamic")

    undo1()
    assert ToolRegistry.get_func("slot_order")() == "later"


def test_register_refuses_duplicate_name_and_replace_opts_in():
    """装饰器是内置声明自己拥有的槽：同名两处是 bug，不是配置。"""
    ToolRegistry._tools["dup_reg"] = ToolInfo(
        name="dup_reg", func=_func("first"), source="builtin"
    )
    try:
        with pytest.raises(DuplicateToolError):
            ToolRegistry.register("dup_reg")(lambda: 1)
        ToolRegistry.register("dup_reg", replace=True)(lambda: 2)
        assert ToolRegistry.get_func("dup_reg")() == 2
    finally:
        ToolRegistry._tools.pop("dup_reg", None)


# ── service_registry：端口接管 ──────────────────────────────────────────


def test_provide_capability_restores_previous_provider():
    original = lambda: "orig"      # noqa: E731 — 端口就是存 callable
    service_registry.register_capability("slice_probe", original)
    try:
        undo = service_registry.provide_capability("slice_probe", lambda: "swapped")
        assert service_registry.get_capability("slice_probe")() == "swapped"
        undo()
        assert service_registry.get_capability("slice_probe") is original

        undo2 = service_registry.provide_capability("brand_new_port", lambda: "new")
        assert service_registry.has_capability("brand_new_port")
        undo2()
        assert not service_registry.has_capability("brand_new_port")

        # 被后来者接管时，先前那次的 undo 什么都不做
        undo_a = service_registry.provide_capability("slice_probe", lambda: "a")
        service_registry.provide_capability("slice_probe", lambda: "b")
        undo_a()
        assert service_registry.get_capability("slice_probe")() == "b"
    finally:
        service_registry.unregister_capability("slice_probe")
        service_registry.unregister_capability("brand_new_port")


# ── infra/ports：横切端口接管（第二套注册表，点号名）────────────────────


def test_provide_port_restores_previous_provider():
    """provide 的语义只有「可撤销」，撤销按身份判断——和 provide_capability 同判据。

    两条注册表必须各自能被插件换掉：只给了业务那一半的话，「候选缝全部可切换」实际
    上说的是「一半」。
    """
    original = object()
    ports.register("slice.probe", original)
    try:
        undo = ports.provide("slice.probe", object())
        assert ports.resolve("slice.probe") is not original
        undo()
        assert ports.resolve("slice.probe") is original

        undo2 = ports.provide("brand_new_port.x", object())
        assert ports.registered("brand_new_port.x")
        undo2()
        assert not ports.registered("brand_new_port.x")

        # 后来者接管时，先前那次的 undo 什么都不做——不能把别人的实现当自己的前任清掉
        mine = object()
        undo_a = ports.provide("slice.probe", mine)
        someone_else = object()
        ports.register("slice.probe", someone_else)
        undo_a()
        assert ports.resolve("slice.probe") is someone_else
    finally:
        ports.unregister("slice.probe")
        ports.unregister("brand_new_port.x")


def test_provide_port_rejects_empty_name():
    with pytest.raises(ValueError):
        ports.provide("", object())


def test_ctx_provide_port_swaps_and_restores():
    """ctx.provide_port 是 provide 的时效包装：作用域内换掉，退出回到前任。"""
    builtin = object()
    ports.register("slice.probe", builtin)

    class _SwapPort(Plugin):
        name = "swap_port"

        def apply(self, ctx):
            ctx.provide_port("slice.probe", _SwapPort.replacement)

    _SwapPort.replacement = object()

    with pytest.raises(ValueError):
        Ctx("root").provide_port("snake_name", object())

    async def _use():
        async with mounted(_SwapPort()):
            assert ports.resolve("slice.probe") is not builtin
            return ports.resolve("slice.probe")

    inside = asyncio.run(_use())
    assert ports.resolve("slice.probe") is builtin
    assert inside is not builtin

# ── 契约层把原语包成时效绑定 ────────────────────────────────────────────

class _Swap(Plugin):
    name = "swapin"

    def apply(self, ctx):
        ctx.provide_tool("exec_command", _func("swapped"))
        ctx.provide_capability("slice_probe", lambda: "swapped")


def test_bindings_are_live_in_scope_and_gone_after():
    builtin_before = ToolRegistry.get_tool("exec_command")
    seen = {}

    async def _use():
        async with mounted(_Swap()):
            seen["tool"] = ToolRegistry.get_func("exec_command")()
            seen["cap"] = service_registry.get_capability("slice_probe")()

    asyncio.run(_use())

    assert seen == {"tool": "swapped", "cap": "swapped"}
    assert ToolRegistry.get_tool("exec_command") is builtin_before
    assert not service_registry.has_capability("slice_probe")


def test_install_rolls_back_a_half_mounted_plugin():
    class _Half(Plugin):
        name = "half"

        def apply(self, ctx):
            ctx.provide_tool("exec_command", _func("bad"))
            raise RuntimeError("apply 走到一半炸了")

    builtin_before = ToolRegistry.get_tool("exec_command")
    with pytest.raises(RuntimeError):
        asyncio.run(install(_Half()))

    assert ToolRegistry.get_tool("exec_command") is builtin_before
    assert "half" not in ROOT._children          # 不留半挂载


def test_provided_tool_reaches_the_model_visibility_funnel():
    """绑定必须走通真实读取路径，否则「挂上了」只是日志好看。"""
    class _P(Plugin):
        name = "visible"

        def apply(self, ctx):
            ctx.provide_tool("visible_probe", lambda: 1, description="探针")

    def _api_names():
        return [t["function"]["name"] for t in ToolRegistry.get_tools_for_api(["*"])]

    ctx = asyncio.run(install(_P()))
    try:
        assert "visible_probe" in _api_names()
    finally:
        asyncio.run(unmount(ctx))
    assert "visible_probe" not in _api_names()
