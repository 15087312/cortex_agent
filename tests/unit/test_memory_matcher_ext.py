"""memory_matcher 补充测试 — embedding 加载/语义/时间衰减/批量/工具函数 全路径覆盖"""
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from infra.tool_manager.tools import memory_matcher as mm
from infra.tool_manager.tools.memory_matcher import MemoryMatchEngine


def _engine():
    return MemoryMatchEngine()


def _close_vectors():
    return np.array([1.0, 0.0]), np.array([1.0, 0.0])


# ---------------------------------------------------------------------------
# _load_embedding_model / get_embedding
# ---------------------------------------------------------------------------

class TestEmbeddingModel:
    def test_import_failure_returns_false(self):
        e = _engine()
        assert e._load_embedding_model() is False
        assert e._model_load_attempted is True
        assert e._model_loaded is False

    def test_cached_after_attempt(self):
        e = _engine()
        assert e._load_embedding_model() is False
        assert e._load_embedding_model() is False

    def test_success_with_fake_module(self, monkeypatch):
        class _FakeST:
            def __init__(self, *a, **k):
                pass

            def encode(self, text, **k):
                return np.array([1.0, 0.0])

        monkeypatch.setitem(sys.modules, "sentence_transformers",
                            SimpleNamespace(SentenceTransformer=_FakeST))
        e = _engine()
        assert e._load_embedding_model() is True
        assert e._model_loaded is True
        emb = e.get_embedding("文本")
        assert emb is not None

    def test_type_error_local_only_true_raises(self, monkeypatch):
        class _RejectKw:
            def __init__(self, *a, **k):
                if "local_files_only" in k:
                    raise TypeError("bad kw")

        monkeypatch.setitem(sys.modules, "sentence_transformers",
                            SimpleNamespace(SentenceTransformer=_RejectKw))
        # CI 无 ~/.cortex settings，EMBEDDING_LOCAL_FILES_ONLY 可能为空 → 显式 mock 为 True
        from config.settings import settings
        monkeypatch.setattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", True)
        e = _engine()
        assert e._load_embedding_model() is False

    def test_type_error_local_only_false_retries(self, monkeypatch):
        class _RejectKw:
            def __init__(self, *a, **k):
                if "local_files_only" in k:
                    raise TypeError("bad kw")

            def encode(self, text, **k):
                return np.array([1.0, 0.0])

        from config.settings import settings
        monkeypatch.setattr(settings, "EMBEDDING_LOCAL_FILES_ONLY", False)
        monkeypatch.setitem(sys.modules, "sentence_transformers",
                            SimpleNamespace(SentenceTransformer=_RejectKw))
        e = _engine()
        assert e._load_embedding_model() is True

    def test_settings_import_failure_defaults(self, monkeypatch):
        class _FakeST:
            def __init__(self, *a, **k):
                pass

            def encode(self, text, **k):
                return np.array([1.0, 0.0])

        monkeypatch.setitem(sys.modules, "sentence_transformers",
                            SimpleNamespace(SentenceTransformer=_FakeST))
        monkeypatch.setitem(sys.modules, "config.settings", None)
        e = _engine()
        assert e._load_embedding_model() is True

    def test_get_embedding_encode_failure(self, monkeypatch):
        class _FakeST:
            def __init__(self, *a, **k):
                pass

            def encode(self, text, **k):
                raise RuntimeError("boom")

        monkeypatch.setitem(sys.modules, "sentence_transformers",
                            SimpleNamespace(SentenceTransformer=_FakeST))
        e = _engine()
        assert e.get_embedding("文本") is None

    def test_get_embedding_when_not_loaded(self):
        e = _engine()
        assert e.get_embedding("文本") is None


# ---------------------------------------------------------------------------
# 语义/时间/关键词 补充分支
# ---------------------------------------------------------------------------

class TestSemanticExt:
    def test_list_inputs_converted(self):
        e = _engine()
        r = e._semantic_similarity([1.0, 0.0], [1.0, 0.0])
        assert r == pytest.approx(1.0)

    def test_numpy_import_failure(self, monkeypatch):
        e = _engine()
        monkeypatch.setitem(sys.modules, "numpy", None)
        assert e._semantic_similarity(np.array([1.0]), np.array([1.0])) == 0.0


class TestTimeDecayExt:
    def test_future_clamped(self):
        e = _engine()
        assert e._time_decay("2999-01-01T00:00:00+00:00") == pytest.approx(1.0)

    def test_strptime_fallback(self):
        e = _engine()
        assert 0.0 < e._time_decay("2026-08-1 10:00:00") <= 1.0

    def test_half_life_zero_returns_mid(self):
        e = _engine()
        assert e._time_decay("2026-08-01T00:00:00", half_life_hours=0) == 0.5

    def test_naive_datetime(self):
        e = _engine()
        assert 0.0 <= e._time_decay("2026-08-01 10:00:00") <= 1.0


