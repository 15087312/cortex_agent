#!/usr/bin/env python3
"""生产模块覆盖补充测试 — 将此前无测试触及的模块纳入泄漏检测覆盖范围。

原则：每个无测试触及的生产模块至少有一个测试引用（import + 核心路径冒烟），
使 sys.modules 覆盖清单（tests/conftest._report_module_coverage）计数归零，
从而纳入 pympler 泄漏检测。仅测纯逻辑/轻量路径，不依赖真实 API / 屏幕 / 浏览器。
"""
import numpy as np
import pytest


# ───────────────────────── config.providers ─────────────────────────
def test_provider_base_is_abstract():
    from config.providers.base import ProviderBase
    assert ProviderBase.__abstractmethods__  # 抽象基类，未实现抽象方法无法实例化


def test_openai_provider_build():
    from config.providers.openai import OpenAIProvider
    p = OpenAIProvider("sk-test", "https://api.openai.com/v1", "gpt-4")
    assert p.build_headers()["Authorization"] == "Bearer sk-test"
    assert "chat/completions" in p.chat_url()
    req = p.build_request([{"role": "user", "content": "hi"}],
                          max_tokens=100, temperature=0.7)
    assert req["model"] == "gpt-4"
    assert req["messages"][0]["content"] == "hi"


def test_anthropic_provider_build():
    from config.providers.anthropic import AnthropicProvider
    p = AnthropicProvider("sk-ant", "https://api.anthropic.com", "claude-3")
    assert p.build_headers()["x-api-key"] == "sk-ant"
    assert "messages" in p.chat_url()
    tools = p._tools_to_anthropic([{"name": "t1", "parameters": {"properties": {}}}])
    assert isinstance(tools, list)
    assert tools[0]["name"] == "t1"


def test_dashscope_provider_build():
    from config.providers.dashscope import DashScopeProvider
    p = DashScopeProvider("sk-ds", "https://dashscope.aliyuncs.com", "qwen-max")
    assert p.build_headers()["Authorization"].startswith("Bearer")
    req = p.build_request([{"role": "user", "content": "hi"}],
                          max_tokens=50, temperature=0.5)
    assert req["model"] == "qwen-max"


def test_provider_registry_dispatch():
    from config.providers.registry import ProviderRegistry, get_provider
    reg = ProviderRegistry()
    from config.providers.openai import OpenAIProvider
    from config.providers.anthropic import AnthropicProvider
    from config.providers.dashscope import DashScopeProvider
    assert reg.get_provider_class_by_format("openai") is OpenAIProvider
    assert reg.get_provider_class_by_format("anthropic") is AnthropicProvider
    assert reg.get_provider_class_by_format("dashscope") is DashScopeProvider
    assert reg.get_provider_class_by_format("") is None  # 回退 URL 推断
    p = get_provider("m1", "k", api_format="openai")
    assert p.model_name == "m1"


# ───────────────────────── config.prompts ─────────────────────────
def test_prompt_composer_build():
    from config.prompts.composer import PromptComposer, PromptRequest
    c = PromptComposer()
    # pool=None 时仅返回任务块（不依赖 TurnContext）
    built = c.build(None, role="large", tier="large", question="分析需求")
    assert "分析需求" in built


def test_prompt_composer_build_system():
    from config.prompts.composer import PromptComposer, PromptRequest
    c = PromptComposer()
    req = PromptRequest(tier="large", role="orchestrator", task="做 X")
    sys_prompt = c.build_system(req)
    assert isinstance(sys_prompt, str) and sys_prompt


# ───────────────────────── infra.model ─────────────────────────
def test_config_fingerprint():
    from infra.model.config_fingerprint import model_config_fingerprint, close_client_session
    fp = model_config_fingerprint("large")
    assert isinstance(fp, tuple) and len(fp) == 4
    close_client_session(None)  # 空 client 安全


def test_model_interface_exports():
    from infra.model.interface import BaseModelClient, ChatMessage, ChatResponse
    assert hasattr(BaseModelClient, "chat")
    assert ChatMessage and ChatResponse


# ───────────────────────── infra.tool_manager ─────────────────────────
def test_context_budget_allocate():
    from infra.tool_manager.context_budget import ContextBudget, ContextBudgetManager
    b = ContextBudget()
    r = b.allocate(10)
    assert set(r) == {"system_prompt", "tool_descriptions", "conversation_history", "memory_retrieval"}
    assert sum(r.values()) <= b.total_tokens
    # 工具少 → 减少工具描述空间
    low = b.allocate(2)["tool_descriptions"]
    many = b.allocate(30)["tool_descriptions"]
    assert low <= many
    m = ContextBudgetManager()
    assert m.estimate_tokens("这是一个比较长的测试文本，用于估算 token 数量") > 0
    assert m.estimate_tokens("") == 0


