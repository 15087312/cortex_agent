"""知识摄取单测 — 多根目录白名单、哈希增量、向量不泄漏、全量重建"""
import pytest

from modules.knowledge.ingestor import (
    KnowledgeIngestor, is_path_allowed, parse_roots, resolve_knowledge_roots,
)
from modules.knowledge.store import KnowledgeStore


def fake_embed(text: str, dim: int = 16):
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


@pytest.fixture
def docs_dir(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "auth.md").write_text(
        "# 鉴权设计\n\n## 1. 鉴权流程\n\n双令牌：access 短期 + refresh 长期。\n", encoding="utf-8")
    (root / "config.md").write_text(
        "# 配置说明\n\n## TOKEN_TTL\n\n令牌有效期，默认 3600 秒。\n", encoding="utf-8")
    return root


def test_multi_root_whitelist_parsing_and_matching(tmp_path):
    docs = tmp_path / "docs"
    specs = tmp_path / "specs"
    docs.mkdir()
    specs.mkdir()
    (docs / "a.md").write_text("# a", encoding="utf-8")
    (specs / "b.txt").write_text("b", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "c.md").write_text("# c", encoding="utf-8")

    spec = f"{docs},{specs}"
    roots = resolve_knowledge_roots(spec)
    assert len(roots) == 2
    assert is_path_allowed(docs / "a.md", roots)
    assert is_path_allowed(specs / "b.txt", roots)
    assert not is_path_allowed(outside / "c.md", roots)
    # 单个文件也可作为精确授权条目
    file_roots = parse_roots(str(docs / "a.md"))
    assert is_path_allowed(docs / "a.md", file_roots)
    assert not is_path_allowed(docs / "other.md", file_roots)


def test_ingest_rejects_path_outside_whitelist(store, docs_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_text("# secret", encoding="utf-8")
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    result = ing.ingest_path(outside / "secret.md")
    assert result.errors
    assert "白名单" in result.errors[0]["error"]
    assert store.count_documents() == 0


def test_ingest_roots_adds_documents_and_vectors(store, docs_dir):
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    result = ing.ingest_roots()
    assert result.added_count == 2
    assert result.errors == []
    stats = store.stats()
    assert stats["documents"] == 2
    assert stats["index_vectors"] == stats["chunks"]
    assert stats["chunks"] > 0


def test_reingest_unchanged_is_skipped(store, docs_dir):
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    ing.ingest_roots()
    vectors_before = store._index.ntotal
    second = ing.ingest_roots()
    assert second.added_count == 0
    assert second.updated_count == 0
    assert second.unchanged_count == 2
    assert store._index.ntotal == vectors_before


def test_modified_file_bumps_version_without_vector_leak(store, docs_dir):
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    ing.ingest_roots()
    target = docs_dir / "auth.md"
    before_chunks = store.count_chunks()
    vectors_before = store._index.ntotal

    target.write_text(
        "# 鉴权设计\n\n## 1. 鉴权流程\n\n双令牌：access 短期 + refresh 长期，refresh 落库。\n"
        "\n## 2. 新增章节\n\n会话吊销与黑名单。\n",
        encoding="utf-8",
    )
    result = ing.ingest_roots()
    assert result.updated_count == 1
    doc = store.get_document_by_path(str(target.resolve()))
    assert doc.version == 2
    # 增量更新后向量数必须与片段数一致（旧向量已删除，无泄漏）
    assert store._index.ntotal == store.count_chunks()
    assert store.count_chunks() != before_chunks or vectors_before != store._index.ntotal


def test_delete_removes_document_and_vectors(store, docs_dir):
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    ing.ingest_roots()
    doc = store.get_document_by_path(str((docs_dir / "auth.md").resolve()))
    out = ing.delete(doc.id)
    assert out["deleted"] is True
    assert store.count_documents() == 1
    assert store._index.ntotal == store.count_chunks()


def test_full_reindex_rebuilds_vectors(store, docs_dir):
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    ing.ingest_roots()
    chunks = store.count_chunks()
    out = ing.reindex(full=True)
    assert out["mode"] == "full"
    assert out["documents"] == 2
    assert store._index.ntotal == chunks


def test_binary_and_unsupported_files_are_skipped(store, docs_dir):
    (docs_dir / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (docs_dir / "blob.md").write_bytes(b"\x00\x01\x02")
    ing = KnowledgeIngestor(store=store, roots=str(docs_dir))
    # 目录扫描不收录不支持的类型；显式指定时才走「不支持」分支
    result = ing.ingest_roots()
    assert result.added_count == 2
    assert "二进制" in " ".join(s["reason"] for s in result.skipped)

    direct = ing.ingest_path(docs_dir / "logo.png")
    assert "不支持" in direct.skipped[0]["reason"]
    assert store.count_documents() == 2


def test_empty_root_spec_reports_error(store, tmp_path):
    ing = KnowledgeIngestor(store=store, roots=str(tmp_path / "not-exists"))
    result = ing.ingest_roots()
    assert result.errors
    assert "KNOWLEDGE_ROOTS" in result.errors[0]["error"]