class TestKeywordExt:
    def test_jieba_failure_falls_back(self, monkeypatch):
        fake_jieba = SimpleNamespace(cut=MagicMock(side_effect=RuntimeError("no jieba")))
        monkeypatch.setitem(sys.modules, "jieba", fake_jieba)
        e = _engine()
        r = e._keyword_overlap("你好世界", "你好世界")
        assert 0.0 < r <= 1.0

    def test_all_punctuation_empty(self):
        e = _engine()
        assert e._keyword_overlap("!!!", "???") == 0.0


# ---------------------------------------------------------------------------
# score_single / score_batch 补充分支
# ---------------------------------------------------------------------------

class TestScoreSingleExt:
    def test_with_embedding_and_missing_memory_embedding(self, monkeypatch):
        e = _engine()
        monkeypatch.setattr(e, "get_embedding", lambda t: np.array([1.0, 0.0]))
        monkeypatch.setattr(e, "_semantic_similarity", lambda a, b: 0.7)
        monkeypatch.setattr(e, "_keyword_overlap", lambda a, b: 0.4)
        monkeypatch.setattr(e, "_time_decay", lambda t: 0.6)
        monkeypatch.setattr(e, "_importance_score", lambda i: 0.5)
        r = e.score_single("查询", {"content": "记忆", "timestamp": "", "importance": 0.5},
                           query_embedding=np.array([1.0, 0.0]))
        assert r["semantic"] == pytest.approx(0.7)

    def test_without_embedding_with_content(self, monkeypatch):
        e = _engine()
        monkeypatch.setattr(e, "get_embedding", lambda t: np.array([1.0, 0.0]))
        monkeypatch.setattr(e, "_semantic_similarity", lambda a, b: 0.9)
        monkeypatch.setattr(e, "_keyword_overlap", lambda a, b: 0.1)
        monkeypatch.setattr(e, "_time_decay", lambda t: 0.8)
        monkeypatch.setattr(e, "_importance_score", lambda i: 0.3)
        r = e.score_single("查询", {"content": "记忆", "timestamp": "", "importance": 0.5})
        assert r["semantic"] == pytest.approx(0.9)

    def test_without_embedding_no_content(self, monkeypatch):
        e = _engine()
        monkeypatch.setattr(e, "get_embedding", lambda t: np.array([1.0, 0.0]))
        monkeypatch.setattr(e, "_semantic_similarity", lambda a, b: 0.9)
        monkeypatch.setattr(e, "_keyword_overlap", lambda a, b: 0.1)
        monkeypatch.setattr(e, "_time_decay", lambda t: 0.8)
        monkeypatch.setattr(e, "_importance_score", lambda i: 0.3)
        r = e.score_single("查询", {"timestamp": "", "importance": 0.5})
        assert r["semantic"] == 0.0


