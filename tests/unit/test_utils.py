"""utils 基础工具测试（此前 0% 覆盖）：时间/JSON/异步/异常安全调用"""
import asyncio
import logging
from datetime import datetime, timezone

import pytest

from utils import time_utils, json_utils, async_utils, exceptions


# ── time_utils ──────────────────────────────────────────────────────────────

def test_now_utc():
    dt = time_utils.now()
    assert dt.tzinfo == timezone.utc
    assert abs((datetime.now(timezone.utc) - dt).total_seconds()) < 5


def test_timestamp_roundtrip():
    ts = 1785000000.0
    dt = time_utils.timestamp_to_datetime(ts)
    assert time_utils.datetime_to_timestamp(dt) == pytest.approx(ts)
    assert dt.tzinfo == timezone.utc


def test_format_parse_datetime():
    dt = datetime(2026, 8, 11, 10, 30, 0)
    s = time_utils.format_datetime(dt)
    assert s == "2026-08-11 10:30:00"
    assert time_utils.parse_datetime(s) == dt


def test_time_range():
    start = datetime(2026, 8, 11, 10, 0, 0)
    end = datetime(2026, 8, 11, 10, 15, 0)
    r = time_utils.time_range(start, end, step_minutes=5)
    assert len(r) == 4
    assert r[0] == start
    assert r[-1] == end


def test_day_boundaries():
    dt = datetime(2026, 8, 11, 15, 30, 45)
    assert time_utils.get_start_of_day(dt) == datetime(2026, 8, 11, 0, 0, 0)
    assert time_utils.get_end_of_day(dt) == datetime(2026, 8, 11, 23, 59, 59, 999999)
    assert time_utils.get_start_of_day() == time_utils.get_start_of_day(time_utils.now())
    assert time_utils.get_end_of_day() == time_utils.get_end_of_day(time_utils.now())


# ── json_utils ──────────────────────────────────────────────────────────────

def test_serialize_datetime():
    dt = datetime(2026, 8, 11, 10, 30, 0)
    s = json_utils.serialize({"t": dt, "n": 1})
    assert '"2026-08-11T10:30:00"' in s
    assert json_utils.deserialize(s)["n"] == 1


def test_serialize_ensure_ascii_false():
    assert json_utils.serialize({"k": "中文"}) == '{"k": "中文"}'


def test_format_json():
    out = json_utils.format_json({"a": 1}, indent=4)
    assert '"a": 1' in out
    assert "\n" in out


def test_serialize_unsupported_type_raises():
    """不可序列化对象 → DateTimeEncoder.default 抛 TypeError（15 行）"""
    import pytest
    with pytest.raises(TypeError):
        json_utils.serialize({"obj": object()})


# ── async_utils ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_wrap():
    def sync_add(a, b):
        return a + b
    wrapped = async_utils.async_wrap(sync_add)
    assert await wrapped(1, 2) == 3


@pytest.mark.asyncio
async def test_gather_with_concurrency():
    async def task(i):
        return i * 2
    r = await async_utils.gather_with_concurrency(2, *(task(i) for i in range(5)))
    assert r == [0, 2, 4, 6, 8]


@pytest.mark.asyncio
async def test_run_with_timeout_success():
    async def ok():
        await asyncio.sleep(0.01)
        return "done"
    assert await async_utils.run_with_timeout(ok(), 5) == "done"


@pytest.mark.asyncio
async def test_run_with_timeout_expires():
    async def slow():
        await asyncio.sleep(10)
        return "x"
    with pytest.raises(TimeoutError):
        await async_utils.run_with_timeout(slow(), 0.05)


@pytest.mark.asyncio
async def test_async_task_group():
    g = async_utils.AsyncTaskGroup(max_concurrent=2)
    async def task(i):
        return i
    r = await g.run_all([task(i) for i in range(4)])
    assert r == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_async_task_group_concurrency_limited():
    g = async_utils.AsyncTaskGroup(max_concurrent=1)
    active = 0
    peak = 0

    async def task():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1

    await g.run_all([task() for _ in range(3)])
    assert peak == 1


# ── exceptions ──────────────────────────────────────────────────────────────

def test_error_hierarchy():
    assert issubclass(exceptions.ModelError, exceptions.BackendError)
    assert issubclass(exceptions.ToolError, exceptions.BackendError)
    assert issubclass(exceptions.SecurityError, exceptions.BackendError)
    assert issubclass(exceptions.MemoryError, exceptions.BackendError)
    assert issubclass(exceptions.CommunicationError, exceptions.BackendError)
    assert issubclass(exceptions.ConfigError, exceptions.BackendError)


def test_backend_error_context():
    cause = ValueError("root")
    err = exceptions.ModelError("失败", cause=cause, model="gpt-4")
    assert err.message == "失败"
    assert err.cause is cause
    assert err.context == {"model": "gpt-4"}


def test_safe_call_returns_fallback():
    def boom():
        raise RuntimeError("x")
    assert exceptions.safe_call(boom, fallback="fb") == "fb"
    assert exceptions.safe_call(lambda: 42) == 42


def test_safe_call_logs(caplog):
    with caplog.at_level(logging.WARNING):
        exceptions.safe_call(lambda: (_ for _ in ()).throw(ValueError("bad")), fallback=None, msg="ops")
    assert any("ops" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_safe_acall():
    async def boom():
        raise RuntimeError("x")
    assert await exceptions.safe_acall(boom(), fallback="fb") == "fb"
    async def ok():
        return "done"
    assert await exceptions.safe_acall(ok()) == "done"
