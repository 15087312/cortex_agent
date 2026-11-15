"""自制插件的两个对外门面：模型面工具 + HTTP 路由（B5 的后两条入口）。

判据是「门面不许加策略」：两个门面必须和 ``plugin_authoring`` 同一套语义——挂了
就看得见、失败不留半挂载、卸了内置就回来、盘上写不写只由试运行决定。这个文件
额外钉门面自己带进来的三件事：

* 同步工具面怎么跑协程（``_run`` 桥绝不能在被调用者已有的循环上 ``asyncio.run``）；
* 路由的状态码分派（提交人的错是 4xx，不是 500）；
* ``DELETE`` 的 ``forget`` 开关（卸而不删 / 卸且删）。
"""
import ast
import asyncio
import inspect

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import infra.tool_manager.service_registry as service_registry
import modules.shared.plugin_authoring as pa
from infra.tool_manager.tool_registry import ToolRegistry
from modules.shared import plugin_authoring_api as papi
from modules.shared import plugin_authoring_tools as pt
from modules.shared.plugin_api import ROOT

ENTRY_TOOLS = ("mount_plugin", "unmount_plugin", "list_plugins", "delete_plugin")


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    """注册表 / 端口 / ROOT / MOUNTED 逐用例还原，存储指向 tmp。

    和 test_plugin_authoring.py 同一条理由：这四个面全是进程级状态，而
    「测试不碰生产状态」在这层是靠路径实现的。
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


@pytest.fixture
def client():
    """只挂这一条路由的最小 app（不需要后端整套 lifespan）。"""
    app = FastAPI()
    app.include_router(papi.router)
    with TestClient(app) as c:
        yield c


def source(tool: str = "hello", mark: str = "v1") -> str:
    return (
        "def apply(ctx):\n"
        f"    def {tool}(word: str = 'hi') -> str:\n"
        f"        return '{mark}:' + word\n"
        f"    ctx.provide_tool('{tool}', {tool}, description='测试工具', "
        "params={'word': '要重复的词'})\n"
    )


def _visible() -> set:
    return {s["function"]["name"] for s in ToolRegistry.get_tools_for_api(["*"])}


# ── 装配层确实 import 了工具面 ────────────────────────────────────────────


def test_the_entry_tools_are_registered():
    for name in ENTRY_TOOLS:
        assert ToolRegistry.get_tool(name) is not None, f"{name} 没进模型可见集"


def test_bootstrap_imports_the_authored_plugin_tools():
    """装饰器注册是 import 副作用：装配层不 import 它，模型就没有这个入口。

    pkgutil 扫描已经被 B1 拆掉了，所以「谁注册了它」必须能在装配层里读出来，
    而不是靠某个入口恰好 import 了某个包。
    """
    import bootstrap

    tree = ast.parse(inspect.getsource(bootstrap.register_business_capabilities))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert "modules.shared.plugin_authoring_tools" in imported


# ── 模型面工具 ────────────────────────────────────────────────────────────


def test_mount_plugin_makes_the_tool_usable_and_persists_it():
    out = pt.mount_plugin("greeter", source())
    assert out.startswith("✅")
    assert "hello" in _visible()
    assert ToolRegistry.get_func("hello")("x") == "v1:x"
    assert pa.get_default_store().get("greeter")["status"] == "ok"


def test_mount_plugin_reports_a_conflict_by_plugin_name():
    pt.mount_plugin("first", source("hello", "v1"))
    out = pt.mount_plugin("second", source("hello", "v2"))
    assert out.startswith("❌")
    assert "由 first 应答" in out
    assert ToolRegistry.get_func("hello")("x") == "v1:x"    # 被拒的没顶掉任何人
    assert set(pa.MOUNTED) == {"first"}


def test_mount_plugin_hands_back_the_authors_own_traceback():
    out = pt.mount_plugin("boom", "def apply(ctx):\n    raise KeyError('tag')\n")
    assert out.startswith("❌")
    assert "KeyError" in out
    assert "<plugin:boom>" in out                            # 指向提交进来的那一行
    assert "boom" not in pa.MOUNTED


def test_mount_plugin_rejects_a_malformed_trial_without_touching_the_registry():
    out = pt.mount_plugin("lazy", source(), trial="这不是 json")
    assert out.startswith("❌") and "trial" in out
    assert "hello" not in _visible()
    assert pa.MOUNTED == {}
    assert pa.get_default_store().list() == []


def test_mount_plugin_rolls_back_when_the_trial_explodes():
    body = (
        "def apply(ctx):\n"
        "    def boom(word: str = 'hi') -> str:\n"
        "        raise RuntimeError('trial exploded')\n"
        "    ctx.provide_tool('boom_tool', boom)\n"
    )
    out = pt.mount_plugin("bad", body, trial={"tool": "boom_tool"})
    assert out.startswith("❌") and "trial exploded" in out
    assert "boom_tool" not in _visible()
    assert pa.get_default_store().list() == []


def test_unmount_keeps_the_row_and_delete_forgets_it():
    pt.mount_plugin("greeter", source())
    out = pt.unmount_plugin("greeter")
    assert out.startswith("✅")
    assert "hello" not in _visible()
    assert pa.get_default_store().get("greeter") is not None     # 重启后还会回来
    assert pt.unmount_plugin("greeter").startswith("❌")         # 没在跑就说没在跑

    pt.mount_plugin("greeter", source())
    assert pt.delete_plugin("greeter").startswith("✅")
    assert "hello" not in _visible()
    assert pa.get_default_store().get("greeter") is None
    assert pt.delete_plugin("greeter").startswith("❌")


def test_list_plugins_says_the_same_thing_the_registry_does():
    assert "还没有提交过" in pt.list_plugins()
    pt.mount_plugin("greeter", source())
    listing = pt.list_plugins()
    assert "greeter（在跑）" in listing and "hello" in listing
    # 归属与模型可见集同源：列出来的名字必须真的在工具列表里
    assert {"hello", "mount_plugin"} <= _visible()


def test_the_bridge_does_not_asyncio_run_on_someone_elses_loop():
    """工具面是同步的，挂载是协程：桥在「本线程已有循环」时必须换线程，不能报错。

    真跑的时候工具执行在 worker 线程（asyncio.to_thread），那个线程没有循环；
    但直接在一个循环里调用（别的插件、测试、嵌入式入口）也不能炸。
    """
    async def _inside_a_loop():
        return pt.mount_plugin("greeter", source())

    out = asyncio.run(_inside_a_loop())
    assert out.startswith("✅")
    assert "hello" in _visible()
    assert ToolRegistry.get_func("hello")("y") == "v1:y"


# ── HTTP 路由 ─────────────────────────────────────────────────────────────


def test_post_mounts_and_get_shows_it(client):
    resp = client.post("/plugins/authored", json={"name": "greeter", "source": source()})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["tools"] == ["hello"]
    assert body["data"]["trial_ran"] is False
    assert "hello" in _visible()

    listed = client.get("/plugins/authored").json()["data"]["plugins"]
    assert [r["name"] for r in listed] == ["greeter"]
    assert listed[0]["mounted"] is True and listed[0]["tools"] == ["hello"]

    one = client.get("/plugins/authored/greeter").json()["data"]
    assert one["mounted"] is True and one["row"]["status"] == "ok"


def test_post_with_uncompilable_source_is_a_422_with_the_author_line(client):
    resp = client.post("/plugins/authored", json={"name": "broken", "source": "def ("})
    assert resp.status_code == 422
    message = resp.json()["error"]["message"]
    assert "编译不过" in message
    assert "broken" not in pa.MOUNTED
    assert pa.get_default_store().list() == []                 # 失败不留半挂载


def test_post_conflict_is_a_400_naming_who_answers(client):
    client.post("/plugins/authored", json={"name": "first", "source": source("hello", "v1")})
    resp = client.post("/plugins/authored", json={"name": "second", "source": source("hello", "v2")})
    assert resp.status_code == 400
    assert "由 first 应答" in resp.json()["error"]["message"]
    assert ToolRegistry.get_func("hello")("x") == "v1:x"

    # 明确接管才放行
    taken = client.post(
        "/plugins/authored", json={"name": "second", "source": source("hello", "v2"), "replace": True}
    )
    assert taken.status_code == 200
    assert ToolRegistry.get_func("hello")("x") == "v2:x"


def test_a_plugin_that_explodes_is_a_response_not_a_500(client):
    resp = client.post(
        "/plugins/authored",
        json={"name": "loud", "source": "def apply(ctx):\n    raise RuntimeError('作者的一行')\n"},
    )
    assert resp.status_code == 400
    assert "作者的一行" in resp.json()["error"]["message"]
    assert "<plugin:loud>" in resp.json()["error"]["message"]
    assert pa.MOUNTED == {}


def test_trial_runs_through_the_route(client):
    ok = client.post(
        "/plugins/authored",
        json={"name": "greeter", "source": source(), "trial": {"tool": "hello", "args": {"word": "y"}}},
    )
    assert ok.status_code == 200
    assert ok.json()["data"]["trial_ran"] is True

    bad = client.post(
        "/plugins/authored",
        json={"name": "lazy", "source": source("other"), "trial": {"tool": "exec_command"}},
    )
    assert bad.status_code == 422
    assert "不在" in bad.json()["error"]["message"]
    assert "other" not in _visible()


def test_get_one_mounted_plugin_with_no_row(client):
    """只挂载没写盘（mount_source）时，路由说的是「没在跑」而不是 404。

    两个门面的差别只在写盘与否，实时归属必须同源：这里读的是 MOUNTED，不是行。
    """
    asyncio.run(pa.mount_source(name="ephemeral", source=source("ghost")))
    body = client.get("/plugins/authored/ephemeral").json()["data"]
    assert body["row"] is None
    assert body["mounted"] is True and body["tools"] == ["ghost"]
    assert client.get("/plugins/authored/nosuch").status_code == 404


def test_delete_forgets_by_default_and_can_be_told_not_to(client):
    client.post("/plugins/authored", json={"name": "greeter", "source": source()})
    resp = client.delete("/plugins/authored/greeter?forget=false")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["unmounted"] is True and data["removed_from_store"] is False
    assert "hello" not in _visible()
    assert pa.get_default_store().get("greeter") is not None

    resp = client.delete("/plugins/authored/greeter")
    assert resp.json()["data"]["removed_from_store"] is True
    assert pa.get_default_store().get("greeter") is None
    assert client.delete("/plugins/authored/greeter").status_code == 404


def test_the_two_doors_agree(client):
    """HTTP 提交的东西模型能看见并卸掉；工具提交的 HTTP 能列出来。"""
    client.post("/plugins/authored", json={"name": "from_http", "source": source("via_http")})
    assert "via_http" in _visible()
    assert "from_http（在跑）" in pt.list_plugins()
    assert pt.unmount_plugin("from_http").startswith("✅")
    assert "via_http" not in _visible()

    pt.mount_plugin("from_tool", source("via_tool"))
    listed = client.get("/plugins/authored").json()["data"]["plugins"]
    row = next(r for r in listed if r["name"] == "from_tool")
    assert row["mounted"] is True and row["tools"] == ["via_tool"]