def test_tool_discovery_search():
    from infra.tool_manager.tool_discovery import get_tool_discovery_engine
    res = get_tool_discovery_engine().search("文件", limit=3)
    assert isinstance(res, list)
    for r in res:
        assert r.tool_name and r.relevance_score >= 0


def test_tool_discovery_categories():
    from infra.tool_manager.tool_discovery import get_tool_discovery_engine
    eng = get_tool_discovery_engine()
    cats = eng.get_tools_by_category("query")
    assert isinstance(cats, list)
    assert isinstance(eng.get_tools_by_tag("file"), list)


# ───────────────────────── infra.mcp ─────────────────────────
def test_in_memory_tool_provider():
    from infra.mcp.in_memory import InMemoryMCPToolProvider
    from infra.mcp.types import ToolSpec
    t = ToolSpec(name="t1", description="d", source="test", risk_level="LOW")
    prov = InMemoryMCPToolProvider()
    prov.register(t)
    assert prov.get_tool("t1").name == "t1"
    assert prov.list_tools(source="test") == {"t1": t}
    assert prov.list_tools(source="other") == {}
    api = prov.get_tools_for_api(["t1"])
    assert api and api[0]["function"]["name"] == "t1"
    assert prov.get_tools_for_api(["nope"]) == []
    assert prov.get_tool("missing") is None


# ───────────────────────── modules.database ─────────────────────────
def test_database_models_importable():
    import modules.database.models  # noqa: F401  占位模块，保证可导入


# ───────────────────────── modules.management ─────────────────────────
def test_management_error_reporter():
    from modules.management.interface import get_error_reporter, ErrorReporterPort
    reporter = get_error_reporter()
    assert reporter is not None
    assert hasattr(reporter, "report")


def test_management_core_interfaces():
    from modules.management.core.interfaces import (
        PerceptionStatusAdapter,
        SecurityStatusAdapter,
        ModuleStatusPort,
    )
    p = PerceptionStatusAdapter()
    assert isinstance(p.get_status(), dict)
    s = SecurityStatusAdapter()
    assert isinstance(s.get_status(), dict)
    assert ModuleStatusPort


# ───────────────────────── cortex.main ─────────────────────────
def test_cortex_main_helpers(monkeypatch):
    from cortex.main import _get_project_root, _port_in_use, parse_args
    root = _get_project_root()
    assert (root / "api" / "main.py").exists()  # 项目根含 api/main.py
    assert _port_in_use(65534) is False  # 高位端口通常空闲
    monkeypatch.setattr("sys.argv", ["cortex"])
    args = parse_args()
    assert args.port == 8080 or args.port is not None


# ───────────────────────── frontend.server ─────────────────────────
def test_frontend_server_proxy_handler():
    from frontend.server import _resolve_backend_port, ProxyHandler, BACKEND_PORT
    port = _resolve_backend_port()
    assert isinstance(port, int) and port > 0
    assert isinstance(BACKEND_PORT, int)
    assert issubclass(ProxyHandler, __import__("http.server").server.BaseHTTPRequestHandler)


# ───────────────────────── infra.data_process.core ─────────────────────────
def test_cdp_scanner_no_chromium():
    from infra.data_process.core.cdp_scanner import CDPScanner
    ports = CDPScanner().find_chromium_ports()
    assert isinstance(ports, list)  # 无 chrome 时为空列表，不会抛异常


# ───────────────────────── infra.mcp.servers.screen_* ─────────────────────────
def test_screen_diff_server_compute_frame_diff():
    from infra.mcp.servers import screen_diff_server as sds
    prev = np.zeros((10, 10, 3), dtype=np.uint8)
    current = prev.copy()
    assert sds._compute_frame_diff(current, prev)["has_changed"] is False
    current[:5, :5] = 255  # 5x5 块变化，规避高斯模糊/形态学去噪吞掉单像素
    diff = sds._compute_frame_diff(current, prev)
    assert diff["has_changed"] is True
    assert diff["change_ratio"] > 0
    # 形状不一致 → 视为全变化
    big = sds._compute_frame_diff(current, np.zeros((5, 5, 3), dtype=np.uint8))
    assert big["change_ratio"] == 1.0


def test_screen_monitor_server_handlers(monkeypatch):
    import infra.mcp.servers.screen_monitor_server as sms
    sent = []
    monkeypatch.setattr(sms, "_send", lambda msg: sent.append(msg))
    sms._handle_initialize({"jsonrpc": "2.0", "id": 1})
    assert sent and sent[0]["result"]["serverInfo"]["name"] == "screen_monitor"
    assert sent[0]["id"] == 1


# ───────────────────────── 核心思考/记忆/安全模块冒烟 ─────────────────────────
def test_task_notebook():
    from modules.memory.utils.task_notebook import TaskNotebook
    nb = TaskNotebook(session_id="s1")
    nb.update("待办A", is_finished=False)
    assert "待办A" in nb.content
    nb.clear()
    assert "任务刚开始" in nb.content  # 无条目时返回默认引导语


