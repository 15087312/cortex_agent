"""modules/management/core/error_bus 补充测试：publish/subscribe/失败降级/钩子"""
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import modules.management.core.error_bus as eb
from modules.management.core.error_bus import (
    ErrorContext,
    ErrorReport,
    GlobalErrorBus,
    _setup_global_logger,
    get_error_bus,
)


@pytest.fixture(autouse=True)
def _restore_global_hooks():
    """每个用例结束恢复 sys/threading 异常钩子，避免污染其它测试"""
    orig_excepthook = sys.excepthook
    orig_thread_hook = threading.excepthook
    yield
    sys.excepthook = orig_excepthook
    threading.excepthook = orig_thread_hook


# ── logger 初始化 / WS 回调 ─────────────────────────────────────────────────

def test_setup_global_logger_cached():
    logger = _setup_global_logger()
    assert _setup_global_logger() is logger


def test_set_ws_callback_and_push_success():
    bus = GlobalErrorBus()
    cb = MagicMock()
    bus.set_ws_callback(cb)
    bus._push_to_ws("ValueError", "boom", {"k": 1})
    cb.assert_called_once_with("ValueError", "boom", {"k": 1})


def test_push_to_ws_without_callback():
    bus = GlobalErrorBus()
    bus._push_to_ws("ValueError", "boom", {})  # _ws_callback 为 None → no-op


def test_push_to_ws_callback_raises():
    """WS 推送失败 → 降级记录日志，不向上抛"""
    bus = GlobalErrorBus()

    def bad(*args):
        raise RuntimeError("ws down")

    bus.set_ws_callback(bad)
    bus._push_to_ws("E", "m", {})


# ── asyncio handler ─────────────────────────────────────────────────────────

def test_asyncio_handler_setup_and_restore():
    bus = GlobalErrorBus()
    loop = MagicMock()
    bus.setup_asyncio_handler(loop)
    loop.get_exception_handler.assert_called_once()
    loop.set_exception_handler.assert_called_once()
    bus.restore_asyncio_handler(loop)
    loop.set_exception_handler.assert_called_with(bus._original_loop_exception_handler)


def test_restore_asyncio_handler_no_original():
    bus = GlobalErrorBus()
    bus._original_loop_exception_handler = None
    bus.restore_asyncio_handler(MagicMock())  # 无原始 handler → no-op


# ── report_error / 结构化报告 ───────────────────────────────────────────────

def test_report_error_without_context():
    bus = GlobalErrorBus()
    cb = MagicMock()
    bus.set_ws_callback(cb)
    bus.report_error(ValueError("bad"))
    assert cb.call_count == 1
    etype, msg, ctx = cb.call_args[0]
    assert etype == "ValueError"
    assert msg == "bad"
    assert ctx == {"module": "", "function": ""}


def test_report_error_with_context():
    bus = GlobalErrorBus()
    cb = MagicMock()
    bus.set_ws_callback(cb)
    ctx = ErrorContext(module="m", function="f", extra={"x": 1})
    bus.report_error(RuntimeError("boom"), ctx)
    _, _, payload = cb.call_args[0]
    assert payload["module"] == "m"
    assert payload["function"] == "f"
    assert payload["x"] == 1


def test_build_report():
    bus = GlobalErrorBus()
    r = bus._build_report(ValueError("bad"), ErrorContext(module="m", function="f", extra={"k": 1}))
    assert isinstance(r, ErrorReport)
    assert r.module == "m"
    assert r.function == "f"
    assert r.context == {"k": 1}
    assert r.error_type == "ValueError"


def test_format_error_with_and_without_context():
    bus = GlobalErrorBus()
    s = bus._format_error(ValueError("bad"), ErrorContext(module="m", function="f", extra={"k": 1}))
    assert "错误类型: ValueError" in s
    assert "模块: m" in s
    assert "上下文: {'k': 1}" in s
    s2 = bus._format_error(ValueError("bad"))
    assert "错误类型: ValueError" in s2
    # 有 context 但无 extra → 不追加上下文行
    s3 = bus._format_error(ValueError("bad"), ErrorContext(module="m", function="f"))
    assert "上下文:" not in s3
    assert "模块: m" in s3


# ── 系统级钩子 ──────────────────────────────────────────────────────────────

def test_handle_uncaught_exception_keyboard_interrupt():
    bus = GlobalErrorBus()
    # KeyboardInterrupt → 走原始 sys.__excepthook__（仅打印，不终止进程）
    bus._handle_uncaught_exception(KeyboardInterrupt, KeyboardInterrupt(), None)


def test_handle_uncaught_exception_normal():
    bus = GlobalErrorBus()
    bus._handle_uncaught_exception(ValueError, ValueError("boom"), None)


def test_handle_thread_exception():
    bus = GlobalErrorBus()
    args = SimpleNamespace(exc_value=ValueError("thread boom"), thread=threading.main_thread())
    bus._handle_thread_exception(args)


class _FakeTask:
    def get_name(self):
        return "task-x"


def test_handle_asyncio_exception_with_exception():
    bus = GlobalErrorBus()
    bus._handle_asyncio_exception(
        MagicMock(), {"exception": ValueError("async boom"), "task": _FakeTask(), "message": "m"}
    )


def test_handle_asyncio_exception_without_task():
    bus = GlobalErrorBus()
    bus._handle_asyncio_exception(MagicMock(), {"exception": ValueError("x")})


def test_handle_asyncio_exception_without_exception():
    bus = GlobalErrorBus()
    bus._handle_asyncio_exception(MagicMock(), {"message": "no exception object"})


# ── get_error_bus 单例（含双检锁分支）────────────────────────────────────────

def test_get_error_bus_existing_singleton():
    assert get_error_bus() is eb.error_bus


def test_get_error_bus_creates():
    saved = eb._error_bus
    eb._error_bus = None
    try:
        bus = get_error_bus()
        assert eb._error_bus is bus
    finally:
        eb._error_bus = saved


def test_get_error_bus_inner_recheck():
    """并发场景：外层判 None 通过后、加锁期间已被其它线程创建 → 复用已有实例"""
    saved = eb._error_bus
    saved_lock = eb._error_bus_lock
    try:
        eb._error_bus = None
        entered = threading.Event()
        release = threading.Event()

        class BlockingLock:
            def __enter__(self):
                entered.set()
                assert release.wait(5), "release timeout"
                return self

            def __exit__(self, *exc):
                return False

        eb._error_bus_lock = BlockingLock()
        result = {}

        def worker():
            result["bus"] = eb.get_error_bus()

        t = threading.Thread(target=worker)
        t.start()
        assert entered.wait(5), "worker did not enter lock"
        eb._error_bus = object()  # 模拟另一线程已先完成创建
        release.set()
        t.join(5)
        assert not t.is_alive()
        assert result["bus"] is eb._error_bus
    finally:
        eb._error_bus = saved
        eb._error_bus_lock = saved_lock
