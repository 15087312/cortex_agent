"""management/api 端点补充测试（此前 41 个端点零覆盖）"""
import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest

import modules.management.api as api_mod


def _run(coro):
    return asyncio.run(coro)


# ── 纯返回 / 简单端点 ───────────────────────────────────────────────────────

def test_root():
    out = _run(api_mod.root())
    assert out["success"] is True
    assert out["data"]["version"] == "1.0.0"


def test_context_removed_endpoints():
    assert _run(api_mod.get_context_status())["data"]["status"] == "removed"
    assert _run(api_mod.get_context_stats())["data"]["status"] == "removed"
    assert _run(api_mod.get_context_warnings())["data"]["warnings"] == []
    assert _run(api_mod.clear_context_warnings())["success"] is True


def test_health_check():
    api_mod._collector = MagicMock()
    api_mod._collector.collect_all.return_value = {"thinking": {"status": "healthy"}, "memory": {"status": "healthy"}}
    out = _run(api_mod.health_check())
    assert out["success"] is True
    assert out["data"]["healthy_modules"] == 2
    assert out["data"]["total_modules"] == 2


def test_health_check_degraded():
    api_mod._collector = MagicMock()
    api_mod._collector.collect_all.return_value = {"thinking": {"status": "error"}}
    out = _run(api_mod.health_check())
    assert out["data"]["status"] == "degraded"


# ── 模型/思维/安全状态 ──────────────────────────────────────────────────────

def test_get_thinking_status(monkeypatch):
    import modules.thinking.model_factory as mf_mod
    factory = MagicMock()
    factory.get_client.return_value = MagicMock()
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: factory)
    out = _run(api_mod.get_thinking_status())
    assert out["success"] is True
    assert out["data"]["status"] == "healthy"
    assert out["data"]["models"]["big"] is True


def test_get_thinking_status_unavailable(monkeypatch):
    import modules.thinking.model_factory as mf_mod
    monkeypatch.setattr(mf_mod, "get_model_factory", lambda: (_ for _ in ()).throw(RuntimeError("no factory")))
    out = _run(api_mod.get_thinking_status())
    assert out["data"]["status"] == "unavailable"


def test_get_security_status():
    out = _run(api_mod.get_security_status())
    assert out["success"] is True
    assert out["data"]["audit_enabled"] is True


# ── 会话 / 模型 / runner（mock 活跃会话为空）────────────────────────────────

def _patch_active_sessions(monkeypatch, sessions):
    import modules.thinking.multi_model_orchestrator as mmo
    monkeypatch.setattr(mmo, "get_active_sessions", lambda: sessions)
    return mmo


def test_get_sessions_empty(monkeypatch):
    _patch_active_sessions(monkeypatch, [])
    out = _run(api_mod.get_sessions())
    assert out["success"] is True
    assert out["data"]["total"] == 0


def test_get_sessions_with_session(monkeypatch):
    bb = MagicMock()
    entry = MagicMock()
    entry.to_dict.return_value = {"role": "user", "content": "hi"}
    bb.read_dialog.return_value = [entry]
    lifecycle = MagicMock()
    lifecycle.session_id = "s1"
    lifecycle.get.return_value = None
    session = {"session_id": "s1", "state": "active", "is_active": True, "turn_id": "t1", "blackboard": bb}
    _patch_active_sessions(monkeypatch, [session])
    out = _run(api_mod.get_sessions(dialog_limit=10))
    assert out["data"]["total"] == 1
    assert out["data"]["sessions"][0]["session_id"] == "s1"


def test_get_runners_empty(monkeypatch):
    _patch_active_sessions(monkeypatch, [])
    out = _run(api_mod.get_runners())
    assert out["success"] is True
    assert out["data"]["count"] == 0


def test_get_model_runners_empty(monkeypatch):
    _patch_active_sessions(monkeypatch, [])
    out = _run(api_mod.get_model_runners())
    assert out["success"] is True


