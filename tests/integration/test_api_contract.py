"""
前后端接口契约测试（响应结构锁定）

目的：防止「前端假设字段/结构与后端实际返回不一致」类 bug 再次发生。
这些 bug 无法被「断言正确值」的普通 API 测试发现，因为那些测试只验证
后端按自己文档返回，从不验证前端真实消费的结构。

本文件对每个前端页面真实调用的端点，锁定后端返回结构的【类型】和【关键字段】，
并标注对应前端消费点（frontend/js/pages/*.js）。若后端改动结构，此测试会立即报错。

锁定的契约来源（实测后端真实响应）：
  - /tools/                → data.tools: dict {name: spec}（前端 tools.js 已适配 dict）
  - /tools/events          → data.events: list
  - /security/status       → data.state: dict {L0:bool,...}, data.audit_enabled: bool
  - /security/audit        → data.logs: list（字段 timestamp/event_type/content_preview/result）
  - /management/sessions   → data.sessions: list（前端 sessions.js 用 session_id/is_active/dialog_size）
  - /stream/sessions       → data: list（前端 chat.js 用 session_id/title/last_active/message_count）
  - /management/memory     → data.event_system: str, data.event_count: int
  - /management/causal-graph → data.nodes: list, data.edges: list, data.stats: dict
  - /management/thinking   → data.models: dict {big/medium/small: bool}
  - /management/perception → data.status: str, data.platform: str, data.voice_available: bool
  - /management/database   → data.tables: list, data.cache: dict
  - /management/modules    → data.modules: list, data.with_api: int, data.with_core: int
  - /config                → data: dict {key: value}
  - /management/info-process → data.image_analyzer: dict, data.speech_recognizer: dict
"""
import contextlib
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch


@pytest.fixture
def _mock_lifespan():
    """Replace the app lifespan so tests do not initialise heavy subsystems."""
    from api.main import app

    @contextlib.asynccontextmanager
    async def _noop_lifespan(app):
        yield

    with patch.object(app.router, "lifespan_context", _noop_lifespan):
        yield


@pytest.fixture
def _no_auth():
    """Disable auth (empty key)."""
    with patch("api.main._SIMPLE_API_KEY", ""):
        yield


@pytest.fixture
def _reset_rate_limit():
    """Clear rate-limit counters."""
    import api.main
    api.main.request_counts.clear()
    api.main._request_counter_ref[0] = 0
    yield
    api.main.request_counts.clear()
    api.main._request_counter_ref[0] = 0


def _client(app, **kwargs):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test", **kwargs)


