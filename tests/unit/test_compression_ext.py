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
from modules.thinking.context.types import CompressionLevel, EventRecord, EventType


@pytest.fixture
def engine():
    import modules.thinking.context.compression as mod
    mod._instance = None
    return get_compression_engine()


# ── compress ───────────────────────────────────────────────────────────

def test_compress_empty(engine):
    assert engine.compress("", max_tokens=100) == ""


def test_compress_level_none_ignored(engine):
    text = "  a  \n\n\n  b  \n\n  c  "
    out = engine.compress(text, max_tokens=1000, level=CompressionLevel.NONE)
    assert out == "a\nb\nc"


# ── 混合/纯中文截断分支 ───────────────────────────────────────────────

def test_truncate_mixed_language(engine):
    text = ("中文内容测试" * 200) + ("english words " * 300)
    out = engine._truncate_to_tokens(text, max_tokens=50)
    assert len(out) < len(text)
    assert "已截断" in out


def test_truncate_pure_chinese(engine):
    text = "中文长文本" * 500
    out = engine._truncate_to_tokens(text, max_tokens=20)
    assert len(out) < len(text)
    assert "已截断" in out


def test_truncate_pure_english(engine):
    text = "word " * 2000
    out = engine._truncate_to_tokens(text, max_tokens=20)
    assert len(out) < len(text)


def test_truncate_within_chars_limit(engine):
    text = "short"
    assert engine._truncate_to_tokens(text, max_tokens=1) == text  # 估算 token ≤ 上限


# ── summarize_events ───────────────────────────────────────────────────

def _event(content, role="user", ts=1.0, etype=EventType.MODEL_OUTPUT):
    return EventRecord(
        timestamp=ts, content=content, source_role=role, event_type=etype,
    )


def test_summarize_events_empty(engine):
    assert engine.summarize_events([]) == "无事件"


def test_summarize_events_groups_by_type(engine):
    events = [
        _event("第一个", role="user", ts=3.0, etype=EventType.MODEL_OUTPUT),
        _event("第二个", role="assistant", ts=2.0, etype=EventType.TOOL_CALL),
        _event("第三个", role="user", ts=1.0, etype=EventType.MODEL_OUTPUT),
    ]
    out = engine.summarize_events(events, max_summary_tokens=5000)
    assert "[model_output] (2 条)" in out
    assert "[tool_call] (1 条)" in out
    assert "第一个" in out


def test_summarize_events_with_none_content(engine):
    events = [_event(None, ts=1.0)]
    out = engine.summarize_events(events, max_summary_tokens=5000)
    assert "无内容" in out


# ── is_redundant ───────────────────────────────────────────────────────

def test_is_redundant_basic(engine):
    assert engine.is_redundant("", ["x"]) is False
    assert engine.is_redundant("x", []) is False
    assert engine.is_redundant("内容完全相同", ["内容完全相同"]) is True
    assert engine.is_redundant("完全不同", ["另一个完全不同的话题"]) is False


# ── detect_incremental_update ──────────────────────────────────────────

def test_detect_incremental_update(engine):
    assert engine.detect_incremental_update("相同", "相同") is None
    assert engine.detect_incremental_update("", "新内容") is not None
    out = engine.detect_incremental_update("第一行\n旧行", "第一行\n旧行\n新增行")
    assert "新增行" in out
    assert engine.detect_incremental_update("旧", "旧") is None


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