class TestScoreBatchExt:
    def test_top_k_and_sort(self, monkeypatch):
        e = _engine()
        monkeypatch.setattr(e, "get_embedding", lambda t: np.array([1.0, 0.0]))
        monkeypatch.setattr(e, "_semantic_similarity", lambda a, b: 0.5)
        monkeypatch.setattr(e, "_keyword_overlap", lambda a, b: 0.5)
        monkeypatch.setattr(e, "_time_decay", lambda t: 0.5)
        monkeypatch.setattr(e, "_importance_score", lambda i: 0.5)
        items = [
            {"content": "A", "importance": 0.2},
            {"content": "B", "importance": 0.9},
            {"content": "C", "importance": 0.5},
        ]
        scores = e.score_batch("q", items, top_k=2)
        assert len(scores) == 2
        assert scores[0]["total_score"] >= scores[1]["total_score"]

    def test_score_single_error_skipped(self, monkeypatch):
        e = _engine()
        monkeypatch.setattr(e, "get_embedding", lambda t: np.array([1.0, 0.0]))
        orig = e.score_single
        calls = {"n": 0}

        def flaky(q, mem, emb=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return orig(q, mem, emb)

        monkeypatch.setattr(e, "score_single", flaky)
        monkeypatch.setattr(e, "_keyword_overlap", lambda a, b: 0.0)
        monkeypatch.setattr(e, "_time_decay", lambda t: 0.5)
        monkeypatch.setattr(e, "_importance_score", lambda i: 0.5)
        items = [{"content": "A"}, {"content": "B"}]
        scores = e.score_batch("q", items)
        assert len(scores) == 1


# ---------------------------------------------------------------------------
# 模块级工具函数
# ---------------------------------------------------------------------------

class TestMemoryMatchTool:
    def test_list_input(self):
        r = json.loads(mm.memory_match("查询", [{"content": "A", "importance": 0.5}]))
        assert "results" in r
        assert r["count"] == 1

    def test_success_json_string(self):
        r = json.loads(mm.memory_match("查询", json.dumps([{"content": "A", "importance": 0.5}])))
        assert r["count"] == 1
        assert r["query"] == "查询"

    def test_bad_json(self):
        r = mm.memory_match("查询", "not-json")
        assert "JSON 解析失败" in r

    def test_not_list(self):
        r = mm.memory_match("查询", '{"a": 1}')
        assert "必须是 JSON 数组" in r

    def test_invalid_top_k_defaults(self):
        r = json.loads(mm.memory_match("查询", json.dumps([{"content": "A"}]), top_k="abc"))
        assert r["count"] == 1

    def test_empty(self):
        r = json.loads(mm.memory_match("查询", "[]"))
        assert r["count"] == 0

    def test_exception_reported(self, monkeypatch):
        fake = MagicMock()
        fake.score_batch.side_effect = RuntimeError("boom")
        monkeypatch.setattr(mm, "_engine", fake)
        r = mm.memory_match("查询", json.dumps([{"content": "A"}]))
        assert "匹配失败" in r


class TestMemoryScoreTool:
    def test_success(self):
        r = json.loads(mm.memory_score("查询", "记忆内容", memory_timestamp="2026-08-01T00:00:00",
                                      memory_importance="0.7"))
        assert "total_score" in r
        assert r["memory_content_preview"] == "记忆内容"
        assert "memory" not in r
        assert r["query"] == "查询"

    def test_invalid_importance_defaults(self):
        r = json.loads(mm.memory_score("查询", "内容", memory_importance="bad"))
        assert r["importance"] == 0.5

    def test_long_content_preview(self):
        r = json.loads(mm.memory_score("查询", "x" * 200))
        assert r["memory_content_preview"] == "x" * 100

    def test_exception_reported(self, monkeypatch):
        fake = MagicMock()
        fake.score_single.side_effect = RuntimeError("boom")
        monkeypatch.setattr(mm, "_engine", fake)
        r = mm.memory_score("查询", "内容")
        assert "评分失败" in r


class TestMemoryBatchFilterTool:
    def _patch_engine(self, monkeypatch):
        monkeypatch.setattr(mm._engine, "get_embedding", lambda t: np.array([1.0, 0.0]))
        monkeypatch.setattr(mm._engine, "_semantic_similarity", lambda a, b: 0.8)
        monkeypatch.setattr(mm._engine, "_keyword_overlap", lambda a, b: 0.8)
        monkeypatch.setattr(mm._engine, "_time_decay", lambda t: 0.8)
        monkeypatch.setattr(mm._engine, "_importance_score", lambda i: float(i))

    def test_success(self, monkeypatch):
        self._patch_engine(monkeypatch)
        items = [
            {"content": "high", "importance": 1.0},
            {"content": "low", "importance": 0.0},
        ]
        r = json.loads(mm.memory_batch_filter("查询", json.dumps(items), threshold="0.7"))
        assert r["total"] == 2
        assert r["count"] == 1

    def test_list_input(self, monkeypatch):
        self._patch_engine(monkeypatch)
        r = json.loads(mm.memory_batch_filter("查询", [{"content": "A", "importance": 1.0}]))
        assert r["count"] == 1

    def test_not_list(self):
        r = mm.memory_batch_filter("查询", '{"a": 1}')
        assert "必须是 JSON 数组" in r

    def test_bad_json(self):
        r = mm.memory_batch_filter("查询", "not-json")
        assert "JSON 解析失败" in r

    def test_invalid_threshold_defaults(self):
        r = json.loads(mm.memory_batch_filter("查询", json.dumps([{"content": "A", "importance": 0.4}]),
                                              threshold="bad"))
        assert r["threshold"] == 0.3

    def test_threshold_clamped(self):
        r = json.loads(mm.memory_batch_filter("查询", json.dumps([{"content": "A", "importance": 0.4}]),
                                              threshold="5.0"))
        assert r["threshold"] == 1.0

    def test_empty(self):
        r = json.loads(mm.memory_batch_filter("查询", "[]"))
        assert r["count"] == 0
        assert r["threshold"] == 0.3

    def test_exception_reported(self, monkeypatch):
        fake = MagicMock()
        fake.score_batch.side_effect = RuntimeError("boom")
        monkeypatch.setattr(mm, "_engine", fake)
        r = mm.memory_batch_filter("查询", json.dumps([{"content": "A"}]))
        assert "过滤失败" in r
