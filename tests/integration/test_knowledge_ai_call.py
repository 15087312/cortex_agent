"""知识库 AI 调用路径集成测试

链路：模型原生 function call（ToolCallRequest）→ CombinedToolExecutor
      → knowledge_* 工具 → KnowledgeService → 真实 SQLite/FTS/切分（假 embedding）

目的：验证「AI 能调用知识库」不是只有 schema，而是整条执行链真的通。
"""
import json
from unittest.mock import MagicMock

import pytest

import infra.tool_manager.tools.knowledge_tools as kt
from infra.mcp.combined_provider import CombinedToolExecutor
from infra.mcp.types import ToolCallRequest
from modules.knowledge.ingestor import KnowledgeIngestor
from modules.knowledge.retrieval import KnowledgeRetrieval
from modules.knowledge.service import KnowledgeService
from modules.knowledge.store import KnowledgeStore
from config.settings import settings

DOC = """# 鉴权设计

## 1. 令牌类型

access 令牌短期有效，refresh 令牌长期有效并落库持久化。

## 2. 令牌吊销

登出时把 refresh 令牌加入黑名单，access 令牌等待自然过期。
"""


def fake_embed(text: str, dim: int = 16):
    vec = [0.0] * dim
    for ch in text:
        vec[ord(ch) % dim] += 1.0
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


@pytest.fixture
def kb(tmp_path, monkeypatch):
    store = KnowledgeStore(
        db_path=str(tmp_path / "knowledge.db"),
        index_path=str(tmp_path / "knowledge.index"),
        id_map_path=str(tmp_path / "knowledge_id_map.json"),
        embed_fn=fake_embed,
    )
    root = tmp_path / "docs"
    root.mkdir()
    (root / "auth.md").write_text(DOC, encoding="utf-8")

    ingestor = KnowledgeIngestor(store=store, roots=str(root))
    ingestor.ingest_roots()
    service = KnowledgeService(
        store=store,
        ingestor=ingestor,
        retrieval=KnowledgeRetrieval(store=store),
    )
    monkeypatch.setattr(
        kt, "get_capability",
        lambda name: (lambda: service) if name == "knowledge_base" else None,
    )
    yield service, root
    store.close()


def _executor() -> CombinedToolExecutor:
    return CombinedToolExecutor(MagicMock(get_tool=lambda name: None))


def test_model_tool_call_returns_citable_hits(kb):
    result = _executor().execute(ToolCallRequest(
        tool_name="knowledge_search",
        params={"query": "refresh 令牌持久化"},
    ))

    assert result.success, result.error
    payload = json.loads(result.result)
    assert payload["count"] >= 1
    hit = payload["hits"][0]
    assert hit["source_path"].endswith("docs/auth.md")
    assert hit["heading_path"]
    # 渲染块带出处，模型可直接引用
    assert "【参考资料】" in payload["rendered"]
    assert "auth.md" in payload["rendered"]


def test_model_tool_call_lists_documents(kb):
    result = _executor().execute(ToolCallRequest(tool_name="knowledge_list", params={}))
    assert result.success, result.error
    payload = json.loads(result.result)
    assert payload["count"] == 1
    assert payload["documents"][0]["source_path"].endswith("docs/auth.md")
    assert payload["stats"]["documents"] == 1


def test_model_tool_call_ingest_is_incremental(kb):
    _, root = kb
    result = _executor().execute(ToolCallRequest(
        tool_name="knowledge_ingest", params={"path": str(root)},
    ))
    assert result.success, result.error
    payload = json.loads(result.result)
    # 内容未变 → 全部跳过，不会重复切分/嵌入
    assert payload["unchanged_count"] == 1
    assert payload["added_count"] == 0


def test_model_tool_call_ingest_rejects_path_outside_roots(kb, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# 越权", encoding="utf-8")
    result = _executor().execute(ToolCallRequest(
        tool_name="knowledge_ingest", params={"path": str(outside)},
    ))
    assert result.success, result.error
    payload = json.loads(result.result)
    assert payload["error_count"] == 1
    assert "白名单" in payload["errors"][0]["error"]


def test_search_without_hits_returns_actionable_hint(kb, monkeypatch):
    # 门槛拉到不可能通过 + 查询词与库内无词项交集 → 命中 0 条（走空结果提示分支）
    monkeypatch.setattr(settings, "KNOWLEDGE_MIN_SEMANTIC", 1.01, raising=False)
    result = _executor().execute(ToolCallRequest(
        tool_name="knowledge_search", params={"query": "xyzzy plugh"},
    ))
    assert result.success, result.error
    payload = json.loads(result.result)
    assert payload["count"] == 0
    assert "knowledge_list" in payload["hint"]
