"""management/api 端点补充测试（第二批）：错误分支 / 失败降级 / 剩余端点"""
import asyncio
import os
import shutil
import subprocess
import builtins
from unittest.mock import MagicMock

import pytest

import modules.management.api as api_mod


def _run(coro):
    return asyncio.run(coro)


# ── api-requests / dashboard 降级 ───────────────────────────────────────────

def test_recent_api_requests_store_error(monkeypatch):
    """ApiLogStore 初始化/查询失败 → _recent_api_requests 返回 []"""
    import modules.management.api_log_store as ls_mod

    def boom():
        raise RuntimeError("no store")

    monkeypatch.setattr(ls_mod.ApiLogStore, "get_instance", staticmethod(boom))
    assert api_mod._recent_api_requests() == []


# ── 模块管理：未找到 / 刷新 ─────────────────────────────────────────────────

def test_get_module_detail_not_found(monkeypatch):
    r = MagicMock()
    r.get_module.return_value = None
    monkeypatch.setattr(api_mod, "_registry", r)
    with pytest.raises(Exception):
        _run(api_mod.get_module_detail("nope"))


def test_refresh_module(monkeypatch):
    mod = MagicMock()
    mod.last_check = 0
    r = MagicMock()
    r.get_module.return_value = mod
    monkeypatch.setattr(api_mod, "_registry", r)
    out = _run(api_mod.refresh_module("thinking"))
    assert out["success"] is True
    assert mod.last_check > 0
    r.get_module.return_value = None
    with pytest.raises(Exception):
        _run(api_mod.refresh_module("nope"))


# ── todos ───────────────────────────────────────────────────────────────────

def test_set_todo_status_not_found(tmp_path, monkeypatch):
    import infra.tool_manager.tools.todo as todo_mod

    monkeypatch.setattr(todo_mod, "_todos_path", lambda sid: str(tmp_path / f"{sid}.json"))
    todo_mod._save_todos([{"id": "other"}], "s1")
    out = _run(api_mod.set_todo_status("t1", {"status": "completed"}, session_id="s1"))
    assert out["success"] is False
    assert out["error"]["code"] == "NOT_FOUND"


# ── 编排 / 强制技能 / 角色技能 ─────────────────────────────────────────────

def test_get_orchestration_custom_agents(monkeypatch):
    import config.prompts.loader as loader_mod
    import importlib

    cs = importlib.import_module("config.settings")
    settings = MagicMock()
    settings.get_custom_agents.return_value = [
        {"role": "custom1", "tier": "expert", "name": "自定义"},
        {"role": "", "tier": "large", "name": "无角色"},  # 空 role → 跳过
    ]
    settings.get_persona.return_value = "persona"
    settings.get_system_override.return_value = ""
    settings.get_role_tools.return_value = {}
    settings.get_model_params.return_value = {}
    settings.get_agent_active.return_value = True
    monkeypatch.setattr(cs, "settings", settings)

    loader = MagicMock()
    loader.load.return_value = {"roles": {}}
    monkeypatch.setattr(loader_mod, "get_loader", lambda: loader)

    out = _run(api_mod.get_orchestration())
    agents = out["data"]["agents"]
    assert any(a["role"] == "custom1" and a["is_custom"] for a in agents)
    assert not any(a["role"] == "" for a in agents)


def test_get_forced_skill(monkeypatch):
    import importlib

    cs = importlib.import_module("config.settings")
    import modules.thinking.skills as skills_mod

    settings = MagicMock()
    settings.get_forced_skill.return_value = "s1"
    monkeypatch.setattr(cs, "settings", settings)

    skill = MagicMock()
    skill.id = "s1"
    skill.name = "技能"
    skill.description = "描述" * 50  # 触发 description[:120] 截断
    mgr = MagicMock()
    mgr.get_skill.return_value = skill
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)

    out = _run(api_mod.get_forced_skill())
    assert out["data"]["forced_skill"] == "s1"
    assert out["data"]["skill"]["id"] == "s1"

    # skill 存在但 get_skill 返回 None
    mgr.get_skill.return_value = None
    out2 = _run(api_mod.get_forced_skill())
    assert out2["data"]["skill"] is None

    # 未设置强制技能
    settings.get_forced_skill.return_value = ""
    out3 = _run(api_mod.get_forced_skill())
    assert out3["data"]["forced_skill"] == ""
    assert out3["data"]["skill"] is None