def test_get_bus_stats(monkeypatch):
    import modules.thinking.communication.message_bus as mb
    bus = AsyncMock()
    bus.get_stats.return_value = {"messages": 10}
    bus.list_recipients.return_value = ["r1"]
    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    out = _run(api_mod.get_bus_stats(peek=False, peek_all=False))
    assert out["data"]["stats"] == {"messages": 10}
    assert out["data"]["recipients"] == ["r1"]


# ── 记忆端点（临时 EventStore 单例）────────────────────────────────────────

@pytest.fixture
def tmp_event_store(monkeypatch, tmp_path):
    from modules.memory.event_store import EventStore, MemoryEvent
    store = EventStore(
        db_path=str(tmp_path / "mem.db"),
        faiss_index_path=str(tmp_path / "mem.faiss"),
        id_map_path=str(tmp_path / "mem.json"),
    )
    monkeypatch.setattr(EventStore, "_instance", store)
    return store


def test_get_memory_full(tmp_event_store):
    from modules.memory.event_store import MemoryEvent
    tmp_event_store.save_event(MemoryEvent(fact="测试事件", importance=0.5))
    out = _run(api_mod.get_memory_full())
    assert out["data"]["event_system"] == "active"
    assert out["data"]["event_count"] >= 1


def test_list_events_filter(tmp_event_store):
    from modules.memory.event_store import MemoryEvent
    tmp_event_store.save_event(MemoryEvent(fact="需求变更", importance=0.8, keywords=["需求"]))
    tmp_event_store.save_event(MemoryEvent(fact="测试不足", importance=0.6, keywords=["测试"]))
    out = _run(api_mod.list_events(limit=50, type="fact", keyword=""))
    assert out["success"] is True
    assert out["data"]["total"] >= 2
    # 关键词过滤
    out2 = _run(api_mod.list_events(limit=50, type="", keyword="需求"))
    assert all("需求" in item["fact"] for item in out2["data"]["events"])


# ── 因果图端点（临时 CausalGraph/EventStore 单例）──────────────────────────

@pytest.fixture
def tmp_causal(tmp_path, monkeypatch):
    from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
    g = CausalGraph(db_path=str(tmp_path / "cg.db"))
    n1 = CausalNode(label="根因", node_type="cause")
    n2 = CausalNode(label="结果", node_type="effect")
    g.save_node(n1)
    g.save_node(n2)
    g.save_edge(CausalEdge(from_id=n1.id, to_id=n2.id))
    monkeypatch.setattr(CausalGraph, "_instance", g)
    from modules.memory.event_store import EventStore
    s = EventStore(
        db_path=str(tmp_path / "mem.db"),
        faiss_index_path=str(tmp_path / "mem.faiss"),
        id_map_path=str(tmp_path / "mem.json"),
    )
    monkeypatch.setattr(EventStore, "_instance", s)
    return g, n1, n2


def test_get_causal_graph(tmp_causal):
    g, n1, n2 = tmp_causal
    out = _run(api_mod.get_causal_graph(time_window=""))
    assert out["success"] is True
    assert out["data"]["stats"]["total_nodes"] == 2
    assert out["data"]["stats"]["total_edges"] == 1
    labels = [n["label"] for n in out["data"]["nodes"]]
    assert "根因" in labels and "结果" in labels


def test_get_causal_graph_metrics(tmp_causal):
    out = _run(api_mod.get_causal_graph_metrics())
    assert out["success"] is True
    assert "metrics" in out["data"]


def test_get_causal_node_detail(tmp_causal):
    g, n1, n2 = tmp_causal
    out = _run(api_mod.get_causal_node_detail(node_id=n1.id))
    assert out["success"] is True
    assert out["data"]["node"]["label"] == "根因"
    assert len(out["data"]["successors"]) == 1  # 根因 → 结果
    assert out["data"]["successors"][0]["label"] == "结果"


def test_get_causal_node_detail_not_found(tmp_causal):
    with pytest.raises(Exception):
        _run(api_mod.get_causal_node_detail(node_id="不存在"))


