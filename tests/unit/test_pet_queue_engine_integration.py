"""PetEngine × PetTaskQueue 集成测试 — 三种触发源统一串行消费

页面聊天（stream_chat）、语音事件（chat）、定时/主动思考（stream_chat('')）
全部经同一队列，同一时间只跑一个模型任务。
"""
import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest

import modules.desktop_pet.pet_engine as pe
import modules.desktop_pet.task_queue as tq
from modules.desktop_pet.pet_engine import PetEngine


@pytest.fixture(autouse=True)
def _fresh_queue():
    tq.PetTaskQueue._instance = None
    yield
    tq.PetTaskQueue._instance = None


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch):
    """隔离真实 pet.db：历史写入全部走内存 Mock（此前测试曾污染真实库）"""
    import modules.desktop_pet.store as store_mod
    fake_store = MagicMock()
    monkeypatch.setattr(store_mod.PetStore, "get_instance", classmethod(lambda cls: fake_store))
    yield


def _engine(monkeypatch):
    monkeypatch.setattr(pe.settings, "DESKTOP_PET_ENABLED", True)
    e = PetEngine.__new__(PetEngine)
    e._client = None
    e._client_cfg = None
    e._event_bus = None
    e._sub_id = ""
    e._running = False
    e._reply_listeners = []
    e.last_reply = None
    e._loop = None
    return e


def _patch_frontend(monkeypatch):
    import modules.thinking.frontend_channel as fc
    monkeypatch.setattr(fc, "confirm_frontend_connection", lambda *a, **k: True)
    monkeypatch.setattr(fc, "push_content", AsyncMock(return_value=None))


def _install_loop(monkeypatch, e, runs):
    """假 ReAct 循环：直接 patch loop 属性（避免读真实 pet.db 配置拉起真模型），
    每个 run 记录开始/结束并追踪并发峰值"""
    fake = MagicMock()

    async def fake_run(engine, text, on_token=None, on_reasoning=None, on_message=None, extra_system=""):
        runs["concurrent"] += 1
        runs["peak"] = max(runs["peak"], runs["concurrent"])
        runs["order"].append(f"start:{text or 'auto'}")
        try:
            await asyncio.sleep(0.03)
            reply = f"reply:{text or 'auto'}"
            if on_token:
                on_token(reply)
            if on_message:
                on_message(reply)
            return reply
        finally:
            runs["concurrent"] -= 1
            runs["order"].append(f"end:{text or 'auto'}")

    fake.run = fake_run
    # patch 类级 loop property：返回固定 fake，绕过 store 配置（测试环境无真实本地模型）
    monkeypatch.setattr(
        PetEngine, "loop", property(lambda self: fake)
    )
    return fake


def test_chat_goes_through_queue(monkeypatch):
    """chat() 经队列执行（worker 消费），结果正确落库一次"""
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    runs = {"concurrent": 0, "peak": 0, "order": []}
    _install_loop(monkeypatch, e, runs)
    monkeypatch.setattr(e, "save_pair", lambda *a: None)

    reply = asyncio.run(e.chat("你好"))
    assert reply == "reply:你好"
    assert runs["peak"] == 1
    assert e.task_queue.pending() == 0


def test_three_sources_serial_no_concurrency(monkeypatch):
    """页面聊天 + 语音 + 定时思考 同时提交 → 严格串行，FIFO 顺序"""
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    runs = {"concurrent": 0, "peak": 0, "order": []}
    _install_loop(monkeypatch, e, runs)
    monkeypatch.setattr(e, "save_pair", lambda *a: None)

    async def main():
        # 模拟三源同时触发（同一事件循环内并发提交）
        page = asyncio.create_task(e.stream_chat_collect("页面消息"))
        voice = asyncio.create_task(e.chat("语音消息"))
        auto = asyncio.create_task(e.stream_chat_collect(""))  # 定时主动思考（空文本）
        await asyncio.gather(page, voice, auto)

    async def stream_chat_collect(text):
        toks = []
        async for t in e.stream_chat(text):
            if t != "\x00ROUND_DONE\x00":
                toks.append(t)
        return toks

    e.stream_chat_collect = stream_chat_collect
    asyncio.run(main())

    assert runs["peak"] == 1, f"并发峰值 {runs['peak']} > 1：三源出现了并发执行！"
    # FIFO：页面消息先提交先执行；语音与主动思考按入队顺序紧随其后
    assert runs["order"][0] == "start:页面消息"
    starts = [s for s in runs["order"] if s.startswith("start:")]
    assert len(starts) == 3
    assert runs["order"].index("start:语音消息") > runs["order"].index("start:页面消息")


def test_stream_cancel_skips_to_next_task(monkeypatch):
    """SSE 断开（取消排队中的流任务）→ worker 跳过它继续执行下一个

    用未放行的 gate 占住 worker，保证被取消的任务确实还在排队中（未执行）。
    """
    _patch_frontend(monkeypatch)
    e = _engine(monkeypatch)
    runs = {"concurrent": 0, "peak": 0, "order": []}
    gate = asyncio.Event()  # 不 set：占住 worker 的长任务一直挂着

    fake = MagicMock()

    async def gated_run(engine, text, on_token=None, on_reasoning=None, on_message=None, extra_system=""):
        runs["concurrent"] += 1
        runs["peak"] = max(runs["peak"], runs["concurrent"])
        runs["order"].append(f"start:{text}")
        try:
            await gate.wait()  # 由测试显式放行
            reply = f"reply:{text}"
            if on_token:
                on_token(reply)
            if on_message:
                on_message(reply)
            return reply
        finally:
            runs["concurrent"] -= 1
            runs["order"].append(f"end:{text}")

    fake.run = gated_run
    monkeypatch.setattr(PetEngine, "loop", property(lambda self: fake))

    async def main():
        # 任务1：gate 阻塞的长任务占住 worker
        t1 = asyncio.create_task(e.chat("长任务"))
        while "start:长任务" not in runs["order"]:
            await asyncio.sleep(0.005)  # 等 worker 真正被长任务占住
        # 任务2：入队（排队中，未执行）后取消 —— 模拟 SSE 客户端断开
        gen = e.stream_chat("被取消的任务")
        consumer = asyncio.create_task(gen.__aiter__().__anext__())
        await asyncio.sleep(0.05)  # 等提交完成：任务2已在队列里排队
        consumer.cancel()  # 断开消费端 → CancelledError 传入生成器 → 取消队列任务
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
        # 任务3：排在被取消任务之后，应被 worker 正常消费
        t3 = asyncio.create_task(e.chat("后续任务"))
        await asyncio.sleep(0.05)  # 等任务3入队、worker 跳过任务2
        gate.set()  # 放行长任务
        await t1
        r3 = await t3
        assert r3 == "reply:后续任务"

    asyncio.run(main())
    assert runs["peak"] == 1
    assert "start:被取消的任务" not in runs["order"], "被取消的排队任务不应执行"