def test_set_forced_skill(monkeypatch):
    import importlib

    cs = importlib.import_module("config.settings")
    import modules.thinking.skills as skills_mod

    settings = MagicMock()
    settings.set_forced_skill.return_value = "s1"
    monkeypatch.setattr(cs, "settings", settings)

    skill = MagicMock()
    skill.id = "s1"
    skill.name = "技能"
    skill.enabled = True
    mgr = MagicMock()
    mgr.get_skill.return_value = skill
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)

    out = _run(api_mod.set_forced_skill({"skill_id": "s1"}))
    assert out["success"] is True
    assert out["data"]["forced_skill"] == "s1"
    settings.set_forced_skill.assert_called_with("s1")

    # 清除
    out2 = _run(api_mod.set_forced_skill({"skill_id": ""}))
    assert out2["success"] is True
    settings.set_forced_skill.assert_called_with("")

    # 技能不存在
    mgr.get_skill.return_value = None
    out3 = _run(api_mod.set_forced_skill({"skill_id": "s1"}))
    assert out3["success"] is False
    assert out3["error"]["code"] == "SKILL_NOT_FOUND"

    # 技能已禁用
    skill.enabled = False
    mgr.get_skill.return_value = skill
    out4 = _run(api_mod.set_forced_skill({"skill_id": "s1"}))
    assert out4["success"] is False
    assert out4["error"]["code"] == "SKILL_DISABLED"


def test_skill_mutation_failures(monkeypatch):
    import modules.thinking.skills as skills_mod

    mgr = MagicMock()
    mgr.create_skill.return_value = (False, "bad id")
    mgr.update_skill.return_value = (False, "no skill")
    mgr.set_enabled.return_value = (False, "no skill")
    mgr.delete_skill.return_value = (False, "protected")
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)

    assert _run(api_mod.create_skill({"id": "", "name": ""}))["success"] is False
    assert _run(api_mod.update_skill("s1", {"name": "x"}))["success"] is False
    assert _run(api_mod.set_skill_enabled("s1", {"enabled": True}))["success"] is False
    assert _run(api_mod.delete_skill("s1"))["success"] is False


def test_role_skills(monkeypatch):
    import importlib

    cs = importlib.import_module("config.settings")
    settings = MagicMock()
    settings.get_role_skills.return_value = ["*"]
    monkeypatch.setattr(cs, "settings", settings)

    out = _run(api_mod.get_role_skills("orchestrator"))
    assert out["data"]["skills"] == ["*"]

    settings.set_role_skills.return_value = ["a", "b"]
    settings.get_role_skills.return_value = ["a", "b"]
    out2 = _run(api_mod.update_role_skills("orchestrator", {"skills": ["a", "b"]}))
    assert out2["data"]["skills"] == ["a", "b"]

    # skills 非数组 → 校验失败
    out3 = _run(api_mod.update_role_skills("orchestrator", {"skills": "not-a-list"}))
    assert out3["success"] is False
    assert out3["error"]["code"] == "VALIDATION_ERROR"


def test_orchestration_preview(monkeypatch):
    import config.prompts.composer as composer_mod
    import importlib

    cs = importlib.import_module("config.settings")
    settings = MagicMock()
    settings.effective_execution_mode = "edit"
    monkeypatch.setattr(cs, "settings", settings)

    composer = MagicMock()
    composer.build_system.return_value = "SYSTEM PROMPT"
    monkeypatch.setattr(composer_mod, "PromptComposer", lambda: composer)

    out = _run(api_mod.orchestration_preview({"role": "orchestrator", "tier": "large"}))
    assert out["success"] is True
    assert out["data"]["prompt"] == "SYSTEM PROMPT"

    # 组装失败 → PREVIEW_ERROR
    composer.build_system.side_effect = RuntimeError("build fail")
    out2 = _run(api_mod.orchestration_preview({}))
    assert out2["success"] is False
    assert out2["error"]["code"] == "PREVIEW_ERROR"


# ── open-folder / vision-models / install-voice-deps ────────────────────────