def test_get_causal_tree_from_node(tmp_causal):
    g, n1, n2 = tmp_causal
    out = _run(api_mod.get_causal_tree_from_node(node_id=n1.id, depth=3))
    assert out["success"] is True


# ── database / info-process / perception 端点 ───────────────────────────────

def test_get_database_info(monkeypatch):
    from modules.database.disk_cache import disk_cache
    monkeypatch.setattr(disk_cache, "get_stats", lambda: {"mode": "disk", "hits": 10, "misses": 2})
    out = _run(api_mod.get_database_info())
    assert out["success"] is True
    assert out["data"]["type"] == "sqlite"
    assert out["data"]["cache"]["hits"] == 10


def test_get_info_process_status(monkeypatch):
    import infra.data_process.core.image_analyzer as ia_mod
    import infra.data_process.core.speech_recognizer as sr_mod

    class FakeAnalyzer:
        model_type = "openai"
        _initialized = True
        local_model = None

    class FakeRecognizer:
        model_name = "whisper"
        _initialized = False

    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda: FakeAnalyzer())
    monkeypatch.setattr(sr_mod, "SpeechRecognizer", lambda: FakeRecognizer())
    out = _run(api_mod.get_info_process_status())
    assert out["success"] is True
    assert out["data"]["image_analyzer"]["type"] == "openai"
    assert out["data"]["speech_recognizer"]["model"] == "whisper"


def test_get_perception_full(monkeypatch):
    import modules.perception as perc_mod
    ps = MagicMock()
    ps._started = True
    ps.get_status.return_value = {"pipeline": "ok", "voice_available": True, "world_state": {}, "event_bus": {}}
    monkeypatch.setattr(perc_mod, "get_perception_system", lambda: ps)
    out = _run(api_mod.get_perception_full())
    assert out["success"] is True
    assert out["data"]["status"] == "running"


# ── memory 事件 CRUD / clear / tool-skills ──────────────────────────────────

def test_create_and_get_event(tmp_event_store):
    out = _run(api_mod.create_event(fact="测试事实", keywords="测试,重要", importance=0.8, event_type="fact", thought="", lesson=""))
    assert out["success"] is True
    eid = out["data"]["id"]
    got = _run(api_mod.get_event(event_id=eid))
    assert got["data"]["fact"] == "测试事实"
    assert got["data"]["keywords"] == ["测试", "重要"]
    assert got["data"]["importance"] == 0.8


def test_get_event_not_found(tmp_event_store):
    with pytest.raises(Exception):
        _run(api_mod.get_event(event_id="不存在"))


def test_update_event(tmp_event_store):
    from modules.memory.event_store import MemoryEvent
    eid = tmp_event_store.save_event(MemoryEvent(fact="旧", importance=0.3))
    out = _run(api_mod.update_event(event_id=eid, fact="新事实", keywords="新词", importance=0.9, event_type=None))
    assert out["success"] is True
    got = _run(api_mod.get_event(event_id=eid))
    assert got["data"]["fact"] == "新事实"
    assert got["data"]["importance"] == 0.9


def test_delete_event(tmp_event_store):
    from modules.memory.event_store import MemoryEvent
    eid = tmp_event_store.save_event(MemoryEvent(fact="删除我"))
    out = _run(api_mod.delete_event(event_id=eid))
    assert out["success"] is True
    with pytest.raises(Exception):
        _run(api_mod.get_event(event_id=eid))


def test_clear_memory(tmp_event_store):
    from modules.memory.event_store import MemoryEvent
    tmp_event_store.save_event(MemoryEvent(fact="要清空"))
    out = _run(api_mod.clear_memory(scope="all"))
    assert out["success"] is True
    assert _run(api_mod.list_events(limit=50, type="", keyword=""))["data"]["total"] == 0


def test_tool_skills_removed_endpoints():
    assert _run(api_mod.get_tool_skills())["success"] is True
    assert "已废弃" in _run(api_mod.record_tool_success(tool_name="calc"))["data"]["message"]
    assert _run(api_mod.record_tool_failure(tool_name="calc"))["success"] is True
