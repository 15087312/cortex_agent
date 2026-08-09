"""attention/analyzer 测试：注意力分析"""
from unittest.mock import patch

from modules.attention.analyzer import AttentionAnalyzer, AttentionResult, AttentionVector


def _analyzer(enabled=True, static=None):
    cfg = type("Cfg", (), {"importance_enabled": enabled, "force_static_level": static})()
    a = AttentionAnalyzer.__new__(AttentionAnalyzer)
    a._cfg = cfg
    return a


def test_attention_vector_to_dict():
    v = AttentionVector()
    d = v.to_dict()
    assert set(d) == {"semantic", "temporal", "task", "emotion", "modality", "source", "confidence"}


def test_attention_result_summary_text():
    r = AttentionResult(importance_score=0.8, attention_level=0.8, vector=AttentionVector())
    assert "任务重要性: 0.80/1.0" in r.summary_text
    assert "多维度注意力状态" in r.summary_text


def test_attention_result_importance_context():
    r = AttentionResult(importance_score=0.7)
    assert "0.70/1.0" in r.importance_context


def test_score_importance_disabled():
    a = _analyzer(enabled=False)
    score, reasons = a._score_importance("很重要")
    assert score == 0.5
    assert "关闭" in reasons[0]


def test_score_importance_empty():
    a = _analyzer()
    score, reasons = a._score_importance("   ")
    assert score == 0.0
    assert "为空" in reasons[0]


def test_score_importance_keywords():
    a = _analyzer()
    score, reasons = a._score_importance("紧急处理")
    assert score > 0.5
    assert any("紧急" in r for r in reasons)


def test_compute_attention_level_forced():
    a = _analyzer(static=0.9)
    assert a._compute_attention_level(0.2) == 0.9


def test_compute_attention_level_dynamic():
    a = _analyzer()
    assert a._compute_attention_level(0.75) == 0.75


def test_analyze_full():
    a = _analyzer()
    result = a.analyze("立刻处理这个任务")
    assert isinstance(result, AttentionResult)
    assert result.vector is not None
    assert result.attention_level > 0


def test_load_config():
    with patch("config.settings.settings") as s:
        s.ATTENTION_IMPORTANCE_ENABLED = True
        s.ATTENTION_FORCE_STATIC_LEVEL = None
        cfg = AttentionAnalyzer._load_config()
        assert cfg.importance_enabled is True