def test_open_folder(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))

    out = _run(api_mod.open_folder({"folder": "pet"}))
    assert out["success"] is True
    assert calls

    out2 = _run(api_mod.open_folder({"folder": "bogus"}))
    assert out2["success"] is False
    assert out2["error"]["code"] == "BAD_FOLDER"

    # Popen 抛错 → OPEN_FAILED
    def boom(*a, **k):
        raise OSError("no open")

    monkeypatch.setattr(subprocess, "Popen", boom)
    out3 = _run(api_mod.open_folder({"folder": "data"}))
    assert out3["success"] is False
    assert out3["error"]["code"] == "OPEN_FAILED"


def test_list_vision_models():
    vision_dir = os.path.join(api_mod.PROJECT_ROOT, "data", "models", "vision")
    os.makedirs(vision_dir, exist_ok=True)
    prefix = f"_cov_test_{os.getpid()}_"
    created_dirs = []
    created_files = []
    try:
        model_a = os.path.join(vision_dir, prefix + "model_a")
        model_b = os.path.join(vision_dir, prefix + "model_b")
        model_bad = os.path.join(vision_dir, prefix + "model_bad")
        empty_dir = os.path.join(vision_dir, prefix + "empty_dir")  # 无 config → 跳过
        readme = os.path.join(vision_dir, prefix + "readme.txt")
        os.makedirs(model_a)
        os.makedirs(model_b)
        os.makedirs(model_bad)
        os.makedirs(empty_dir)
        created_dirs = [model_a, model_b, model_bad, empty_dir]
        with open(os.path.join(model_a, "config.json"), "w") as f:
            f.write('{"model_type": "bert"}')
        with open(os.path.join(model_b, "preprocessor_config.json"), "w") as f:
            f.write("{}")
        with open(os.path.join(model_bad, "config.json"), "w") as f:
            f.write("{not json}")  # json 解析失败 → model_type 空
        with open(readme, "w") as f:
            f.write("hi")
        created_files = [readme]

        out = _run(api_mod.list_vision_models())
        names = {m["name"] for m in out["data"]["models"]}
        assert prefix + "model_a" in names
        assert prefix + "model_b" in names
        assert prefix + "model_bad" in names
        assert prefix + "empty_dir" not in names
        assert prefix + "readme.txt" not in names
    finally:
        for d in created_dirs:
            shutil.rmtree(d, ignore_errors=True)
        for f in created_files:
            if os.path.exists(f):
                os.remove(f)


def test_install_voice_deps(monkeypatch):
    import types

    real_import = builtins.__import__
    installed = {"speech_recognition"}  # 仅 SpeechRecognition 已安装

    def fake_import(name, *args, **kwargs):
        if name in ("speech_recognition", "pyaudio", "whisper", "pynput"):
            if name in installed:
                return types.ModuleType(name)
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    class FakeProc:
        def __init__(self, code, err=""):
            self.returncode = code
            self.stderr = err

    # 第 1 轮：already_installed / installed(验证通过) / pip 失败 else / subprocess 异常
    def fake_run1(cmd, **kw):
        pkg = cmd[-2]
        if pkg == "openai-whisper":
            installed.add("whisper")  # 安装成功且 import 验证通过
            return FakeProc(0, "ok")
        if pkg == "pyaudio":
            return FakeProc(1, "pip failed")  # pip 安装失败 → 走 else 分支
        raise RuntimeError("subprocess boom")  # pynput：subprocess 异常

    monkeypatch.setattr(subprocess, "run", fake_run1)
    out = _run(api_mod.install_voice_deps())
    statuses = {r["package"]: r["status"] for r in out["data"]["results"]}
    assert statuses["SpeechRecognition"] == "already_installed"
    assert statuses["openai-whisper"] == "installed"
    assert statuses["pyaudio"] == "install_failed"
    assert statuses["pynput"] == "error"
    assert out["success"] is False

    # 第 2 轮：pip 成功但 import 验证失败 → except ImportError
    def fake_run2(cmd, **kw):
        pkg = cmd[-2]
        if pkg in ("openai-whisper", "pyaudio"):
            return FakeProc(0, "ok")
        raise RuntimeError("subprocess boom")

    monkeypatch.setattr(subprocess, "run", fake_run2)
    out2 = _run(api_mod.install_voice_deps())
    statuses2 = {r["package"]: r["status"] for r in out2["data"]["results"]}
    assert statuses2["pyaudio"] == "install_failed"
    assert statuses2["openai-whisper"] == "already_installed"


