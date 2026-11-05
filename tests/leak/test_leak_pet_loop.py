"""桌宠持续思考循环（CustomLoop）内存安全回归

类型: 缓存型泄漏防护——「循环永不因空拍终止」语义下的有界性：
连续空拍长期空转是常态路径，循环实例 / engine 容器 / gc 存活对象 /
退避等待时长都必须有界；退避倍率在超大空拍计数下不得溢出。

（空拍退避改造后补充：rounds=0 持续循环 + 空拍退避按 2^n 增长封顶 idle_backoff_max）
"""
import asyncio
import gc
from types import SimpleNamespace

import pytest

from modules.desktop_pet.pet_engine import PetEngine
from modules.desktop_pet.react_loop import CustomLoop

pytestmark = pytest.mark.leak


@pytest.fixture(autouse=True)
def _cleanup_singleton():
    old = PetEngine._instance
    PetEngine._instance = None
    yield
    PetEngine._instance = old


@pytest.fixture
def pet_db(tmp_path, monkeypatch):
    import modules.desktop_pet.store as store_mod
    store = store_mod.PetStore(str(tmp_path / "pet_leak.db"))
    monkeypatch.setattr(store_mod.PetStore, "_instance", store)
    yield store
    store_mod.PetStore._instance = None


def _engine(monkeypatch, cfg_patch=None):
    """最小 engine 桩：关闭工具（tools_enabled=False）排除 ToolManager 干扰"""
    import modules.desktop_pet.store as store_mod
    patch = {"loop_type": "custom", "min_think_interval": 0, "tools_enabled": False}
    if cfg_patch:
        patch.update(cfg_patch)
    store_mod.PetStore.get_instance().update_config(patch)
    e = PetEngine.__new__(PetEngine)
    e._client = None
    e._client_cfg = ("", "", "", "", "")  # 与空 API 配置指纹一致，防止 client property 重建真实客户端
    e._event_bus = None
    e._sub_id = ""
    e._running = False
    e._reply_listeners = []
    e.last_reply = None
    e._loop = None
    from unittest.mock import AsyncMock
    monkeypatch.setattr(PetEngine, "build_context", AsyncMock(return_value="【测试上下文】"))
    return e


class _IdleHeavyClient:
    """空拍/产出按 2:1 循环供给（text, empty, empty, ...），calls 计数供断言"""

    def __init__(self):
        self.calls = 0

    async def chat_stream(self, messages=None, max_tokens=None, on_token=None, **kw):
        self.calls += 1
        content = f"回复{self.calls}" if self.calls % 3 == 1 else ""
        return SimpleNamespace(message=SimpleNamespace(content=content))


def test_idle_backoff_mult_no_overflow_huge_streak():
    """超大空拍计数下倍率不溢出（2.0**n 先钉顶再取幂，防 OverflowError）

    永不终止语义下 empty_streak 无上界，指数若先计算后封顶会在
    空拍 ~1024 次（退避关闭时 ~17 分钟空转）时抛 OverflowError 崩循环。
    """
    assert CustomLoop._idle_backoff_mult(5000, 8) == 8.0
    assert CustomLoop._idle_backoff_mult(10 ** 9, 8) == 8.0
    assert CustomLoop._idle_backoff_mult(1024, 1) == 1.0  # cap=1 关退避
    assert CustomLoop._idle_backoff_mult(0, 8) == 1.0
    assert CustomLoop._idle_backoff_mult(-3, 8) == 1.0  # 负数安全


@pytest.mark.asyncio
async def test_continuous_loop_objects_bounded(monkeypatch, pet_db):
    """持续循环（rounds=120，含 2/3 空拍）多轮运行后 gc 存活对象数有界"""
    e = _engine(monkeypatch, {"max_loop_rounds": 120})
    e._client = _IdleHeavyClient()
    sleeps: list = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    loop = CustomLoop()

    # 预热 12 轮取基线（排除一次性导入/初始化）；注意 probe 的 _engine 会覆盖
    # 共享 store 配置，用完即恢复主轮数
    probe = _engine(monkeypatch, {"max_loop_rounds": 12})
    probe._client = _IdleHeavyClient()
    await CustomLoop().run(probe, "warmup")
    del probe
    pet_db.update_config({"max_loop_rounds": 120})

    loop = CustomLoop()
    gc.collect()
    base = len(gc.get_objects())

    await loop.run(e, "leak probe")
    gc.collect()
    after = len(gc.get_objects())
    # 客户端调用节奏：第 1/4/7… 次产出，其余空拍；_react_cycle 内空拍会立即重试，
    # 因此首轮 1 次调用即产出，之后每轮固定 3 次（空、空、产出）→ 1 + 3×(rounds-1)。
    assert e._client.calls == 1 + 3 * (120 - 1)
    # 120 轮后新增存活对象有限（放宽阈值过滤 gc 噪声）
    assert after - base < 3000, f"object growth {after - base}"


@pytest.mark.asyncio
async def test_continuous_loop_backoff_waits_capped(monkeypatch, pet_db):
    """空拍退避等待时长有界：无论空拍多少次，等待不超过 base×cap（+ε）"""
    e = _engine(monkeypatch, {"min_think_interval": 1.0, "max_loop_rounds": 60,
                              "idle_backoff_max": 8})
    e._client = _IdleHeavyClient()
    sleeps: list = []

    async def _fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
    await CustomLoop().run(e, "hi")
    # 同上：首轮 1 次产出，之后每轮 3 次调用（空、空、产出）。
    assert e._client.calls == 1 + 3 * (60 - 1)
    assert sleeps, "应有退避等待发生"
    # 上限 = base(1.0) × cap(8) ；+0.5s 容忍时间推进误差
    assert max(sleeps) <= 8.0 + 0.5


@pytest.mark.asyncio
async def test_loop_instance_state_bounded(monkeypatch, pet_db):
    """循环实例与 engine 订阅容器无属性/条目累积（唯一可变状态是标量时刻）"""
    e = _engine(monkeypatch, {"max_loop_rounds": 40})
    e._client = _IdleHeavyClient()

    async def _fake_sleep(seconds):
        pass

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)  # 空拍等待不真实 sleep
    loop = CustomLoop()

    keys_before = set(loop.__dict__.keys())
    for _ in range(5):
        await loop.run(e, "probe")
    assert set(loop.__dict__.keys()) == keys_before
    for k, v in loop.__dict__.items():
        if k != "_last_think_start":
            assert not hasattr(v, "__len__") or len(v) == 0 or isinstance(v, (str, bytes)), k
    # engine 侧订阅容器不被循环触碰
    assert e._reply_listeners == []


def test_petstore_repeated_config_rw_bounded(pet_db):
    """PetStore 高频配置读写：连接即开即关，实例无新属性/连接对象累积"""
    attrs_before = set(pet_db.__dict__.keys())
    for i in range(200):
        pet_db.update_config({"min_think_interval": float(i % 10)})
        pet_db.get_config()
    assert set(pet_db.__dict__.keys()) == attrs_before
    # 无 sqlite 连接被挂在实例上（连接不 close 才会累积）
    import sqlite3
    for v in pet_db.__dict__.values():
        assert not isinstance(v, sqlite3.Connection)
    assert pet_db.get_config()["min_think_interval"] == 9.0  # 内容稳定
