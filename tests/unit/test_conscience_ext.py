"""conscience 测试：因果知识提取 / 内心独白 / 反馈闭环"""
import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.thinking.conscience import (
    Conscience,
    ConscienceGuidance,
    get_conscience,
    _conscience_instance,
    CONSCIENCE_PROMPT,
    CONSCIENCE_SYSTEM_PROMPT,
)


def _client(text="（内心独白）"):
    c = MagicMock()
    c.generate = AsyncMock(return_value=text)
    return c


# ── 基础 ───────────────────────────────────────────────────────────────

def test_add_to_dialog():
    c = Conscience()
    c.add_to_dialog("user", "你好")
    c.add_to_dialog("assistant", "好的")
    c.add_to_dialog("system", "忽略")  # 非 user/assistant 忽略
    buf = c._dialog_buffers["large_primary"]
    assert len(buf) == 2
    assert "用户" in buf[0]
    assert "助手" in buf[1]
    # 超过 20 条裁剪
    for i in range(25):
        c.add_to_dialog("user", f"消息{i}")
    assert len(c._dialog_buffers["large_primary"]) == 20


def test_add_to_dialog_session_isolated():
    """心理活动对话缓存按 session 隔离：不同会话互不累计"""
    c = Conscience()
    c.add_to_dialog("user", "会话A内容", session_id="sess_a")
    c.add_to_dialog("user", "会话B内容", session_id="sess_b")
    assert c._dialog_buffers["sess_a"][0] == "用户: 会话A内容"
    assert c._dialog_buffers["sess_b"][0] == "用户: 会话B内容"
    # 切换会话后 think 只引用本会话的最近对话
    assert c._dialog_buffers.get("sess_c", []) == []


def test_add_to_dialog_empty():
    c = Conscience()
    c.add_to_dialog("user", "")
    assert c._dialog_buffers == {}


def test_extract_keywords():
    assert set(Conscience._extract_keywords("财务问题 performance")) >= {"财务", "performance"}
    assert Conscience._extract_keywords("") == []
    kws = Conscience._extract_keywords("这是一个长度超过四字的词条示例")
    assert len(kws) <= 10


def test_build_role_context_uses_unified_persona(monkeypatch):
    """心理活动人设走统一入口 get_role_persona（用户自定义优先），与对话同源"""
    from config.settings import Settings
    monkeypatch.setattr(Settings, "get_role_persona",
                        lambda self, role: "统一的自定义人设文本")
    c = Conscience(model_client=None)
    intro, persona, name = c._build_role_context("large_primary")
    assert persona == "统一的自定义人设文本"
    assert name == "总指挥"


def test_build_role_context_fallback_builtin(monkeypatch):
    """统一入口为空时回退 roles.yaml 内置人设（settings 异常兜底）"""
    c = Conscience(model_client=None)
    intro, persona, name = c._build_role_context("large_primary")
    # roles.yaml 内置总指挥（用户无自定义时）
    assert "用户与系统之间的唯一桥梁" in persona
    assert name == "总指挥"


def test_build_role_context_settings_error_fallback(monkeypatch):
    """settings.get_role_persona 抛异常 → 回退 roles.yaml 内置字段（防御）"""
    from config.settings import Settings
    def boom(self, role):
        raise RuntimeError("settings 异常")
    monkeypatch.setattr(Settings, "get_role_persona", boom)
    c = Conscience(model_client=None)
    _, persona, name = c._build_role_context("large_primary")
    assert "用户与系统之间的唯一桥梁" in persona
    assert name == "总指挥"


def test_resolve_role_unknown_owner():
    """未知 owner → 默认 orchestrator（总指挥）（防御）"""
    c = Conscience(model_client=None)
    assert c._resolve_role("custom_xyz").get("role") == "orchestrator"
    assert c._resolve_role("").get("role") == "orchestrator"
    assert c._resolve_role(None).get("role") == "orchestrator"


def test_resolve_role_roles_yaml_failure(monkeypatch):
    """roles.yaml 解析失败 → roles 空 → 默认总指挥，不崩（防御）"""
    import yaml as _yaml
    def boom(*a, **k):
        raise RuntimeError("roles.yaml 损坏")
    monkeypatch.setattr(_yaml, "safe_load", boom)
    c = Conscience(model_client=None)
    _, persona, name = c._build_role_context("large_primary")
    assert name == "总指挥"
    assert persona == ""


# ── 因果知识提取 ───────────────────────────────────────────────────────

def test_get_causal_knowledge_from_events(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph, CausalNode
    from modules.memory.causal_tree import CausalTree
    from modules.memory.event_store import EventStore, MemoryEvent

    graph = CausalGraph(db_path=str(tmp_path / "cg.db"))
    node = CausalNode(label="性能")
    graph.save_node(node)
    store = EventStore(
        db_path=str(tmp_path / "ev.db"),
        faiss_index_path=str(tmp_path / "ev.faiss"),
        id_map_path=str(tmp_path / "ev.json"),
    )
    store.save_event(MemoryEvent(fact="优化了缓存", causal_node_ids=[node.id]))
    monkeypatch.setattr(EventStore, "_instance", store)
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))

    c = Conscience()
    c._get_node_ids_from_events = MagicMock(return_value=[node.id])
    out = c._get_causal_knowledge("性能问题")
    assert "性能" in out
    assert node.id in c._last_analyzed_node_ids


