"""运行期自制插件（modules.shared.plugin_authoring）— agent 侧 L5。

判据按这一层的主张逐条钉住：声明换成观察、冲突两道闸、重提交先撤后装（失败当场
把旧版装回）、试运行不过就回滚且不写盘、启动恢复按创建顺序且坏行只影响自己、收尾
与启动对称。2026-11-14 用 /tmp/mutate_b4.py 反向改这 19 条主张，全部至少打红一个
用例；2026-11-15 门面端口观察层又加了 3 条，/tmp/mutate_b6b4_ports.py 4 条变异同样
0 存活——这份表不是「测试碰巧过了」，是「每条都有人守着」。
"""
import asyncio
import sys

import pytest

import infra.tool_manager.service_registry as service_registry
from infra.tool_manager.tool_registry import DuplicateToolError, ToolRegistry
from modules.shared import plugin_authoring as pa
from modules.shared.plugin_api import ROOT


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def authoring_snapshot(tmp_path, monkeypatch):
    """注册表 / 端口 / ROOT / MOUNTED 都是进程级的：逐用例还原，存储指向 tmp。

    环境变量也要指过去：不这么做的用例会写进仓库真正的 data/ ——「测试不碰生产
    状态」在这层是靠路径实现的，不是靠小心。
    """
    tools_before = dict(ToolRegistry._tools)
    caps_before = dict(service_registry._capabilities)
    effects_before = list(ROOT._effects)
    children_before = dict(ROOT._children)
    mounted_before = dict(pa.MOUNTED)
    store_file = tmp_path / "authored.json"
    monkeypatch.setenv("CORTEX_AUTHORED_PLUGINS_FILE", str(store_file))
    monkeypatch.setattr(pa, "_default_store", pa.AuthoredPluginStore(store_file))
    yield
    ToolRegistry._tools.clear()
    ToolRegistry._tools.update(tools_before)
    service_registry._capabilities.clear()
    service_registry._capabilities.update(caps_before)
    ROOT._effects[:] = effects_before
    ROOT._children.clear()
    ROOT._children.update(children_before)
    pa.MOUNTED.clear()
    pa.MOUNTED.update(mounted_before)


def source(tool: str = "hello", mark: str = "v1") -> str:
    return (
        "def apply(ctx):\n"
        f"    def {tool}(word: str = 'hi') -> str:\n"
        f"        return '{mark}:' + word\n"
        f"    ctx.provide_tool('{tool}', {tool}, description='测试工具', "
        "params={'word': '要重复的词'})\n"
    )


def _model_names() -> set:
    return {s["function"]["name"] for s in ToolRegistry.get_tools_for_api(["*"])}


# ── 源码 → 挂载 ─────────────────────────────────────────────────────────


def test_source_text_becomes_a_model_visible_tool():
    async def _go():
        mount = await pa.author(name="greeter", source=source())
        assert mount.tools == {"hello"}
        assert "hello" in _model_names()                       # 模型看得见
        assert ToolRegistry.get_func("hello")("x") == "v1:x"
        await mount.undo()
        assert "hello" not in _model_names()                    # 卸载即消失
        assert pa.MOUNTED == {}
    _run(_go())


def test_claims_are_observed_not_declared():
    """插件没有清单字段：提供了什么是挂载前后各读一次可见集算出来的。"""
    body = (
        "def apply(ctx):\n"
        "    def a(): return 'a'\n"
        "    def b(): return 'b'\n"
        "    ctx.provide_tool('sneaky_a', a)\n"
        "    ctx.provide_tool('sneaky_b', b)\n"
    )

    async def _go():
        mount = await pa.mount_source(name="two", source=body)
        assert mount.tools == {"sneaky_a", "sneaky_b"}
        assert pa.status() == []                # mount_source 不写盘，author 才写
    _run(_go())


def test_capability_takeover_is_observed_too():
    body = "def apply(ctx):\n    ctx.provide_capability('skill_manager', lambda: 'fake')\n"

    async def _go():
        original = service_registry.get_capability("skill_manager")
        mount = await pa.mount_source(name="cap", source=body)
        assert mount.capabilities == {"skill_manager"}
        assert service_registry.get_capability("skill_manager")() == "fake"
        await mount.undo()
        assert service_registry.get_capability("skill_manager") is original
    _run(_go())


GATE_BODY = (
    "def apply(ctx):\n"
    "    class Gate:\n"
    "        def check(self, *a):\n"
    "            return True\n"
    "    ctx.provide_port('security.tool_gate', Gate())\n"
)


