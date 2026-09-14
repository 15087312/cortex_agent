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
    patch = {"loop_type": "custom", "min_think_interval": 0}
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
    """轻量 ToolManager：避免 MCP/内置工具加载干扰泄漏测量"""
    import importlib
    tm_mod = importlib.import_module("infra.tool_manager.tool_manager")
    from unittest.mock import AsyncMock, MagicMock
    fake = MagicMock()
    fake.get_tool_info.side_effect = lambda n: {
        "calc": {"name": "calc", "params": {"expr": "表达式"}},
    }.get(n)
    fake.list_available_tools.return_value = {"calc": {"name": "calc", "params": {"expr": "x"}}}
    fake.call_tool = AsyncMock(return_value={"success": True, "result": "2"})
    monkeypatch.setattr(tm_mod, "ToolManager", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_loop_state_does_not_accumulate(monkeypatch, _pet_db):
    _fast_tool_manager(monkeypatch)
    e = _engine(monkeypatch)
    e._client = _FakeClient()
    loop = CustomLoop()

    keys_before = set(loop.__dict__.keys())
    for _ in range(60):
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

    for _ in range(80):
        await loop.run(e, "leak probe")
    gc.collect()
    after = len(gc.get_objects())
    # 80 轮循环后新增存活对象应有限（放宽阈值，过滤 gc 噪声）
    assert after - base < 3000, f"object growth {after - base}"


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