# ── 记忆事件：更新/删除未找到 ──────────────────────────────────────────────

@pytest.fixture
def tmp_event_store(monkeypatch, tmp_path):
    from modules.memory.event_store import EventStore

    store = EventStore(
        db_path=str(tmp_path / "mem.db"),
        faiss_index_path=str(tmp_path / "mem.faiss"),
        id_map_path=str(tmp_path / "mem.json"),
    )
    monkeypatch.setattr(EventStore, "_instance", store)
    return store


def test_update_event_not_found(tmp_event_store):
    with pytest.raises(Exception):
        _run(api_mod.update_event(event_id="不存在", fact="x"))


def test_delete_event_not_found(tmp_event_store):
    with pytest.raises(Exception):
        _run(api_mod.delete_event(event_id="不存在"))


# ── 因果图：关联事件 / 未找到 / what-if ───────────────────────────────────

@pytest.fixture
def tmp_causal(tmp_path, monkeypatch):
    from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
    from modules.memory.event_store import EventStore

    g = CausalGraph(db_path=str(tmp_path / "cg.db"))
    n1 = CausalNode(label="根因", node_type="cause")
    n2 = CausalNode(label="结果", node_type="effect")
    g.save_node(n1)
    g.save_node(n2)
    g.save_edge(CausalEdge(from_id=n1.id, to_id=n2.id))
    monkeypatch.setattr(CausalGraph, "_instance", g)

    s = EventStore(
        db_path=str(tmp_path / "mem.db"),
        faiss_index_path=str(tmp_path / "mem.faiss"),
        id_map_path=str(tmp_path / "mem.json"),
    )
    monkeypatch.setattr(EventStore, "_instance", s)
    return g, n1, n2


def test_get_causal_graph_linked_events(tmp_causal):
    from modules.memory.event_store import EventStore, MemoryEvent

    g, n1, n2 = tmp_causal
    store = EventStore.get_instance()
    store.save_event(MemoryEvent(fact="关联事件", causal_node_ids=[n1.id]))
    out = _run(api_mod.get_causal_graph(time_window=""))
    assert out["data"]["stats"]["linked_events"] == 1


def test_get_causal_graph_list_error(tmp_causal):
    from modules.memory.event_store import EventStore

    g, n1, n2 = tmp_causal
    store = EventStore.get_instance()

    def boom(*a, **k):
        raise RuntimeError("list fail")

    store.list_events = boom
    out = _run(api_mod.get_causal_graph(time_window=""))
    assert out["data"]["stats"]["linked_events"] == 0


def test_get_causal_node_detail_linked_events(tmp_causal):
    from modules.memory.event_store import EventStore, MemoryEvent

    g, n1, n2 = tmp_causal
    store = EventStore.get_instance()
    store.save_event(MemoryEvent(fact="关联", causal_node_ids=[n1.id]))
    store.save_event(MemoryEvent(fact="无关", causal_node_ids=[]))  # 未关联 → 跳过
    out = _run(api_mod.get_causal_node_detail(node_id=n1.id))
    assert out["data"]["linked_events"][0]["fact"] == "关联"
    assert len(out["data"]["linked_events"]) == 1


def test_get_causal_tree_node_not_found(tmp_causal):
    with pytest.raises(Exception):
        _run(api_mod.get_causal_tree_from_node(node_id="nope"))


def test_get_causal_what_if(tmp_causal):
    g, n1, n2 = tmp_causal
    out = _run(
        api_mod.get_causal_what_if(
            node_id=n1.id, target_node_id=n2.id, relation="causes", confidence=0.8, depth=3
        )
    )
    assert out["success"] is True
    assert out["data"]["hypothetical_edge"]["relation"] == "causes"
    assert isinstance(out["data"]["chains"], list)

    with pytest.raises(Exception):
        _run(api_mod.get_causal_what_if(node_id="nope", target_node_id=n2.id, relation="causes"))
    with pytest.raises(Exception):
        _run(api_mod.get_causal_what_if(node_id=n1.id, target_node_id="nope", relation="causes"))


# ── 感知 / 数据库 / 信息处理 / 安全 ────────────────────────────────────────

