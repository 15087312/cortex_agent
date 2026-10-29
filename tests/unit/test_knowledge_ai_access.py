"""知识库接入单测 — 工具暴露、角色可见性、模型调用路径、system prompt 指引"""
import json
from unittest.mock import MagicMock

import infra.tool_manager.tools.knowledge_tools as kt  # noqa: F401  触发 @ToolRegistry.register
from infra.mcp.combined_provider import CombinedToolExecutor, CombinedToolProvider
from infra.mcp.types import ToolCallRequest
from infra.tool_manager.tool_registry import ToolRegistry
from modules.security_system.tool_permission_controller import get_tool_permission_controller
from modules.thinking.core.model_runner import ModelRunner

WRITE_TOOLS = {"knowledge_ingest", "knowledge_delete", "knowledge_reindex"}


def _visible(tier: str, role: str):
    """经唯一权限出口取模型可见工具（与 ModelRunner 同一路径）。"""
    return get_tool_permission_controller().get_visible_tools(
        tier=tier, mode="plan", role=role,
    )


class _StubKB:
    def search(self, query, top_k=None, path_prefix=""):
        return [{
            "chunk_id": "kc_1", "doc_id": "kd_1", "title": "鉴权设计",
            "heading_path": "1. 令牌类型", "text": "refresh 令牌长期有效",
            "score": 0.5, "source_path": "docs/auth.md",
            "char_start": 0, "char_end": 12,
        }]

    def search_text(self, query, top_k=None, path_prefix=""):
        return "【参考资料】（来自知识库，引用时请标注文档与章节）"


# ── 可见性（身份白名单 → 权限控制器）────────────────────────────────────────

def test_large_sees_all_knowledge_tools():
    visible = set(_visible("large", "orchestrator"))
    assert {"knowledge_search", "knowledge_list"} | WRITE_TOOLS <= visible


def test_supervisor_and_expert_see_read_only_tools():
    for tier, role in (("supervisor", "code_supervisor"), ("expert", "code_writer")):
        visible = set(_visible(tier, role))
        assert {"knowledge_search", "knowledge_list"} <= visible, (tier, role)
        # 写入类不下放，避免专家随手改知识库
        assert not (WRITE_TOOLS & visible), (tier, role)


def test_role_without_grant_cannot_see_knowledge_tools():
    visible = set(_visible("expert", "customer"))
    assert "knowledge_search" not in visible


# ── 模型 tools schema（原生 function call 的入口）───────────────────────────

def test_core_schema_exposes_knowledge_search_with_query():
    api = ToolRegistry.get_core_tools_for_api(_visible("large", "orchestrator"))
    fn = {t["function"]["name"]: t["function"] for t in api}["knowledge_search"]
    assert fn["parameters"]["required"] == ["query"]
    assert set(fn["parameters"]["properties"]) == {"query", "top_k", "path_prefix"}


def test_provider_api_tools_include_knowledge_search_for_expert():
    manager = MagicMock()
    manager.get_all_tools.return_value = {}
    provider = CombinedToolProvider(manager)
    tools = provider.get_tools_for_api(_visible("expert", "code_writer"), core_only=True)
    assert "knowledge_search" in {t["function"]["name"] for t in tools}


def test_write_tools_are_listed_as_non_core_for_large():
    names = {t["name"] for t in ToolRegistry.list_non_core_tools(_visible("large", "orchestrator"))}
    assert WRITE_TOOLS <= names


def test_model_arg_gate_requires_query():
    runner = ModelRunner.__new__(ModelRunner)
    assert runner._has_required_tool_args("knowledge_search", {}) is False
    assert runner._missing_required_tool_args("knowledge_search", {}) == ["query"]
    assert runner._has_required_tool_args("knowledge_search", {"query": "鉴权"}) is True


# ── system prompt 指引（仅在可见时注入）────────────────────────────────────

def _runner_with_tools(tools):
    runner = ModelRunner.__new__(ModelRunner)
    runner._visible_tool_whitelist = lambda: list(tools)
    return runner


def test_prompt_section_injected_when_tool_visible():
    out = _runner_with_tools(["knowledge_search", "event_query"])._build_knowledge_prompt_section()
    assert "【知识库】" in out
    assert "knowledge_search" in out
    assert "event_query" in out


def test_prompt_section_skipped_when_tool_not_visible():
    assert _runner_with_tools(["calc", "todo"])._build_knowledge_prompt_section() == ""


def test_system_prompt_carries_knowledge_section(monkeypatch):
    runner = _runner_with_tools(["knowledge_search"])
    runner.tier = "large"
    ident = MagicMock()
    ident.role = "orchestrator"
    ident.personality = "你是总指挥"
    runner.identity = ident
    runner.blackboard = None
    runner._active_skill = None
    runner._active_skill_tool_rules = None
    runner.model_id = "m1"
    runner.session_id = "s1"
    monkeypatch.setattr(
        "config.prompts.composer.PromptComposer.build_system",
        lambda self, req: "SYS-PROMPT",
    )
    out = runner._build_system_prompt_for_mode()
    assert "SYS-PROMPT" in out
    assert "【知识库】" in out


# ── 模型 function call → MCP 执行器 → 知识库工具 ───────────────────────────

def test_tool_call_request_routes_to_knowledge_search(monkeypatch):
    stub = _StubKB()
    monkeypatch.setattr(
        kt, "get_capability",
        lambda name: (lambda: stub) if name == "knowledge_base" else None,
    )
    executor = CombinedToolExecutor(MagicMock(get_tool=lambda name: None))

    result = executor.execute(ToolCallRequest(
        tool_name="knowledge_search", params={"query": "鉴权"},
    ))

    assert result.success, result.error
    payload = json.loads(result.result)
    assert payload["count"] == 1
    assert payload["hits"][0]["source_path"] == "docs/auth.md"
    assert "【参考资料】" in payload["rendered"]