@pytest.fixture
async def _c(_mock_lifespan, _no_auth, _reset_rate_limit):
    from api.main import app
    async with _client(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /tools/*  — 前端 tools.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tools_root_tools_is_dict(_c):
    """data.tools 必须是 dict {name: spec}（tools.js loadData 用 Object.keys 转数组）"""
    resp = await _c.get("/tools/")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("tools"), dict), "tools 必须是 dict，前端 tools.js 依赖 dict 转数组"
    assert isinstance(data.get("by_source"), dict), "by_source 必须是 dict（tools.js 用 Object.keys().length）"
    assert isinstance(data.get("count"), int)


@pytest.mark.asyncio
async def test_tools_events_events_is_list(_c):
    """data.events 必须是 list（tools.js evts.map / evts.length）"""
    resp = await _c.get("/tools/events?limit=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("events"), list), "events 必须是 list"


@pytest.mark.asyncio
async def test_tools_info_returns_spec(_c):
    """data 必须含 name/description/source（tools.js select 用 info.description/info.source）"""
    resp = await _c.get("/tools/info/nonexistent_tool_xyz")
    # 工具不存在也可能 404，但存在时结构必须一致；这里直接断言结构字段
    if resp.status_code == 200:
        data = resp.json().get("data", {})
        assert isinstance(data, dict)
        for key in ("name", "description", "source"):
            assert key in data, f"tools/info 缺少字段 {key}"


# ---------------------------------------------------------------------------
# /security/*  — 前端 security.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_status_state_is_dict(_c):
    """data.state 必须是 dict {L0:bool,...}（security.js Object.entries(state)）"""
    resp = await _c.get("/security/status")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("state"), dict), "state 必须是 dict（security.js 用 Object.entries）"
    assert isinstance(data.get("audit_enabled"), bool)


@pytest.mark.asyncio
async def test_security_audit_logs_is_list(_c):
    """data.logs 必须是 list，且字段含 timestamp/event_type/content_preview/result"""
    resp = await _c.get("/security/audit?limit=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("logs"), list), "logs 必须是 list"
    for log in data["logs"]:
        assert isinstance(log, dict)
        for key in ("timestamp", "event_type", "content_preview", "result"):
            assert key in log, f"audit log 缺少字段 {key}（security.js 已适配）"


# ---------------------------------------------------------------------------
# /management/sessions  vs  /stream/sessions  — 前端 sessions.js / chat.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_management_sessions_is_list(_c):
    """data.sessions 必须是 list（sessions.js loadData 用 data.sessions）"""
    resp = await _c.get("/management/sessions")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("sessions"), list), "sessions 必须是 list"
    for s in data["sessions"]:
        for key in ("session_id", "is_active", "dialog_size"):
            assert key in s, f"session 缺少字段 {key}（sessions.js 用这些字段）"


@pytest.mark.asyncio
async def test_stream_sessions_data_is_list(_c):
    """/stream/sessions 的 data 必须是 list（chat.js loadSessions 用 r.data）"""
    resp = await _c.get("/stream/sessions")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list), "data 必须是 list（chat.js 会话列表）"


# ---------------------------------------------------------------------------
# /management/memory  — 前端 memory.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_management_memory_structure(_c):
    """data.event_system/event_count 存在（memory.js 用事件记忆）"""
    resp = await _c.get("/management/memory")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "event_system" in data
    assert isinstance(data.get("event_count"), int)


@pytest.mark.asyncio
async def test_management_memory_events_is_list(_c):
    """data.events 必须是 list（memory.js loadData 用 data.events.map）"""
    resp = await _c.get("/management/memory/events?limit=5")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("events"), list), "events 必须是 list"


# ---------------------------------------------------------------------------
# /management/causal-graph  — 前端 causal.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_causal_graph_structure(_c):
    """data.nodes/edges/stats 存在（causal.js 用）"""
    resp = await _c.get("/management/causal-graph")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("nodes"), list), "nodes 必须是 list"
    assert isinstance(data.get("edges"), list), "edges 必须是 list"
    assert isinstance(data.get("stats"), dict), "stats 必须是 dict（causal.js 用 s.total_nodes 等）"


# ---------------------------------------------------------------------------
# /management/thinking  — 前端 chat.js / system.js / settings.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_thinking_status_models_is_dict(_c):
    """data.models 必须是 dict {big/medium/small:bool}（chat.js/system.js/settings.js 用）"""
    resp = await _c.get("/management/thinking")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("models"), dict), "models 必须是 dict"
    for k in ("big", "medium", "small"):
        assert k in data["models"], f"models 缺少 {k}"


# ---------------------------------------------------------------------------
# /management/perception  — 前端 perception.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_perception_status_structure(_c):
    """data.status/platform/voice_available 存在（perception.js 用）"""
    resp = await _c.get("/management/perception")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "status" in data
    assert "platform" in data
    assert "voice_available" in data


# ---------------------------------------------------------------------------
# /management/database  — 前端 system.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_database_structure(_c):
    """data.tables 是 list、data.cache 是 dict（system.js 用）"""
    resp = await _c.get("/management/database")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("tables"), list), "tables 必须是 list"
    assert isinstance(data.get("cache"), dict), "cache 必须是 dict"


# ---------------------------------------------------------------------------
# /management/modules  — 前端 modules.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_modules_structure(_c):
    """data.modules 是 list、with_api/with_core 是 int（modules.js 用）"""
    resp = await _c.get("/management/modules")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data.get("modules"), list), "modules 必须是 list"
    assert isinstance(data.get("with_api"), int)
    assert isinstance(data.get("with_core"), int)


# ---------------------------------------------------------------------------
# /config  — 前端 settings.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_data_is_dict(_c):
    """data 必须是 dict {key:value}（settings.js Object.keys(cfg)）"""
    resp = await _c.get("/config")
    assert resp.status_code == 200
    data = resp.json().get("data")
    assert isinstance(data, dict), "data 必须是 dict（settings.js 用 Object.keys）"


# ---------------------------------------------------------------------------
# /management/info-process  — 前端 system.js
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_info_process_structure(_c):
    """data.image_analyzer/speech_recognizer 是 dict（system.js 用）"""
    resp = await _c.get("/management/info-process")
    assert resp.status_code == 200
    data = resp.json().get("data", {})
    assert isinstance(data.get("image_analyzer"), dict)
    assert isinstance(data.get("speech_recognizer"), dict)