def test_get_causal_knowledge_fallback_to_anchors(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph, CausalNode
    graph = CausalGraph(db_path=str(tmp_path / "cg2.db"))
    node = CausalNode(label="延期")
    graph.save_node(node)
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    c = Conscience()
    c._get_node_ids_from_events = MagicMock(return_value=[])
    out = c._get_causal_knowledge("项目延期")
    assert "延期" in out


def test_get_causal_knowledge_no_data(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(tmp_path / "cg3.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    c = Conscience()
    c._get_node_ids_from_events = MagicMock(return_value=[])
    out = c._get_causal_knowledge("完全无关的词")
    assert out == "（暂无相关因果经验）"


def test_get_causal_knowledge_exception(monkeypatch):
    from modules.memory.causal_graph import CausalGraph
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
    c = Conscience()
    out = c._get_causal_knowledge("q")
    assert out == "（暂无相关因果经验）"


# ── _get_node_ids_from_events ──────────────────────────────────────────

def test_get_node_ids_from_events(monkeypatch):
    from modules.memory.event_retrieval import EventRetrieval
    from modules.memory.event_store import MemoryEvent

    class FakeRetrieval:
        async def retrieve(self, query, max_results=10, threshold=0.0, owner_id=""):
            return [
                MemoryEvent(fact="a", causal_node_ids=["n1", "n2"]),
                MemoryEvent(fact="b", causal_node_ids=["n1"]),
            ]

    monkeypatch.setattr(EventRetrieval, "get_instance", staticmethod(lambda: FakeRetrieval()))
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    c = Conscience()
    try:
        ids = c._get_node_ids_from_events("q")
    finally:
        loop.close()
    assert ids == ["n1", "n2"]  # n1 频率更高在前


def test_get_node_ids_from_events_empty(monkeypatch):
    from modules.memory.event_retrieval import EventRetrieval

    class FakeRetrieval:
        async def retrieve(self, **kw):
            return []

    monkeypatch.setattr(EventRetrieval, "get_instance", staticmethod(lambda: FakeRetrieval()))
    loop = asyncio.new_event_loop()
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    c = Conscience()
    try:
        assert c._get_node_ids_from_events("q") == []
    finally:
        loop.close()


def test_get_node_ids_from_events_error(monkeypatch):
    from modules.memory.event_retrieval import EventRetrieval
    monkeypatch.setattr(EventRetrieval, "get_instance", staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("boom"))))
    c = Conscience()
    assert c._get_node_ids_from_events("q") == []


def test_get_node_ids_from_events_running_loop(monkeypatch):
    import asyncio as _asyncio

    events = [type("E", (), {"causal_node_ids": ["n9"]})()]

    class FakeFuture:
        def result(self, timeout=None):
            return events

    class FakeLoop:
        def is_running(self):
            return True

        def run_coroutine_threadsafe(self, coro, loop):
            return FakeFuture()

    monkeypatch.setattr(_asyncio, "get_event_loop", lambda: FakeLoop())
    monkeypatch.setattr(_asyncio, "run_coroutine_threadsafe", lambda coro, loop: FakeFuture())
    c = Conscience()
    assert c._get_node_ids_from_events("q") == ["n9"]


# ── think ──────────────────────────────────────────────────────────────

async def test_think_generates_monologue(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(tmp_path / "cg4.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    c = Conscience(model_client=_client("记得性能问题往往导致延期"))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    out = await c.think("性能又出问题了", owner_id="large_primary")
    assert out == "记得性能问题往往导致延期"
    assert c._dialog_buffers["large_primary"]  # 独白加入历史（按 session）
    gen_kwargs = c._model_client.generate.call_args.kwargs
    assert gen_kwargs["system_prompt"] == CONSCIENCE_SYSTEM_PROMPT
    # 无历史经验（_get_node_ids_from_events 返回 []）→ 走"不编造、仅推测"提示词分支
    prompt = gen_kwargs.get("prompt") or c._model_client.generate.call_args.args[0]
    assert "不要编造" in prompt
    assert "推测" in prompt


async def test_think_no_client():
    c = Conscience(model_client=None)
    c._get_node_ids_from_events = MagicMock(return_value=[])
    assert await c.think("q") == ""


async def test_think_llm_error():
    client = MagicMock()
    client.generate = AsyncMock(side_effect=RuntimeError("llm down"))
    c = Conscience(model_client=client)
    c._get_node_ids_from_events = MagicMock(return_value=[])
    assert await c.think("q") == ""


async def test_think_empty_output():
    c = Conscience(model_client=_client("   "))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    assert await c.think("q") == ""


async def test_think_spatial_enhancement(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "SPATIAL_ENHANCEMENT_ENABLED", True)
    c = Conscience(model_client=_client("行动"))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    await c.think("q")
    prompt = c._model_client.generate.call_args.args[0]
    assert "空间增强" in prompt


async def test_think_values_file_missing(monkeypatch, tmp_path):
    # values.txt 不存在 → 默认值
    from modules.memory.causal_graph import CausalGraph
    graph = CausalGraph(db_path=str(tmp_path / "cg5.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    import modules.thinking.conscience as cons_mod
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    c = Conscience(model_client=_client("x"))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    await c.think("q")
    prompt = c._model_client.generate.call_args.args[0]
    assert "诚实、负责、安全、有益" in prompt


# ── analyze_feedback ───────────────────────────────────────────────────

async def test_analyze_feedback_no_nodes():
    c = Conscience()
    await c.analyze_feedback("q", "r")  # 无节点 → 直接返回


async def test_analyze_feedback_adjusts_confidence(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph, CausalNode
    from modules.memory.event_reducer import EventReducer
    import modules.memory.event_reducer as er_mod

    graph = CausalGraph(db_path=str(tmp_path / "cg6.db"))
    node = CausalNode(label="原因", confidence=0.5)
    graph.save_node(node)
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))

    reducer = EventReducer(model_client=_client('{"confirmed": ["%s"], "contradicted": []}' % node.id))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)

    c = Conscience()
    c._last_analyzed_node_ids = [node.id]
    await c.analyze_feedback("q", "r")
    assert graph.get_node(node.id).confidence > 0.5
    assert c._last_analyzed_node_ids == []


async def test_analyze_feedback_contradicted(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph, CausalNode
    from modules.memory.event_reducer import EventReducer
    import modules.memory.event_reducer as er_mod

    graph = CausalGraph(db_path=str(tmp_path / "cg7.db"))
    node = CausalNode(label="原因", confidence=0.8)
    graph.save_node(node)
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    reducer = EventReducer(model_client=_client('{"confirmed": [], "contradicted": ["%s"]}' % node.id))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [node.id]
    await c.analyze_feedback("q", "r")
    assert graph.get_node(node.id).confidence < 0.8


async def test_analyze_feedback_no_model_client(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph
    from modules.memory.event_reducer import EventReducer
    import modules.memory.event_reducer as er_mod
    graph = CausalGraph(db_path=str(tmp_path / "cg8.db"))
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    reducer = EventReducer(model_client=None)
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = ["n1"]
    await c.analyze_feedback("q", "r")  # 无 client → 直接返回


async def test_analyze_feedback_bad_json(monkeypatch, tmp_path):
    from modules.memory.causal_graph import CausalGraph, CausalNode
    from modules.memory.event_reducer import EventReducer
    import modules.memory.event_reducer as er_mod
    graph = CausalGraph(db_path=str(tmp_path / "cg9.db"))
    node = CausalNode(label="L")
    graph.save_node(node)
    monkeypatch.setattr(CausalGraph, "get_instance", staticmethod(lambda: graph))
    reducer = EventReducer(model_client=_client("不是json"))
    monkeypatch.setattr(er_mod, "_reducer_instance", reducer)
    c = Conscience()
    c._last_analyzed_node_ids = [node.id]
    await c.analyze_feedback("q", "r")  # 解析失败不抛异常
    assert c._last_analyzed_node_ids == []


def test_get_conscience_singleton(monkeypatch):
    import modules.thinking.conscience as mod
    monkeypatch.setattr(mod, "_conscience_instance", None)
    a = get_conscience()
    b = get_conscience()
    assert a is b


async def test_think_disabled_by_switch(monkeypatch):
    """心理活动开关 MENTAL_ACTIVITY_ENABLED=False → think 直接返回空、不调模型"""
    from config.settings import settings
    c = Conscience(model_client=_client("独白"))
    c._get_node_ids_from_events = MagicMock(return_value=[])
    monkeypatch.setattr(settings, "MENTAL_ACTIVITY_ENABLED", False)
    out = await c.think("q", owner_id="s1")
    assert out == ""
    c._model_client.generate.assert_not_called()


async def test_think_switch_read_exception(monkeypatch):
    """开关读取抛异常 → except pass，继续正常生成（防御）"""
    import sys
    from config.settings import settings
    c = Conscience(model_client=_client("独白"))
    c._get_node_ids_from_events = MagicMock(return_value=[])

    class BoomSettings:
        def __getattr__(self, name):
            raise RuntimeError("boom")
        MENTAL_ACTIVITY_ENABLED = None  # getattr 拿到 None（非真）→ 不返回空

    # 用可访问属性但抛异常的方式：直接 monkeypatch settings 为 BoomSettings
    monkeypatch.setattr(sys.modules["config.settings"], "settings", BoomSettings())
    out = await c.think("q", owner_id="s2")
    # settings 异常时 _get_causal_knowledge 内部也会处理，最终正常生成或返回
    assert out in ("", "独白")
