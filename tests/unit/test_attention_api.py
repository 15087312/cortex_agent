"""attention/api 测试（此前 47% 覆盖）：注意力分析端点"""
import asyncio
from unittest.mock import MagicMock

from modules.attention import api as api_mod
from modules.attention.analyzer import AttentionResult, AttentionVector


def test_analyze_attention_success(monkeypatch):
    fake = MagicMock()
    fake.analyze.return_value = AttentionResult(
        importance_score=0.8, attention_level=0.8, vector=AttentionVector()
    )
    monkeypatch.setattr(api_mod, "_get_analyzer", lambda: fake)
    out = asyncio.run(api_mod.analyze_attention(user_input="测试", context=None, short_term_memory=None))
    assert out["success"] is True
    assert out["data"]["importance_score"] == 0.8


def test_analyze_attention_failure(monkeypatch):
    monkeypatch.setattr(api_mod, "_get_analyzer", lambda: (_ for _ in ()).throw(RuntimeError()))
    out = asyncio.run(api_mod.analyze_attention(user_input="x", context=None, short_term_memory=None))
    assert out["success"] is False


def test_get_status(monkeypatch):
    monkeypatch.setattr(api_mod, "_get_analyzer", lambda: MagicMock())
    out = asyncio.run(api_mod.get_status())
    assert out["success"] is True
    assert out["data"]["status"] == "healthy"