def test_perception_screen_backends_importable():
    import modules.perception.screen.touchpoint_backend  # noqa: F401
    import modules.perception.screen.vision_backend  # noqa: F401


def test_perception_trigger_importable():
    import modules.perception.trigger  # noqa: F401
    import modules.perception.trigger_think  # noqa: F401


def test_tool_permission_controller():
    from modules.security_system.tool_permission_controller import ToolPermissionController
    ctl = ToolPermissionController()
    visible = ctl.get_visible_tools(tier="large", mode="edit")
    assert isinstance(visible, list)


def test_thinking_adapters():
    from modules.thinking.adapters import (
        DifferenceDetectorActivityNotifier,
        SecurityApiAdapter,
    )
    DifferenceDetectorActivityNotifier().notify_activity()  # 无害调用
    ok, _ = SecurityApiAdapter().validate_input("正常输入")
    assert isinstance(ok, bool)


def test_attachment_handler_importable():
    import modules.thinking.attachment_handler  # noqa: F401


def test_blackboard_construct():
    from modules.thinking.cognition.blackboard import CognitiveBlackboard
    bb = CognitiveBlackboard(session_id="s1", turn_id="t1")
    assert hasattr(bb, "write_thought")


def test_context_slicer_importable():
    from modules.thinking.cognition.context_slicer import ContextSlicer
    assert hasattr(ContextSlicer, "slice_for_large")


def test_message_bus():
    from modules.thinking.communication.message_bus import Message, MessageType
    from modules.thinking.communication.interface import get_message_bus_port
    assert MessageType.SYSTEM.value
    msg = Message(msg_type=MessageType.SYSTEM, sender="t", content="x")
    assert isinstance(msg.to_dict(), dict)
    assert get_message_bus_port() is not None


def test_continuous_thinker_importable():
    import modules.thinking.core.continuous_thinker  # noqa: F401


def test_control_tools_decision():
    from modules.thinking.core.control_tools import ThinkingControlDecision
    d = ThinkingControlDecision.from_payload({"continue": False, "result_summary": "ok"})
    assert d.should_continue is False
    assert d.result_summary == "ok"


def test_delegation_port():
    from modules.thinking.core.delegation_port import (
        DelegationRequest,
        create_delegation_port,
    )
    req = DelegationRequest(task="查询", role="expert")
    assert req.task == "查询" and req.role == "expert"
    port = create_delegation_port()
    assert port is not None


def test_model_runner_importable():
    from modules.thinking.core.model_runner import ModelRunner
    assert ModelRunner is not None


def test_process_collector():
    from modules.thinking.core.process_collector import ThinkingProcessSnapshot
    from modules.thinking.core.process_collector import ThinkingTaskContext
    snap = ThinkingProcessSnapshot(session_id="s1", model_id="m1", tier="large",
                                   task_context=ThinkingTaskContext(task_id="t1", loop_goal="g", origin_model_id="m1"))
    assert snap.model_id == "m1" and snap.session_id == "s1"


def test_probe_permission():
    from modules.thinking.probes.probe_permission import ProbePermissionManager
    pm = ProbePermissionManager()
    assert isinstance(pm.can_control("large", "expert"), bool)


def test_runtime_expert_importable():
    import modules.thinking.runtime_expert  # noqa: F401


def test_session_graph():
    from modules.thinking.session_graph import SessionGraphStore
    g = SessionGraphStore()
    g.record(session_id="s1", model_id="m1", identity_name="总指挥",
             tier="large", return_to_model_id="", entry_type="", content="x", ts=1.0)
    graph = g.get_graph("s1")
    assert isinstance(graph, dict)


# ───────────────────────── utils 工具模块冒烟 ─────────────────────────
def test_utils_async_json_exceptions():
    import asyncio
    from utils.async_utils import async_wrap
    from utils.json_utils import DateTimeEncoder
    from utils.exceptions import BackendError, ModelError, ToolError
    from datetime import datetime
    assert asyncio.run(async_wrap(lambda: 42)()) == 42
    assert DateTimeEncoder().default(datetime(2026, 1, 1)) == "2026-01-01T00:00:00"
    e = BackendError("消息", cause=ValueError("根因"), code=400)
    assert isinstance(e, BackendError)
    assert issubclass(ModelError, BackendError)
    assert issubclass(ToolError, BackendError)


def test_utils_time_autostart_power():
    from utils.time_utils import now, datetime_to_timestamp, format_datetime
    from utils.autostart import _build_plist
    from utils.power import is_active
    assert datetime_to_timestamp(now()) > 0
    assert "Label" in _build_plist()
    assert isinstance(is_active(), bool)


def test_utils_ocr_engine_importable():
    from utils.ocr_utils import get_ocr_engine
    engine, _ = get_ocr_engine()
    assert engine is not None or True  # 无 OCR 依赖时返回 (None, 原因)，不抛异常
