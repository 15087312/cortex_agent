"""memory_matcher 测试（此前 12% 覆盖）：语义/关键词/时间/重要性/综合评分"""
import math
import numpy as np
import pytest

pytestmark = pytest.mark.timeout(60)  # 工具链 import 较重
from datetime import datetime, timezone
from unittest.mock import MagicMock

from infra.tool_manager.tools.memory_matcher import MemoryMatchEngine


def _m():
    m = MemoryMatchEngine.__new__(MemoryMatchEngine)
    m.logger = MagicMock()
    m.SEMANTIC_WEIGHT = 0.4
    m.KEYWORD_WEIGHT = 0.2
    m.TIME_DECAY_WEIGHT = 0.2
    m.IMPORTANCE_WEIGHT = 0.2
    return m


def test_semantic_similarity_same():
    v = np.array([1.0, 0.0])
    assert _m()._semantic_similarity(v, v) == pytest_approx(1.0)


def test_semantic_similarity_orthogonal():
    assert _m()._semantic_similarity(np.array([1.0, 0.0]), np.array([0.0, 1.0])) == pytest_approx(0.5)


def test_semantic_similarity_none():
    assert _m()._semantic_similarity(None, np.array([1.0])) == 0.0


def test_semantic_similarity_zero_norm():
    assert _m()._semantic_similarity(np.array([0.0]), np.array([0.0])) == 0.0


def test_keyword_overlap():
    m = _m()
    r = m._keyword_overlap("hello world", "hello python")
    assert 0.0 < r <= 1.0


def test_keyword_overlap_empty():
    assert _m()._keyword_overlap("", "") == 0.0


def test_time_decay_empty():
    assert _m()._time_decay("") == 0.5


def test_time_decay_recent():
    now = datetime.now(timezone.utc).isoformat()
    assert _m()._time_decay(now) == pytest_approx(1.0, abs=0.05)


def test_time_decay_old():
    old = datetime.now(timezone.utc).replace(year=2000).isoformat()
    assert 0.0 <= _m()._time_decay(old) < 0.5


def test_time_decay_bad_format():
    assert _m()._time_decay("not-a-date") == 0.5


def test_importance_score():
    m = _m()
    assert m._importance_score(0.8) == 0.8
    assert m._importance_score(None) == 0.5
    assert m._importance_score(2.0) == 1.0
    assert m._importance_score("bad") == 0.5


def test_score_single_with_embedding():
    m = _m()
    qv = np.array([1.0, 0.0])
    memory = {"content": "测试记忆", "timestamp": datetime.now(timezone.utc).isoformat(),
              "importance": 0.8, "embedding": np.array([1.0, 0.0])}
    r = m.score_single("测试", memory, query_embedding=qv)
    assert "total_score" in r
    assert r["semantic"] == pytest_approx(1.0)
    assert r["importance"] == pytest_approx(0.8)


def test_score_batch(monkeypatch):
    m = _m()
    monkeypatch.setattr(m, "get_embedding", lambda t: np.array([1.0, 0.0]))
    monkeypatch.setattr(m, "_semantic_similarity", lambda a, b: 0.9)
    monkeypatch.setattr(m, "_keyword_overlap", lambda a, b: 0.5)
    monkeypatch.setattr(m, "_time_decay", lambda t: 0.7)
    monkeypatch.setattr(m, "_importance_score", lambda i: 0.6)
    items = [{"content": "A", "timestamp": "2026-08-01T00:00:00", "importance": 0.5},
             {"content": "B", "timestamp": "", "importance": 0.3}]
    scores = m.score_batch("查询", items)
    assert len(scores) == 2
    assert scores[0]["total_score"] > 0


def test_memory_match_bad_json(monkeypatch):
    """bad JSON 应返回 error（不触发引擎加载）"""
    from infra.tool_manager.tools import memory_matcher as mm
    fake = MagicMock()
    monkeypatch.setattr(mm, "_engine", fake)
    r = mm.memory_match("查询", "not-json")
    assert "JSON 解析失败" in r


def test_memory_match_empty(monkeypatch):
    from infra.tool_manager.tools import memory_matcher as mm
    fake = MagicMock()
    monkeypatch.setattr(mm, "_engine", fake)
    r = mm.memory_match("查询", "[]")
    assert "count" in r


def pytest_approx(x, abs=1e-6):
    import pytest
    return pytest.approx(x, abs=abs)