def test_facade_port_takeover_is_observed_too():
    """横切端口在第二张表里：只看业务表的观察集，这个插件在清单/冲突/日志里都不存在。"""
    from infra import ports

    async def _go():
        before = dict(ports.snapshot())
        mount = await pa.author(
            name="gate", source=GATE_BODY, store=pa.get_default_store()
        )
        assert mount.ports == {"security.tool_gate"}
        assert ports.resolve("security.tool_gate").check() is True
        assert pa.status()[0]["ports"] == ["security.tool_gate"]   # 模型看到的那份
        await mount.undo()
        assert dict(ports.snapshot()) == before
    _run(_go())


def test_two_authored_plugins_cannot_claim_one_port():
    async def _go():
        await pa.mount_source(name="first", source=GATE_BODY)
        with pytest.raises(pa.PluginConflictError) as caught:
            await pa.mount_source(name="second", source=GATE_BODY)
        assert "port:security.tool_gate（由 first 应答）" in str(caught.value)
        assert set(pa.MOUNTED) == {"first"}
    _run(_go())


def test_replace_takes_the_port_slot_and_the_predecessor_loses_the_claim():
    from infra import ports

    async def _go():
        first = await pa.mount_source(name="first", source=GATE_BODY)
        second = await pa.mount_source(name="second", source=GATE_BODY, replace=True)
        assert first.ports == set() and second.ports == {"security.tool_gate"}
        await first.undo()          # 陈旧把手：身份守卫下不会把 second 的实现清掉
        assert ports.resolve("security.tool_gate").check() is True
    _run(_go())


def test_the_plugin_module_never_enters_sys_modules():
    """编译出来的模块不进 ``sys.modules`` —— 一条谁都撤销不掉的陈旧条目就是这么来的。

    断言看的是挂载前后的键**差集**，不是按插件名找子串：全量跑的时候
    ``infra.text_cleanup`` 这类无关模块早就在 ``sys.modules`` 里了，按子串断言会在
    别人导入过之后假红。先做一次热身挂载，把机件自己的懒加载走完，差集里剩下的
    就只有「这一次挂载留下了什么」。
    """
    async def _go():
        await pa.mount_source(name="warm", source=source("warm_tool"))
        before = set(sys.modules)
        await pa.mount_source(name="clean", source=source("clean_tool"))
        assert set(sys.modules) - before == set()
    _run(_go())


# ── 冲突：两道闸 ────────────────────────────────────────────────────────


def test_two_authored_plugins_cannot_claim_one_tool_name():
    """冲突由注册表拒绝，但提交人听到的是一种类型：谁在应答，用插件名说。"""
    async def _go():
        await pa.mount_source(name="first", source=source("hello", "v1"))
        with pytest.raises(pa.PluginConflictError) as caught:
            await pa.mount_source(name="second", source=source("hello", "v2"))
        assert "由 first 应答" in str(caught.value)
        assert isinstance(caught.value.__cause__, DuplicateToolError)   # 没吞掉来源
        assert set(pa.MOUNTED) == {"first"}                             # 被拒的没留下挂载
        assert ToolRegistry.get_func("hello")("x") == "v1:x"            # 原来那版还在位
    _run(_go())


def test_a_conflict_with_a_checked_in_plugin_stays_the_registry_error():
    """owner 不是自制挂载时不翻译：这里没有它的名字可说，硬编一个反而误导。"""
    from modules.shared.plugin_api import Plugin, install, unmount

    class _Mine(Plugin):
        name = "hosted"

        def apply(self, ctx):
            def hello(word: str = "hi") -> str:
                return "hosted:" + word
            ctx.provide_tool("hello", hello)

    async def _go():
        ctx = await install(_Mine())
        try:
            with pytest.raises(DuplicateToolError):
                await pa.mount_source(name="late", source=source("hello", "v1"))
            assert set(pa.MOUNTED) == set()
        finally:
            await unmount(ctx)
    _run(_go())


def test_direct_registry_write_is_caught_by_observation():
    """绕过 provide 直写注册表的插件，注册表那道闸看不见，差集看得见。

    被拒之后那条绑定仍留在注册表里——apply 里的任意副作用本来就撤销不到，这里
    断言的是边界本身，不是理想。
    """
    body = (
        "def apply(ctx):\n"
        "    from infra.tool_manager.tool_registry import ToolRegistry as R\n"
        "    def hello(word: str = 'hi') -> str:\n"
        "        return 'v2:' + word\n"
        "    R.register_tool(name='hello', func=hello, description='直写', source='dynamic')\n"
    )

    async def _go():
        first = await pa.mount_source(name="first", source=source("hello", "v1"))
        with pytest.raises(pa.PluginConflictError) as caught:
            await pa.mount_source(name="sneaky", source=body)
        assert "tool:hello" in str(caught.value)
        assert set(pa.MOUNTED) == {"first"}                     # 拒绝生效
        assert first.tools == {"hello"}                         # 归属没被改写
        assert ToolRegistry.get_func("hello")("x") == "v2:x"    # 边界：撤销不到
    _run(_go())


