"""CompressionEngine / ContextController 扩展测试"""
import pytest

from modules.thinking.context.compression import (
    CompressionEngine,
    get_compression_engine,
)
from modules.thinking.context.controller import (
    ContextController,
    get_context_controller,
)


@pytest.fixture
def engine():
    import modules.thinking.context.compression as mod
    mod._instance = None
    return get_compression_engine()


def test_estimate_tokens_positive(engine):
    assert engine.estimate_tokens("一段中英混合 mixed text 内容") > 0


# ── 单例 ──────────────────────────────────────────────────────────────

def test_get_compression_engine_singleton(monkeypatch):
    import modules.thinking.context.compression as mod
    monkeypatch.setattr(mod, "_instance", None)
    a = get_compression_engine()
    b = get_compression_engine()
    assert a is b


# ── ContextController ──────────────────────────────────────────────────

def test_controller_set_mode_valid_and_invalid():
    c = ContextController()
    c.set_mode("plan")
    assert c.mode == "plan"
    c.set_mode("bogus")  # 非法模式 → 忽略
    assert c.mode == "plan"


def test_controller_build_time_context():
    c = ContextController()
    out = c.build_time_context()
    assert "当前时间" in out
    out2 = c.build_time_context(user_name="张三", last_msg_time=120.0)
    assert "张三" in out2
    assert "分钟前" in out2


def test_controller_clear():
    c = ContextController()
    c._injected_hashes.add("abc")
    c.clear()
    assert c._injected_hashes == set()


def test_get_context_controller_singleton(monkeypatch):
    import modules.thinking.context.controller as mod
    monkeypatch.setattr(mod, "_instance", None)
    a = get_context_controller()
    b = get_context_controller()
    assert a is b