def test_get_perception_full(monkeypatch):
    import modules.perception as perc_mod

    ps = MagicMock()
    ps._started = True
    ps.get_status.return_value = {"pipeline": "ok", "voice_available": True, "world_state": {}, "event_bus": {}}
    monkeypatch.setattr(perc_mod, "get_perception_system", lambda: ps)
    out = _run(api_mod.get_perception_full())
    assert out["success"] is True
    assert out["data"]["status"] == "running"


def test_get_perception_full_no_file_perception(monkeypatch):
    """无 file_perception 子系统 → watch_paths 为空"""
    import modules.perception as perc_mod

    ps = MagicMock()
    ps._started = True
    ps.file_perception = None
    ps.get_status.return_value = {}
    monkeypatch.setattr(perc_mod, "get_perception_system", lambda: ps)
    out = _run(api_mod.get_perception_full())
    assert out["data"]["watch_paths"] == []


def test_get_perception_full_error(monkeypatch):
    import modules.perception as perc_mod

    monkeypatch.setattr(
        perc_mod, "get_perception_system", lambda: (_ for _ in ()).throw(RuntimeError("fail"))
    )
    with pytest.raises(Exception):
        _run(api_mod.get_perception_full())


def test_start_perception(monkeypatch):
    import modules.perception as perc_mod

    mgr = MagicMock()
    monkeypatch.setattr(perc_mod, "perception_manager", mgr)
    out = _run(api_mod.start_perception())
    assert out["success"] is True
    mgr.start_monitoring.assert_called_once()


def test_start_perception_error(monkeypatch):
    import modules.perception as perc_mod

    mgr = MagicMock()
    mgr.start_monitoring.side_effect = RuntimeError("fail")
    monkeypatch.setattr(perc_mod, "perception_manager", mgr)
    with pytest.raises(Exception):
        _run(api_mod.start_perception())


def test_stop_perception(monkeypatch):
    import modules.perception as perc_mod

    mgr = MagicMock()
    monkeypatch.setattr(perc_mod, "perception_manager", mgr)
    out = _run(api_mod.stop_perception())
    assert out["success"] is True
    mgr.stop_monitoring.assert_called_once()


def test_stop_perception_error(monkeypatch):
    import modules.perception as perc_mod

    mgr = MagicMock()
    mgr.stop_monitoring.side_effect = RuntimeError("fail")
    monkeypatch.setattr(perc_mod, "perception_manager", mgr)
    with pytest.raises(Exception):
        _run(api_mod.stop_perception())


def test_clear_perception():
    out = _run(api_mod.clear_perception())
    assert out["success"] is True


def test_get_database_info_with_tables(monkeypatch, tmp_path):
    import sqlite3

    from modules.database.disk_cache import disk_cache

    db = tmp_path / "data" / "memory.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(api_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(disk_cache, "get_stats", lambda: {"mode": "disk", "hits": 0, "misses": 0})
    out = _run(api_mod.get_database_info())
    assert out["success"] is True
    assert out["data"]["tables"][0]["name"] == "foo"
    assert out["data"]["tables"][0]["columns"] == ["id"]


def test_get_database_info_no_db(monkeypatch, tmp_path):
    from modules.database.disk_cache import disk_cache

    monkeypatch.setattr(api_mod, "PROJECT_ROOT", tmp_path / "nonexistent")
    monkeypatch.setattr(disk_cache, "get_stats", lambda: {"mode": "disk", "hits": 1, "misses": 2})
    out = _run(api_mod.get_database_info())
    assert out["success"] is True
    assert out["data"]["tables"] == []