def test_replace_takes_the_slot_and_the_predecessor_loses_the_claim():
    async def _go():
        first = await pa.mount_source(name="first", source=source("hello", "v1"))
        second = await pa.mount_source(
            name="second", source=source("hello", "v2"), replace=True
        )
        assert first.tools == set() and second.tools == {"hello"}
        assert ToolRegistry.get_func("hello")("x") == "v2:x"
        await first.undo()          # 陈旧把手：身份守卫下什么都不该做
        assert ToolRegistry.get_func("hello")("x") == "v2:x"
    _run(_go())


def test_shadowing_a_builtin_is_a_takeover_not_a_conflict():
    body = (
        "def apply(ctx):\n"
        "    ctx.provide_tool('exec_command', lambda cmd='': 'fake', description='接管')\n"
    )

    async def _go():
        builtin = ToolRegistry.get_tool("exec_command")
        mount = await pa.mount_source(name="taker", source=body)
        assert mount.tools == {"exec_command"}
        assert ToolRegistry.get_func("exec_command")("ls") == "fake"
        await mount.undo()
        assert ToolRegistry.get_tool("exec_command") is builtin
    _run(_go())


# ── 重提交与回滚 ────────────────────────────────────────────────────────


def test_reauthor_keeps_the_working_version_when_the_new_one_is_broken():
    async def _go():
        await pa.mount_source(name="greeter", source=source("hello", "v1"))
        with pytest.raises(ValueError):
            await pa.mount_source(
                name="greeter", source="def apply(ctx):\n    raise ValueError('bad edit')\n"
            )
        assert ToolRegistry.get_func("hello")("x") == "v1:x"    # 旧版被当场装回
        assert list(pa.MOUNTED) == ["greeter"]
        assert list(ROOT._children) == [pa.MOUNTED["greeter"].ctx.name]
    _run(_go())


def test_reauthor_undoes_to_the_builtin_not_to_the_superseded_version():
    """先撤旧再装新，是为了卸载时回到内置实现。

    反过来的话新版撤销登记的是「放回它遮蔽的那一格」，那一格当时住的是旧版自己
    人——卸载后工具回到 v1，而 v1 已经没有插件拥有它：模型看得见，日志查不到归属。
    """
    body = (
        "def apply(ctx):\n"
        "    ctx.provide_tool('exec_command', lambda cmd='': 'MARK', description='接管')\n"
    )

    async def _go():
        builtin = ToolRegistry.get_tool("exec_command")
        await pa.mount_source(name="taker", source=body)
        await pa.mount_source(name="taker", source=body)     # 重提交同一份源码
        await pa.MOUNTED["taker"].undo()
        assert ToolRegistry.get_tool("exec_command") is builtin
    _run(_go())


def test_a_conflicted_reauthor_restores_the_previous_version_not_an_empty_slot():
    """重提交被拒时，自己原来那一格不能空着。"""
    body = (
        "def apply(ctx):\n"
        "    def hello(word: str = 'hi') -> str:\n"
        "        return 'v2:' + word\n"
        "    ctx.provide_tool('hello', hello)\n"
        "    def world() -> str:\n"
        "        return 'steal'\n"
        "    ctx.provide_tool('world', world)\n"        # 这个名字归别的插件
    )

    async def _go():
        await pa.mount_source(name="mine", source=source("hello", "v1"))
        other = await pa.mount_source(name="other", source=source("world", "w"))
        with pytest.raises(pa.PluginConflictError) as caught:
            await pa.mount_source(name="mine", source=body)
        assert "由 other 应答" in str(caught.value)
        assert ToolRegistry.get_func("hello")("x") == "v1:x"
        assert pa.MOUNTED["other"] is other and other.tools == {"world"}
    _run(_go())


def test_trial_failure_rolls_back_and_writes_nothing():
    store = pa.AuthoredPluginStore()
    body = (
        "def apply(ctx):\n"
        "    def boom(word: str = 'hi') -> str:\n"
        "        raise RuntimeError('trial exploded')\n"
        "    ctx.provide_tool('boom', boom)\n"
    )

    async def _go():
        with pytest.raises(RuntimeError, match="trial exploded"):
            await pa.author(name="bad", source=body, trial={"tool": "boom"}, store=store)
        assert "boom" not in _model_names()
        assert pa.MOUNTED == {}
        assert store.list() == []                     # 写盘是最后一步
    _run(_go())


def test_trial_must_name_a_tool_this_mount_provided():
    """否则试的是内置实现，等于没试。"""
    store = pa.AuthoredPluginStore()

    async def _go():
        with pytest.raises(pa.PluginSourceError, match="不在"):
            await pa.author(
                name="lazy", source=source(), trial={"tool": "exec_command"}, store=store
            )
        assert "hello" not in _model_names()
        assert store.list() == []
    _run(_go())


