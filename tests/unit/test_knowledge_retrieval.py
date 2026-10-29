"""知识检索单测 — 两路召回、RRF 融合、相邻合并、文档限流、注入格式"""
import pytest

from config.settings import settings
from modules.knowledge.ingestor import KnowledgeIngestor
from modules.knowledge.models import KnowledgeHit
from modules.knowledge.retrieval import (
    KnowledgeRetrieval, reciprocal_rank_fusion, should_retrieve,
)
from modules.knowledge.store import KnowledgeStore


def fake_embed(text: str, dim: int = 16):
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


AUTH_MD = """# 鉴权设计

## 1. 鉴权流程

双令牌：access 短期 + refresh 长期，refresh 落库持久化。

## 2. 令牌吊销

登出时把 refresh 令牌加入黑名单，access 令牌等待自然过期。
"""

CONFIG_MD = """# 配置说明

## TOKEN_TTL

令牌有效期，单位秒，默认 3600。修改后需要重启服务。
"""


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(
        db_path=str(tmp_path / "knowledge.db"),
        index_path=str(tmp_path / "knowledge.index"),
        id_map_path=str(tmp_path / "knowledge_id_map.json"),
        embed_fn=fake_embed,
    )
    yield s
    s.close()


@pytest.fixture
def retrieval(store, tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "auth.md").write_text(AUTH_MD, encoding="utf-8")
    (root / "config.md").write_text(CONFIG_MD, encoding="utf-8")
    KnowledgeIngestor(store=store, roots=str(root)).ingest_roots()
    return KnowledgeRetrieval(store=store)


def test_search_returns_hits_with_citation(retrieval):
    hits = retrieval.search("鉴权流程 双令牌", top_k=3)
    assert hits
    top = hits[0]
    assert top.title
    assert top.source_path.endswith("auth.md")
    assert "鉴权" in top.heading_path
    assert "双令牌" in top.text
    assert top.score > 0


def test_search_respects_path_prefix(retrieval):
    hits = retrieval.search("令牌", top_k=5)
    assert hits
    only_config = retrieval.search("令牌", top_k=5, path_prefix="docs/not-exists")
    assert only_config == []


def test_search_returns_empty_when_nothing_passes_gate(retrieval, monkeypatch):
    # 向量门槛拉到不可能通过，且查询词与库内词项无交集 → 不应返回任何结果
    monkeypatch.setattr(settings, "KNOWLEDGE_MIN_SEMANTIC", 1.01, raising=False)
    assert retrieval.search("zzz_不存在的词项", top_k=3) == []


def test_empty_query_and_disabled_kb_return_empty(retrieval, monkeypatch):
    assert retrieval.search("", top_k=3) == []
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", False, raising=False)
    assert retrieval.search("鉴权", top_k=3) == []


def test_per_doc_limit_applied(retrieval, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_MAX_CHUNKS_PER_DOC", 1, raising=False)
    hits = retrieval.search("令牌 refresh access", top_k=10)
    assert hits
    per_doc = {}
    for h in hits:
        per_doc[h.doc_id] = per_doc.get(h.doc_id, 0) + 1
    assert all(v <= 1 for v in per_doc.values())


def test_token_budget_trims_results(retrieval, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_MAX_INJECT_TOKENS", 1, raising=False)
    hits = retrieval.search("令牌 refresh access", top_k=5)
    # 预算再小也至少保留一条，避免查询必然空
    assert len(hits) == 1


def test_rrf_prefers_items_ranked_by_both_routes():
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "d"]])
    assert scores["b"] > scores["a"]
    assert scores["b"] > scores["d"]
    assert scores["c"] > 0


def test_adjacent_chunks_are_merged(retrieval, store):
    doc = next(d for d in store.list_documents() if d.source_path.endswith("auth.md"))
    chunks = store.list_chunks_by_doc(doc.id)
    assert len(chunks) >= 2
    meta = store.doc_meta_map([doc.id])[doc.id]
    hits = [
        KnowledgeHit(chunk_id=c.id, doc_id=doc.id, title=meta["title"],
                     heading_path=c.heading_path, text=c.text, score=0.02,
                     source_path=meta["source_path"], seq=c.seq,
                     char_start=c.char_start, char_end=c.char_end)
        for c in chunks[:2]
    ]
    merged = retrieval._merge_adjacent(hits)
    assert len(merged) == 1
    # 合并后前后文都在，且不重复拼接重叠部分
    assert len(merged[0].text) >= max(len(chunks[0].text), len(chunks[1].text))


def test_format_renders_reference_block(retrieval):
    hits = retrieval.search("鉴权流程", top_k=2)
    rendered = retrieval.format(hits)
    assert "【参考资料】" in rendered
    assert "auth.md" in rendered
    assert "§" in rendered
    assert retrieval.format([]) == ""


def test_should_retrieve_only_for_knowledge_intent(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INJECT", True, raising=False)
    assert should_retrieve("这个接口的参数是什么")
    assert should_retrieve("看下 docs/ARCHITECTURE.md 里怎么写的")
    assert not should_retrieve("今天午饭吃什么")
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INJECT", False, raising=False)
    assert not should_retrieve("这个接口的参数是什么")
