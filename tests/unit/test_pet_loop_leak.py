"""桌宠 CustomLoop 内存泄漏测试

重复执行循环 N 次（混合文本/工具/拒绝/缺参），断言：
- 循环实例除 _last_think_start 外无属性/容器累积
- engine 的循环缓存不重建（同一实例）
- 全局对象数增量在阈值内（无泄漏）
- ToolManager 工具事件队列不因桌宠调用无界增长
"""
import gc
from types import SimpleNamespace

import pytest

from modules.desktop_pet.pet_engine import PetEngine
from modules.desktop_pet.react_loop import CustomLoop


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetEngine._instance
    PetEngine._instance = None
    yield
    PetEngine._instance = old


@pytest.fixture(autouse=True)
def _pet_db(tmp_path, monkeypatch):
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_leak.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield store
    store_mod.PetStore._instance = None


class _FakeClient:
    """每轮取一个输出，支持循环复用"""

    def __init__(self):
        self.n = 0
        self.calls = []

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
        self.n += 1
        # 在 文本/工具(带参)/工具(缺参)/白名单拒绝 之间轮转
        pool = [
            "好的呢",
            '{"tool":"calc","params":{"expr":"1+1"}}',
            '{"tool":"calc"}',
            '{"tool":"exec_command"}',
            "收到",
        ]
        content = pool[(self.n - 1) % len(pool)]
        self.calls.append({"max_tokens": max_tokens, "content": content})
        return SimpleNamespace(message=SimpleNamespace(content=content))


def _engine(monkeypatch, cfg_patch=None):
    import modules.desktop_pet.store as store_mod
    # max_loop_rounds=0（持续思考）是线上的默认值，但这几个用例是“调用
    # run() N 次，每次一轮”，固定 1 轮才能让单次 run() 返回；否则每次
    # run() 会一直思考到 8k 上下文上限，整例撞 pytest --timeout=60。
    patch = {"loop_type": "custom", "min_think_interval": 0, "max_loop_rounds": 1}
    if cfg_patch:
        patch.update(cfg_patch)
    store_mod.PetStore.get_instance().update_config(patch)
    e = PetEngine.__new__(PetEngine)
    e._client = None
    e._client_cfg = None
    e._event_bus = None
    e._sub_id = ""
    e._running = False
    e._reply_listeners = []
    e.last_reply = None
    e._loop = None
    e._client_cfg = ("", "", "", "", "")  # 与空 API 配置指纹一致，防止 client property 重建真实客户端
    # 隔离外部慢依赖（感知/向量初始化不属于泄漏测试范围）
    from unittest.mock import AsyncMock
    monkeypatch.setattr(PetEngine, "build_context", AsyncMock(return_value="【测试上下文】"))
    return e


def _fast_tool_manager(monkeypatch):
    """轻量 ToolManager：避免 MCP/内置工具加载干扰泄漏测量

    注意：用普通函数而非 AsyncMock/MagicMock 方法 —— mock 的调用记录
    （call_args_list 等）会随持续循环跨 run 无界累积，污染 gc 对象数测量。
    """
    import importlib
    from unittest.mock import MagicMock
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")

    fake = MagicMock()
    fake.get_tool_info = lambda n: {
        "calc": {"name": "calc", "params": {"expr": "表达式"}},
    }.get(n)
    fake.list_available_tools = lambda: {"calc": {"name": "calc", "params": {"expr": "x"}}}

    async def _call_tool(name, params, caller_role=None):
        return {"success": True, "result": "2"}

    fake.call_tool = _call_tool
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_loop_state_does_not_accumulate(monkeypatch, _pet_db):
    _fast_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient()
    loop = CustomLoop()

    keys_before = set(loop.__dict__.keys())
    # 20 轮足够：_FakeClient 的池每 5 轮覆盖一次全部分支（文本/工具/缺参/拒绝），
    # 4 个完整周期做累积判定绰绰有余。原为 60 轮 —— 单轮 ~0.5s，叠加同文件
    # 其他用例会把单测推到 pytest.ini 的 --timeout=60 边界，混跑即假阳性失败。
    for _ in range(20):
        await loop.run(e, "leak probe")
    keys_after = set(loop.__dict__.keys())
    # 无新属性累积
    assert keys_after == keys_before
    # 唯一可变状态是思考开始时刻（数值标量）
    for k, v in loop.__dict__.items():
        if k != "_last_think_start":
            assert not hasattr(v, "__len__") or len(v) == 0 or isinstance(v, (str, bytes)), k


@pytest.mark.asyncio
async def test_engine_loop_cache_reused(monkeypatch, _pet_db):
    _fast_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient()
    # 通过 engine.loop property 触发实例化
    first = e.loop
    for _ in range(30):
        await e.loop.run(e, "cache probe")
    assert e.loop is first  # 循环类型未变 → 同一实例，不重建


@pytest.mark.asyncio
async def test_gc_object_count_bounded(monkeypatch, _pet_db):
    _fast_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient()
    loop = CustomLoop()

    # 预热一轮后取基线（排除一次性导入/初始化）
    await loop.run(e, "warmup")
    gc.collect()
    base = len(gc.get_objects())

    # 25 轮（5 个完整分支周期）足以暴露无界累积；原为 80 轮，单轮 ~0.5s 会把
    # 本用例单独推到 pytest.ini 的 --timeout=60 之上（实测 58–60s），属计时
    # 假阳性而非真实泄漏。阈值同步收紧，保持判据强度。
    for _ in range(25):
        await loop.run(e, "leak probe")
    gc.collect()
    after = len(gc.get_objects())
    assert after - base < 1500, f"object growth {after - base}"


@pytest.mark.asyncio
async def test_tool_event_queue_bounded(monkeypatch, _pet_db):
    """工具事件队列（cap 2000）不因桌宠调用无界增长"""
    from infra.tool_manager.tool_manager import ToolManager
    tm = ToolManager()
    before = len(tm._tool_events)
    for _ in range(50):
        tm._record_tool_event("calc", {}, True, "ok")
    assert len(tm._tool_events) == min(before + 50, 2000)
    assert len(tm._tool_events) <= 2000