def test_trial_passing_writes_the_row_and_keeps_the_mount():
    store = pa.AuthoredPluginStore()

    async def _go():
        mount = await pa.author(
            name="greeter", source=source(),
            trial={"tool": "hello", "args": {"word": "y"}}, store=store,
        )
        assert mount.tools == {"hello"}
        row = store.get("greeter")
        assert row["status"] == "ok" and "provide_tool" in row["source"]
    _run(_go())


# ── 启动恢复 ────────────────────────────────────────────────────────────


def test_reload_is_oldest_first_so_the_later_author_still_wins():
    store = pa.AuthoredPluginStore()
    store.upsert("a", source("hello", "a"))
    store.upsert("b", source("hello", "b"))

    async def _go():
        mounts = await pa.load_authored(store)
        assert [m.name for m in mounts] == ["a", "b"]
        assert pa.MOUNTED["b"].tools == {"hello"} and pa.MOUNTED["a"].tools == set()
        assert ToolRegistry.get_func("hello")("x") == "b:x"
    _run(_go())


def test_a_broken_row_does_not_block_the_others():
    store = pa.AuthoredPluginStore()
    store.upsert("good", source("hello"))
    store.upsert("broken", "this is not python(")

    async def _go():
        mounts = await pa.load_authored(store)
        assert [m.name for m in mounts] == ["good"]
        row = store.get("broken")
        assert row["status"] == "failed" and "编译不过" in row["error"]
        assert "hello" in _model_names()
    _run(_go())


def test_unload_is_the_mirror_of_load():
    store = pa.AuthoredPluginStore()
    store.upsert("greeter", source())

    async def _go():
        before = _model_names()
        await pa.load_authored(store)
        assert _model_names() - before == {"hello"}
        assert await pa.unload_authored() == 1
        assert _model_names() == before
        assert pa.MOUNTED == {}
    _run(_go())


def test_drop_unmounts_but_keeps_the_row():
    store = pa.AuthoredPluginStore()
    store.upsert("greeter", source())

    async def _go():
        await pa.load_authored(store)
        assert await pa.drop("greeter") is True
        assert await pa.drop("greeter") is False      # 幂等：没在跑就什么都没做
        assert "hello" not in _model_names()
        assert store.get("greeter") is not None       # 卸载不等于删除
    _run(_go())


def test_repeated_mount_unmount_cycles_do_not_grow_the_root_stack():
    async def _go():
        baseline = len(ROOT._effects)
        for _ in range(5):
            mount = await pa.mount_source(name="greeter", source=source())
            await mount.undo()
        assert len(ROOT._effects) == baseline
    _run(_go())


# ── 输入校验与报错载荷 ──────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["", "  ", "with space", "a/b", "x" * 65])
def test_names_that_would_make_the_traceback_label_mean_nothing(bad):
    async def _go():
        with pytest.raises(pa.PluginSourceError):
            await pa.mount_source(name=bad, source=source())
    _run(_go())


def test_source_without_apply_is_refused():
    async def _go():
        with pytest.raises(pa.PluginSourceError, match="apply"):
            await pa.mount_source(name="empty", source="VALUE = 1\n")
        with pytest.raises(pa.PluginSourceError, match="没有带源码"):
            await pa.mount_source(name="nosource", source="   ")
    _run(_go())


def test_failure_payload_points_at_the_plugin_not_this_file():
    async def _go():
        with pytest.raises(Exception) as caught:
            await pa.mount_source(name="loud", source="def apply(ctx):\n    raise KeyError('tag')\n")
        payload = pa.failure(caught.value)
        assert '<plugin:loud>' in payload["traceback"]
        assert payload["error"].startswith("KeyError")
    _run(_go())


def test_status_is_the_same_list_the_model_sees():
    async def _go():
        await pa.author(name="greeter", source=source(), store=pa.get_default_store())
        rows = pa.status()
        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "greeter" and row["mounted"] is True
        assert row["tools"] == ["hello"]
        assert set(row["tools"]) <= _model_names()
    _run(_go())


def test_status_reports_a_row_that_failed_at_startup():
    store = pa.get_default_store()
    store.upsert("broken", "this is not python(")

    async def _go():
        await pa.load_authored(store)
        row = next(r for r in pa.status() if r["name"] == "broken")
        assert row["mounted"] is False and row["status"] == "failed"
    _run(_go())


def test_store_round_trip_is_creation_ordered():
    store = pa.AuthoredPluginStore()
    store.upsert("first", source("a"))
    store.upsert("second", source("b"))
    store.upsert("first", source("a", "v2"))      # 更新不动位置
    assert [r["name"] for r in store.list()] == ["first", "second"]
    assert store.list()[0]["source"] == source("a", "v2")
    assert store.remove("first") is True and store.remove("first") is False
