"""知识库存储层单测 — 建表、片段、向量一致性、关键词路降级、级联删除"""
import pytest

from modules.knowledge.chunker import chunk_document, normalize_text
from modules.knowledge.models import KnowledgeDoc
from modules.knowledge.store import KnowledgeStore, tokenize_terms

DOC_MD = """# 鉴权设计

## 1. 鉴权流程

双令牌：access 短期 + refresh 长期，refresh 落库。

## 2. 配置项

TOKEN_TTL 控制令牌有效期，默认 3600 秒。
"""


def fake_embed(text: str, dim: int = 16):
    """确定性向量（词袋哈希），避免测试加载真实 embedding 模型。"""
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


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


def _add_doc(store, source_path="docs/auth.md", text=DOC_MD):
    body = normalize_text(text)
    doc = KnowledgeDoc(
        title="鉴权设计", source_path=source_path, content_hash="hash-1",
        mime="text/markdown", size_bytes=len(body),
    )
    doc_id = store.upsert_document(doc)
    drafts = chunk_document(body, source_path)
    chunks = store.replace_chunks(doc_id, drafts)
    return doc_id, chunks


def test_upsert_document_and_replace_chunks(store):
    doc_id, chunks = _add_doc(store)
    assert doc_id.startswith("kd_")
    assert store.count_documents() == 1
    assert store.count_chunks() == len(chunks)
    doc = store.get_document(doc_id)
    assert doc.chunk_count == len(chunks)
    assert doc.indexed_at
    # 按 source_path 再 upsert 不会新建文档（唯一约束 + 复用 id）
    again = store.upsert_document(KnowledgeDoc(
        title="鉴权设计 v2", source_path="docs/auth.md", content_hash="hash-2", version=2))
    assert again == doc_id
    assert store.count_documents() == 1
    assert store.get_document(doc_id).version == 2


def test_replace_chunks_is_full_replacement(store):
    doc_id, chunks = _add_doc(store)
    before = store.count_chunks()
    drafts = chunk_document(normalize_text("# 新标题\n\n只有一句话。"), "docs/auth.md")
    store.replace_chunks(doc_id, drafts)
    assert before != len(drafts)
    assert store.count_chunks() == len(drafts)
    assert store.get_document(doc_id).chunk_count == len(drafts)


def test_vector_add_and_remove_keep_index_consistent(store):
    _, chunks = _add_doc(store)
    ids = [c.id for c in chunks]
    added = store.add_vectors(ids, [fake_embed(c.text) for c in chunks])
    assert added == len(chunks)
    assert store._index.ntotal == len(chunks)
    assert len(store._id_map) == len(chunks)

    hits = store.search_vector(fake_embed("鉴权流程"), top_k=3)
    assert hits and hits[0][0] in ids

    removed = store.remove_vectors([ids[0]])
    assert removed == 1
    assert store._index.ntotal == len(chunks) - 1
    assert len(store._id_map) == len(chunks) - 1


def test_vector_index_survives_new_instance(store, tmp_path):
    _, chunks = _add_doc(store)
    store.add_vectors([c.id for c in chunks], [fake_embed(c.text) for c in chunks])
    store.close()

    other = KnowledgeStore(
        db_path=str(tmp_path / "knowledge.db"),
        index_path=str(tmp_path / "knowledge.index"),
        id_map_path=str(tmp_path / "knowledge_id_map.json"),
        embed_fn=fake_embed,
    )
    hits = other.search_vector(fake_embed("配置项"), top_k=3)
    assert hits
    assert other._index.ntotal == len(chunks)
    other.close()


def test_keyword_search_hits_indexed_terms(store):
    _add_doc(store)
    hits = store.search_keyword("双令牌", top_k=5)
    assert hits
    top = store.get_chunk(hits[0][0])
    assert "双令牌" in top.text


def test_keyword_search_degrades_without_fts(store):
    _add_doc(store)
    store._fts = False  # 模拟 FTS5 不可用
    hits = store.search_keyword("双令牌", top_k=5)
    assert hits
    assert store.get_chunk(hits[0][0]) is not None


def test_delete_document_removes_chunks_and_vectors(store):
    doc_id, chunks = _add_doc(store)
    store.add_vectors([c.id for c in chunks], [fake_embed(c.text) for c in chunks])
    result = store.delete_document(doc_id)
    assert result["deleted"] is True
    assert result["chunks_removed"] == len(chunks)
    assert store.count_documents() == 0
    assert store.count_chunks() == 0
    assert store._index.ntotal == 0
    assert store.delete_document(doc_id)["deleted"] is False


def test_stats_reports_counts_and_dim(store):
    _, chunks = _add_doc(store)
    store.add_vectors([c.id for c in chunks], [fake_embed(c.text) for c in chunks])
    stats = store.stats()
    assert stats["documents"] == 1
    assert stats["chunks"] == len(chunks)
    assert stats["index_vectors"] == len(chunks)
    assert stats["dim"] == 16


def test_tokenize_terms_dedupes_and_drops_punctuation():
    terms = tokenize_terms("鉴权流程，鉴权流程！access_token")
    assert terms.count("鉴权") <= 1
    assert any("access" in t for t in terms)
    assert all(t.strip() for t in terms)
