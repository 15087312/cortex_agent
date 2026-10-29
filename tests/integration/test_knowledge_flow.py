"""知识库端到端：摄取 → 检索 → 注入文本 → 改文件 → 命中新版本

全部使用 tmp_path 独立库 + 确定性假向量，不加载真实 embedding 模型。
"""
import sqlite3

import pytest

from config.settings import settings
from modules.knowledge.ingestor import KnowledgeIngestor
from modules.knowledge.retrieval import KnowledgeRetrieval, should_retrieve
from modules.knowledge.service import KnowledgeService
from modules.knowledge.store import KnowledgeStore

V1 = """# 项目架构说明

## 1. 鉴权设计

双令牌：access 短期有效，refresh 长期有效并落库。

## 2. 部署

服务通过 uvicorn 启动，默认端口 8080。
"""

V2 = """# 项目架构说明

## 1. 鉴权设计

双令牌：access 短期有效，refresh 长期有效并落库。
新增：refresh 令牌轮换，旧令牌立即失效。

## 2. 部署

服务通过 uvicorn 启动，默认端口 9090（已改）。
"""


def fake_embed(text: str, dim: int = 16):
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "ARCHITECTURE.md").write_text(V1, encoding="utf-8")
    store = KnowledgeStore(
        db_path=str(tmp_path / "knowledge.db"),
        index_path=str(tmp_path / "knowledge.index"),
        id_map_path=str(tmp_path / "knowledge_id_map.json"),
        embed_fn=fake_embed,
    )
    ingestor = KnowledgeIngestor(store=store, roots=str(root))
    svc = KnowledgeService(store=store, ingestor=ingestor,
                           retrieval=KnowledgeRetrieval(store=store))
    yield svc, store, root
    store.close()


def test_ingest_search_and_cite(env):
    svc, store, root = env
    out = svc.ingest("")
    assert out["added_count"] == 1
    assert out["errors"] == []

    text = svc.search_text("鉴权设计 双令牌", top_k=3)
    assert "【参考资料】" in text
    assert "ARCHITECTURE.md" in text
    assert "双令牌" in text

    hits = svc.search("部署 端口", top_k=3)
    assert hits
    assert any("uvicorn" in h["text"] or "8080" in h["text"] for h in hits)


def test_modify_file_then_hits_new_version(env):
    svc, store, root = env
    svc.ingest("")
    (root / "ARCHITECTURE.md").write_text(V2, encoding="utf-8")
    out = svc.ingest("")
    assert out["updated_count"] == 1

    doc = store.list_documents()[0]
    assert doc.version == 2
    hits = svc.search("refresh 令牌轮换", top_k=3)
    assert hits
    assert any("轮换" in h["text"] for h in hits)
    # 旧内容不应再出现（版本被覆盖，不是追加）
    stale = svc.search("默认端口 8080", top_k=5)
    assert all("8080" not in h["text"] for h in stale)
    fresh = svc.search("默认端口", top_k=5)
    assert any("9090" in h["text"] for h in fresh)


def test_auto_inject_intent_gate(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INJECT", True, raising=False)
    svc = KnowledgeService()
    assert svc.should_retrieve("按文档里的接口参数怎么填")
    assert not svc.should_retrieve("帮我讲个笑话")
    assert should_retrieve("看下 docs/ARCHITECTURE.md")


def test_knowledge_db_does_not_touch_memory_tables(env):
    svc, store, _ = env
    svc.ingest("")
    conn = sqlite3.connect(store._db_path)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "documents" in tables and "chunks" in tables
    # 与事件记忆隔离：知识库不建 events 表
    assert "events" not in tables


def test_delete_then_search_misses(env):
    svc, store, _ = env
    svc.ingest("")
    doc_id = store.list_documents()[0].id
    assert svc.delete(doc_id)["deleted"] is True
    assert svc.search("双令牌", top_k=3) == []
    assert svc.stats()["documents"] == 0
