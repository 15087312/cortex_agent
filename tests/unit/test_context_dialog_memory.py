"""上下文管理口径（设置页「上下文管理」）单元测试

覆盖 CONTEXT_LIMIT_MODE 三种口径：窗口比例 / 消息条数 / Token 数，
以及 DB 读取上限随配置变化（不再硬编码 100 条）。
"""
import sys
import types
from unittest.mock import MagicMock

import pytest

from modules.thinking.context import dialog_memory


def _cfg(**kwargs):
    base = {
        "CONTEXT_LIMIT_MODE": "ratio",
        "CONTEXT_COMPRESS_RATIO": 0.8,
        "CHAT_CONTEXT_MAX_MESSAGES": 20,
        "CHAT_CONTEXT_MAX_TOKENS": 32000,
        "CONTEXT_WINDOW_SIZE": 1000,
    }
    base.update(kwargs)
    return types.SimpleNamespace(**base)


@pytest.fixture
def settings_stub(monkeypatch):
    cfg_mod = sys.modules["config.settings"]

    def _apply(**kwargs):
        monkeypatch.setattr(cfg_mod, "settings", _cfg(**kwargs))

    return _apply


@pytest.fixture
def engine(monkeypatch):
    """每条消息固定 100 token"""
    eng = MagicMock()
    eng.estimate_tokens = MagicMock(return_value=100)
    monkeypatch.setattr(
        "modules.thinking.context.compression.get_compression_engine", lambda: eng
    )
    return eng


def _msgs(n):
    return [{"role": "user", "content": f"m{i}"} for i in range(n)]


# ── context_config / history_read_limit ───────────────────────────────

def test_context_config_reads_settings(settings_stub):
    settings_stub(CONTEXT_LIMIT_MODE="tokens", CHAT_CONTEXT_MAX_TOKENS=500)
    cfg = dialog_memory.context_config()
    assert cfg["mode"] == "tokens"
    assert cfg["max_tokens"] == 500


def test_context_config_falls_back_on_bad_mode(settings_stub):
    settings_stub(CONTEXT_LIMIT_MODE="unknown")
    assert dialog_memory.context_config()["mode"] == "ratio"


def test_context_config_falls_back_on_error(monkeypatch):
    cfg_mod = sys.modules["config.settings"]
    monkeypatch.setattr(cfg_mod, "settings", None)  # getattr 取不到 → 回落默认
    cfg = dialog_memory.context_config()
    assert cfg == {"mode": "ratio", "max_messages": 0, "max_tokens": 0, "ratio": 0.8}


def test_history_read_limit_messages_mode(settings_stub):
    settings_stub(CONTEXT_LIMIT_MODE="messages", CHAT_CONTEXT_MAX_MESSAGES=7)
    assert dialog_memory.history_read_limit() == 7


def test_history_read_limit_messages_unlimited(settings_stub):
    settings_stub(CONTEXT_LIMIT_MODE="messages", CHAT_CONTEXT_MAX_MESSAGES=0)
    assert dialog_memory.history_read_limit() == dialog_memory.MAX_HISTORY_SCAN


def test_history_read_limit_token_and_ratio_modes(settings_stub):
    settings_stub(CONTEXT_LIMIT_MODE="tokens")
    assert dialog_memory.history_read_limit() == dialog_memory.MAX_HISTORY_SCAN
    settings_stub(CONTEXT_LIMIT_MODE="ratio")
    assert dialog_memory.history_read_limit() == dialog_memory.MAX_HISTORY_SCAN


def test_load_dialog_from_db_uses_config_limit(monkeypatch, settings_stub):
    settings_stub(CONTEXT_LIMIT_MODE="messages", CHAT_CONTEXT_MAX_MESSAGES=5)
    repo = MagicMock()
    repo.get_messages = MagicMock(return_value=[])
    dialog_memory.load_dialog_from_db("s1", repo=repo)
    assert repo.get_messages.call_args.kwargs["limit"] == 5


# ── budget_trim 三种口径 ──────────────────────────────────────────────

def test_budget_trim_messages_mode(settings_stub, engine):
    settings_stub(CONTEXT_LIMIT_MODE="messages", CHAT_CONTEXT_MAX_MESSAGES=3)
    kept = dialog_memory.budget_trim(_msgs(10))
    assert [m["content"] for m in kept] == ["m7", "m8", "m9"]


def test_budget_trim_messages_zero_means_unlimited(settings_stub, engine):
    settings_stub(CONTEXT_LIMIT_MODE="messages", CHAT_CONTEXT_MAX_MESSAGES=0)
    assert len(dialog_memory.budget_trim(_msgs(30))) == 30


def test_budget_trim_tokens_mode(settings_stub, engine):
    # 每条 100 token，上限 250 → 只保留最新 2 条
    settings_stub(CONTEXT_LIMIT_MODE="tokens", CHAT_CONTEXT_MAX_TOKENS=250)
    kept = dialog_memory.budget_trim(_msgs(10))
    assert [m["content"] for m in kept] == ["m8", "m9"]


def test_budget_trim_tokens_keeps_latest_even_when_over(settings_stub, monkeypatch):
    engine = MagicMock()
    engine.estimate_tokens = MagicMock(return_value=999999)
    monkeypatch.setattr(
        "modules.thinking.context.compression.get_compression_engine", lambda: engine
    )
    settings_stub(CONTEXT_LIMIT_MODE="tokens", CHAT_CONTEXT_MAX_TOKENS=10)
    kept = dialog_memory.budget_trim(_msgs(5))
    assert [m["content"] for m in kept] == ["m4"]


def test_budget_trim_tokens_zero_means_unlimited(settings_stub, engine):
    settings_stub(CONTEXT_LIMIT_MODE="tokens", CHAT_CONTEXT_MAX_TOKENS=0)
    assert len(dialog_memory.budget_trim(_msgs(50))) == 50


def test_budget_trim_ratio_mode_respects_configured_ratio(settings_stub, engine):
    # 窗口 1000 × 0.3 = 300 → 保留 3 条
    settings_stub(CONTEXT_LIMIT_MODE="ratio", CONTEXT_COMPRESS_RATIO=0.3)
    kept = dialog_memory.budget_trim(_msgs(10))
    assert [m["content"] for m in kept] == ["m7", "m8", "m9"]


def test_budget_trim_ratio_mode_indexed_by_1000(settings_stub, engine):
    # 窗口按 1000 token 索引，预算 800 → 保留 8 条
    settings_stub(CONTEXT_LIMIT_MODE="ratio", CONTEXT_COMPRESS_RATIO=0.8)
    kept = dialog_memory.budget_trim(_msgs(10))
    assert [m["content"] for m in kept] == [f"m{i}" for i in range(2, 10)]


def test_budget_trim_ratio_zero_window_means_unlimited(settings_stub, engine):
    settings_stub(CONTEXT_LIMIT_MODE="ratio", CONTEXT_WINDOW_SIZE=0)
    assert len(dialog_memory.budget_trim(_msgs(30))) == 30


def test_budget_trim_explicit_overrides(settings_stub, engine):
    """显式参数优先于 settings（测试/特殊调用场景）"""
    settings_stub(CONTEXT_LIMIT_MODE="ratio", CONTEXT_COMPRESS_RATIO=0.8)
    kept = dialog_memory.budget_trim(_msgs(10), mode="messages", max_messages=2)
    assert [m["content"] for m in kept] == ["m8", "m9"]


def test_budget_trim_empty():
    assert dialog_memory.budget_trim([]) == []
