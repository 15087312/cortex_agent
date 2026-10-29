"""知识库工具单测 — 注册元数据、能力缺失降级、经能力端口的调用"""
import json

import pytest

import infra.tool_manager.tools.knowledge_tools as kt
from infra.tool_manager.service_registry import get_capability
from infra.tool_manager.tool_registry import ToolRegistry

EXPECTED = {
    "knowledge_search": ("LOW", "query"),
    "knowledge_list": ("LOW", "query"),
    "knowledge_ingest": ("MEDIUM", "mutation"),
    "knowledge_delete": ("MEDIUM", "mutation"),
    "knowledge_reindex": ("MEDIUM", "mutation"),
}


class _StubService:
    def __init__(self):
        self.calls = []

    def search(self, query, top_k=None, path_prefix=""):
        self.calls.append(("search", query, top_k, path_prefix))
        return [{
            "chunk_id": "kc_1", "doc_id": "kd_1", "title": "鉴权设计",
            "heading_path": "1. 鉴权流程", "text": "双令牌", "score": 0.03,
            "source_path": "docs/auth.md", "char_start": 0, "char_end": 3,
        }]

    def search_text(self, query, top_k=None, path_prefix=""):
        return "【参考资料】（来自知识库，引用时请标注文档与章节）"

    def list_docs(self, limit=50, offset=0):
        return [{"id": "kd_1", "title": "鉴权设计", "source_path": "docs/auth.md"}]

    def ingest(self, path=""):
        return {"added_count": 1, "path": path}

    def delete(self, doc_id):
        return {"deleted": True, "doc_id": doc_id}

    def reindex(self, full=False):
        return {"mode": "full" if full else "incremental"}

    def stats(self):
        return {"documents": 1, "chunks": 2}


@pytest.fixture
def stub(monkeypatch):
    svc = _StubService()
    monkeypatch.setattr(kt, "get_capability", lambda name: (lambda: svc) if name == "knowledge_base" else None)
    return svc


def test_tools_are_registered_with_expected_risk():
    for name, (risk, category) in EXPECTED.items():
        info = ToolRegistry.get_tool(name)
        assert info is not None, name
        assert info.risk_level == risk
        assert info.category == category
        assert "knowledge" in info.tags


def test_missing_capability_degrades_without_raising(monkeypatch):
    monkeypatch.setattr(kt, "get_capability", lambda name: None)
    payload = json.loads(kt.knowledge_search("鉴权"))
    assert "未注册" in payload["error"]
    assert json.loads(kt.knowledge_list())["error"]


def test_search_returns_hits_and_rendered_block(stub):
    payload = json.loads(kt.knowledge_search("鉴权流程", top_k="3", path_prefix="docs/"))
    assert payload["count"] == 1
    assert payload["hits"][0]["source_path"] == "docs/auth.md"
    assert "【参考资料】" in payload["rendered"]
    assert stub.calls == [("search", "鉴权流程", 3, "docs/")]


def test_search_clamps_top_k(stub):
    json.loads(kt.knowledge_search("x", top_k="999"))
    assert stub.calls[-1][2] == 20


def test_ingest_delete_reindex_pass_through(stub):
    assert json.loads(kt.knowledge_ingest("docs"))["added_count"] == 1
    assert json.loads(kt.knowledge_delete("kd_1"))["deleted"] is True
    assert json.loads(kt.knowledge_reindex("true"))["mode"] == "full"
    assert json.loads(kt.knowledge_reindex())["mode"] == "incremental"


def test_delete_requires_doc_id(stub):
    assert "error" in json.loads(kt.knowledge_delete("  "))


def test_search_output_stays_valid_json_under_mcp_limit(stub, monkeypatch):
    """命中很长时必须仍是合法 JSON 且不超 MCP 硬截断上限（4000）。

    combined_provider 对 str 结果按 4000 字符直接截断，超长 JSON 会变成
    半截字符串，模型无法解析——工具必须自己收缩。
    """
    long_text = "令牌" * 400
    monkeypatch.setattr(stub, "search", lambda *a, **k: [
        {
            "chunk_id": f"kc_{i}", "doc_id": "kd_1", "title": "鉴权设计",
            "heading_path": "1. 鉴权流程", "text": long_text, "score": 0.5,
            "source_path": "docs/auth.md", "char_start": 0, "char_end": 10,
        }
        for i in range(20)
    ])
    monkeypatch.setattr(stub, "search_text", lambda *a, **k: "【参考资料】\n" + long_text * 5)

    out = kt.knowledge_search("鉴权", top_k="20")
    assert len(out) <= 3600
    payload = json.loads(out)  # 合法 JSON，不抛异常
    assert payload["count"] == len(payload["hits"])
    assert payload["total"] == 20
    assert payload["truncated"] is True


def test_list_output_stays_valid_json_under_mcp_limit(stub, monkeypatch):
    monkeypatch.setattr(stub, "list_docs", lambda limit=50, offset=0: [
        {"id": f"kd_{i}", "title": "鉴权设计" * 20,
         "source_path": f"docs/auth_{i}.md", "chunk_count": 3}
        for i in range(200)
    ])
    out = kt.knowledge_list("200")
    assert len(out) <= 3600
    payload = json.loads(out)
    assert payload["truncated"] is True
    assert payload["count"] == len(payload["documents"])
    assert payload["total"] == 200


def test_ingest_output_stays_valid_json_with_many_files(stub, monkeypatch):
    monkeypatch.setattr(stub, "ingest", lambda path="": {
        "added": [
            {"id": f"kd_{i}", "title": "文档" * 20,
             "source_path": f"docs/doc_{i}.md", "chunks": 3, "vectors": 3, "version": 1}
            for i in range(200)
        ],
        "updated": [], "unchanged": [], "skipped": [], "errors": [],
        "added_count": 200, "updated_count": 0, "unchanged_count": 0,
        "skipped_count": 0, "error_count": 0,
    })
    out = kt.knowledge_ingest("docs")
    assert len(out) <= 3600
    payload = json.loads(out)
    assert payload["truncated"] is True
    assert payload["added_count"] == len(payload["added"])


def test_knowledge_capability_registered_by_bootstrap():
    factory = get_capability("knowledge_base")
    assert factory is not None
    service = factory()
    assert hasattr(service, "search") and hasattr(service, "ingest")