def test_get_database_info_error(monkeypatch):
    from modules.database.disk_cache import disk_cache

    monkeypatch.setattr(disk_cache, "get_stats", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(Exception):
        _run(api_mod.get_database_info())


def test_get_info_process_error(monkeypatch):
    import infra.data_process.core.image_analyzer as ia_mod

    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    with pytest.raises(Exception):
        _run(api_mod.get_info_process_status())


def test_get_security_status_unavailable(monkeypatch):
    import modules.security_system.audit_logger as al_mod

    monkeypatch.setattr(
        al_mod, "SecurityAuditLogger", lambda: (_ for _ in ()).throw(RuntimeError("no"))
    )
    out = _run(api_mod.get_security_status())
    assert out["data"]["status"] == "unavailable"


# ── 会话 / 模型 runner / 总线 ──────────────────────────────────────────────

def _patch_active_sessions(monkeypatch, sessions):
    import modules.thinking.multi_model_orchestrator as mmo

    monkeypatch.setattr(mmo, "get_active_sessions", lambda: sessions)
    return mmo


def test_get_sessions_error(monkeypatch):
    import modules.thinking.multi_model_orchestrator as mmo

    monkeypatch.setattr(mmo, "get_active_sessions", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(Exception):
        _run(api_mod.get_sessions())


def test_get_model_runners_with_sessions(monkeypatch):
    import modules.thinking.core.model_runner as mr

    lifecycle = MagicMock()
    lifecycle.session_id = "s1"
    _patch_active_sessions(monkeypatch, [lifecycle])

    rm = MagicMock()
    rm.list_runners.return_value = [
        {"tier": "large", "name": "r1"},
        {"tier": "expert", "name": "r2"},
        {"tier": "weird", "name": "r3"},
        {"tier": "large", "name": "r4"},  # 重复 tier → 累加
    ]
    monkeypatch.setattr(mr, "get_runner_manager", lambda sid: rm)
    out = _run(api_mod.get_model_runners())
    assert out["data"]["summary"]["large"]["active"] == 2
    assert out["data"]["summary"]["weird"]["max"] == 8  # 未知 tier 默认 8


def test_get_model_runners_no_manager(monkeypatch):
    import modules.thinking.core.model_runner as mr

    lifecycle = MagicMock()
    lifecycle.session_id = "s1"
    _patch_active_sessions(monkeypatch, [lifecycle])
    monkeypatch.setattr(mr, "get_runner_manager", lambda sid: None)
    out = _run(api_mod.get_model_runners())
    assert out["success"] is True
    assert out["data"]["runners"] == []


def test_get_model_runners_error(monkeypatch):
    import modules.thinking.multi_model_orchestrator as mmo

    monkeypatch.setattr(mmo, "get_active_sessions", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    out = _run(api_mod.get_model_runners())
    assert out["success"] is True
    assert out["data"]["runners"] == []


def test_get_session_dialog(monkeypatch):
    bb = MagicMock()
    entry = MagicMock()
    entry.to_dict.return_value = {"role": "user", "content": "hi"}
    bb.read_dialog.return_value = [entry]
    lifecycle = MagicMock()
    lifecycle.session_id = "s1"
    lifecycle.blackboard = bb
    other = MagicMock()
    other.session_id = "s0"  # 不匹配 → 跳过
    _patch_active_sessions(monkeypatch, [other, lifecycle])

    out = _run(api_mod.get_session_dialog("s1", limit=10))
    assert out["data"]["dialog_size"] == 1
    assert out["data"]["dialog"][0]["role"] == "user"


def test_get_session_dialog_not_found(monkeypatch):
    _patch_active_sessions(monkeypatch, [])
    with pytest.raises(Exception):
        _run(api_mod.get_session_dialog("nope"))


def test_get_session_dialog_error(monkeypatch):
    import modules.thinking.multi_model_orchestrator as mmo

    monkeypatch.setattr(mmo, "get_active_sessions", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(Exception):
        _run(api_mod.get_session_dialog("s1"))


def test_get_runners_with_sessions(monkeypatch):
    import modules.thinking.core.model_runner as mr

    lifecycle1 = MagicMock()
    lifecycle1.session_id = "s1"
    lifecycle2 = MagicMock()
    lifecycle2.session_id = "s2"
    lifecycle3 = MagicMock()
    lifecycle3.session_id = "s3"
    _patch_active_sessions(monkeypatch, [lifecycle1, lifecycle2, lifecycle3])

    rm = MagicMock()
    rm.list_runners.return_value = [
        {"model_id": "m1", "identity_key": "k", "tier": "large", "role": "r", "status": "active"},
        "not-a-dict",  # 非 dict → 跳过
    ]

    class _NoListRunners:
        pass

    bad_rm = MagicMock()
    bad_rm.list_runners.side_effect = RuntimeError("boom")  # 读取 runner 失败 → 降级跳过

    def _rm(sid):
        if sid == "s2":
            return None
        if sid == "s3":
            return bad_rm
        return rm

    monkeypatch.setattr(mr, "get_runner_manager", _rm)
    out = _run(api_mod.get_runners())
    assert out["data"]["count"] == 1
    assert out["data"]["runners"][0]["session_id"] == "s1"

    # 无 list_runners 属性的 manager
    monkeypatch.setattr(mr, "get_runner_manager", lambda sid: _NoListRunners())
    _patch_active_sessions(monkeypatch, [lifecycle1])
    out2 = _run(api_mod.get_runners())
    assert out2["data"]["count"] == 0


def test_get_runners_error(monkeypatch):
    import modules.thinking.multi_model_orchestrator as mmo

    monkeypatch.setattr(mmo, "get_active_sessions", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(Exception):
        _run(api_mod.get_runners())


class _FakeBus:
    def __init__(self, recipients=("r1", "r2")):
        self._recipients = list(recipients)
        self._queue = {"r1": [MagicMock()]}
        self._queue["r1"][0].to_dict.return_value = {"id": 1}
        self.stats = {"messages": 5}

    async def get_stats(self):
        return self.stats

    async def list_recipients(self):
        return self._recipients

    def peek_all(self):
        return self._queue

    async def peek(self, recipient_id, limit=20):
        return self._queue.get(recipient_id, [])


class _MinimalBus:
    """无 peek/peek_all 方法的 bus → hasattr 分支为 False"""

    def __init__(self):
        self.stats = {"m": 1}
        self._recipients = ["r1"]

    async def get_stats(self):
        return self.stats

    async def list_recipients(self):
        return self._recipients


def _patch_bus(monkeypatch, bus):
    import modules.thinking.communication.message_bus as mb

    monkeypatch.setattr(mb, "get_message_bus", lambda: bus)
    return mb


def test_get_bus_stats_basic(monkeypatch):
    _patch_bus(monkeypatch, _FakeBus())
    out = _run(api_mod.get_bus_stats(peek=False, peek_all=False))
    assert out["data"]["stats"] == {"messages": 5}
    assert out["data"]["recipients"] == ["r1", "r2"]


def test_get_bus_stats_peek_all(monkeypatch):
    _patch_bus(monkeypatch, _FakeBus())
    out = _run(api_mod.get_bus_stats(peek=False, peek_all=True))
    assert out["data"]["queues"]["r1"]["count"] == 1
    assert out["data"]["queues"]["r1"]["messages"] == [{"id": 1}]


def test_get_bus_stats_peek(monkeypatch):
    _patch_bus(monkeypatch, _FakeBus())
    out = _run(api_mod.get_bus_stats(peek=True, peek_all=False))
    assert out["data"]["queues"]["r1"]["count"] == 1
    assert out["data"]["queues"]["r1"]["messages"][0]["id"] == 1


def test_get_bus_stats_peek_no_recipients(monkeypatch):
    _patch_bus(monkeypatch, _FakeBus(recipients=()))
    out = _run(api_mod.get_bus_stats(peek=True, peek_all=False))
    assert "queues" not in out["data"]


def test_get_bus_stats_no_peek_methods(monkeypatch):
    _patch_bus(monkeypatch, _MinimalBus())
    out = _run(api_mod.get_bus_stats(peek=True, peek_all=True))
    assert out["data"]["queues"] == {}
    out2 = _run(api_mod.get_bus_stats(peek=True, peek_all=False))
    assert out2["data"]["queues"] == {}


def test_get_bus_stats_error(monkeypatch):
    import modules.thinking.communication.message_bus as mb

    monkeypatch.setattr(mb, "get_message_bus", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(Exception):
        _run(api_mod.get_bus_stats())


# ── 路由级集成（TestClient 冒烟）────────────────────────────────────────────

def test_router_via_testclient(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from modules.management.api_log_store import ApiLogStore

    store = ApiLogStore(path=str(tmp_path / "t.db"))
    store.add("GET", "/health", 200)
    store.flush()
    monkeypatch.setattr(ApiLogStore, "get_instance", classmethod(lambda cls: store))

    app = FastAPI()
    app.include_router(api_mod.router)
    client = TestClient(app)

    assert client.get("/management/dashboard").json()["success"] is True
    assert client.get("/management/api-requests").json()["success"] is True
    assert client.get("/management/api-requests/stats").json()["success"] is True
    assert client.get("/management/health").json()["success"] is True
    store.stop()
