"""management/api 测试（此前 38% 覆盖）：管理控制台端点"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

import modules.management.api as api_mod
from modules.management.core.collector import ModuleInfo


@pytest.fixture
def api_log(tmp_path, monkeypatch):
    """真实 ApiLogStore（临时 SQLite）注入"""
    from modules.management.api_log_store import ApiLogStore
    store = ApiLogStore(path=str(tmp_path / "api_log.db"))
    monkeypatch.setattr(ApiLogStore, "get_instance", classmethod(lambda cls: store))
    return store


def _fake_collector():
    c = MagicMock()
    c.collect_all.return_value = {
        "thinking": {"status": "healthy"},
        "memory": {"status": "healthy"},
    }
    return c


def _patch_store(monkeypatch):
    import modules.management.api_log_store as store_mod
    store = MagicMock()
    store.flush.return_value = None
    store.query.return_value = [{"method": "GET", "path": "/x"}]
    store.count.return_value = 1
    store.stats.return_value = {"total": 5}
    monkeypatch.setattr(store_mod.ApiLogStore, "get_instance", staticmethod(lambda: store))
    return store


def test_get_dashboard_real(api_log):
    """真实 collector + 真实 ApiLogStore"""
    from modules.management.core.collector import StatusCollector, ModuleRegistry
    api_mod._collector = StatusCollector(ModuleRegistry())
    out = asyncio.run(api_mod.get_dashboard())
    assert out["success"] is True
    assert out["data"]["health"]["total_modules"] > 0


def test_get_api_requests_real(api_log):
    """真实 ApiLogStore：先落一条再查询"""
    api_log.add("GET", "/health", 200)
    api_log.flush()
    out = asyncio.run(api_mod.get_api_requests(method="", path="", status=0, limit=50, offset=0, since_hours=0))
    assert out["data"]["total"] >= 1


def test_get_api_requests_stats_real(api_log):
    api_log.add("GET", "/health", 200)
    api_log.flush()
    out = asyncio.run(api_mod.get_api_requests_stats(since_hours=24))
    assert out["data"]["total"] >= 1


def test_get_all_modules(monkeypatch):
    r = MagicMock()
    r.get_all_modules.return_value = [ModuleInfo(name="thinking", module_path="/x", has_api=True, has_core=True)]
    monkeypatch.setattr(api_mod, "_registry", r)
    out = asyncio.run(api_mod.get_all_modules())
    assert out["success"] is True
    assert out["data"]["modules"][0]["name"] == "thinking"
    assert out["data"]["total"] == 1


def test_get_modules_status(monkeypatch):
    monkeypatch.setattr(api_mod, "_collector", _fake_collector())
    out = asyncio.run(api_mod.get_modules_status())
    assert out["data"]["thinking"]["status"] == "healthy"


def test_get_module_detail_found(monkeypatch):
    r = MagicMock()
    r.get_module.return_value = ModuleInfo(name="thinking", module_path="/x", has_api=True)
    monkeypatch.setattr(api_mod, "_registry", r)
    monkeypatch.setattr(api_mod, "_collector", _fake_collector())
    out = asyncio.run(api_mod.get_module_detail("thinking"))
    assert out["success"] is True
    assert out["data"]["info"]["name"] == "thinking"


def test_get_orchestration_real():
    """真实 loader（roles.yaml）+ 真实 settings"""
    out = asyncio.run(api_mod.get_orchestration())
    assert out["success"] is True
    assert any(a["role"] == "orchestrator" for a in out["data"]["agents"])


def test_get_todos_real(tmp_path, monkeypatch):
    """真实 todo 文件：写入后读取"""
    import infra.tool_manager.tools.todo as todo_mod
    monkeypatch.setattr(todo_mod, "_todos_path", lambda sid: str(tmp_path / f"{sid}.json"))
    todo_mod._save_todos([{"id": "t1", "title": "任务", "status": "pending"}], "s1")
    out = asyncio.run(api_mod.get_todos(session_id="s1"))
    assert out["data"]["todos"][0]["id"] == "t1"


def test_set_todo_status_real(tmp_path, monkeypatch):
    """真实 todo 文件：更新状态落盘"""
    import infra.tool_manager.tools.todo as todo_mod
    monkeypatch.setattr(todo_mod, "_todos_path", lambda sid: str(tmp_path / f"{sid}.json"))
    todo_mod._save_todos([{"id": "t1", "status": "pending"}], "s1")
    out = asyncio.run(api_mod.set_todo_status("t1", {"status": "completed"}, session_id="s1"))
    assert out["success"] is True
    loaded = todo_mod._load_todos("s1")
    assert loaded[0]["status"] == "completed"


def test_set_todo_status_invalid_and_missing(tmp_path, monkeypatch):
    """无效状态 / 不存在的任务 → 失败"""
    import infra.tool_manager.tools.todo as todo_mod
    monkeypatch.setattr(todo_mod, "_todos_path", lambda sid: str(tmp_path / f"{sid}.json"))
    out = asyncio.run(api_mod.set_todo_status("t1", {"status": "bad"}, session_id="s1"))
    assert out["success"] is False
    out2 = asyncio.run(api_mod.set_todo_status("t1", {"status": "done"}, session_id="s1"))
    assert out2["success"] is False


@pytest.fixture
def skill_mgr(monkeypatch, tmp_path):
    """真实 SkillManager（临时 skills 目录，隔离全局目录）"""
    import modules.thinking.skills.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "_get_skills_dir", lambda: tmp_path)
    mgr = mgr_mod.SkillManager()
    import modules.thinking.skills as skills_mod
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    return mgr


class _Skill:
    def __init__(self, **kw):
        self.id = kw.get("id", "s1")
        self.name = kw.get("name", "技能")
        self.description = kw.get("description", "描述")
        self.keywords = kw.get("keywords", [])
        self.source = kw.get("source", "user")
        self.enabled = kw.get("enabled", True)
        self.metadata = kw.get("metadata", {})
        self.tool_rules = kw.get("tool_rules", None)
        self.trigger = kw.get("trigger", None)
        self.raw_content = kw.get("raw_content", "# 技能")
        self.path = kw.get("path", "/tmp/skills/s1/SKILL.md")


def _patch_skill_manager(monkeypatch):
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.to_listing.return_value = [{"id": "s1"}]
    mgr.get_skill.return_value = _Skill()
    mgr.create_skill.return_value = (True, "已创建")
    mgr.update_skill.return_value = (True, "已更新")
    mgr.set_enabled.return_value = (True, "ok")
    mgr.delete_skill.return_value = (True, "已删除")
    mgr.reload.return_value = 3
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    return mgr


def test_list_skills_empty(skill_mgr):
    """真实 manager：空目录列表为空"""
    out = asyncio.run(api_mod.list_skills())
    assert out["data"]["skills"] == []


def test_create_and_get_skill(skill_mgr):
    """真实创建技能 + 读取详情"""
    ok, _ = skill_mgr.create_skill(
        skill_id="test_skill", name="测试技能", description="描述",
        keywords=["测试"], trigger=None, tool_rules=None,
    )
    assert ok is True
    out = asyncio.run(api_mod.get_skill_detail("test_skill"))
    assert out["data"]["name"] == "测试技能"
    out2 = asyncio.run(api_mod.get_skill_detail("nope"))
    assert out2["success"] is False


def test_update_and_enable_skill(skill_mgr):
    skill_mgr.create_skill(skill_id="s1", name="旧名", description="d", keywords=[], trigger=None, tool_rules=None)
    ok, _ = skill_mgr.update_skill("s1", name="新名")
    assert ok is True
    ok2, _ = skill_mgr.set_enabled("s1", False)
    assert ok2 is True
    out = asyncio.run(api_mod.get_skill_detail("s1"))
    assert out["data"]["name"] == "新名"
    assert out["data"]["enabled"] is False


def test_delete_skill(skill_mgr):
    skill_mgr.create_skill(skill_id="s1", name="x", description="d", keywords=[], trigger=None, tool_rules=None)
    out = asyncio.run(api_mod.delete_skill("s1"))
    assert out["success"] is True
    assert asyncio.run(api_mod.get_skill_detail("s1"))["success"] is False


def test_reload_skills(skill_mgr):
    out = asyncio.run(api_mod.reload_skills())
    assert out["success"] is True


def test_set_todo_status_invalid_and_missing(tmp_path, monkeypatch):
    """无效状态 / 不存在的任务 → 失败"""
    import infra.tool_manager.tools.todo as todo_mod
    monkeypatch.setattr(todo_mod, "_todos_path", lambda sid: str(tmp_path / f"{sid}.json"))
    out = asyncio.run(api_mod.set_todo_status("t1", {"status": "bad"}, session_id="s1"))
    assert out["success"] is False
    out2 = asyncio.run(api_mod.set_todo_status("t1", {"status": "done"}, session_id="s1"))
    assert out2["success"] is False


@pytest.fixture
def skill_mgr(monkeypatch, tmp_path):
    """真实 SkillManager（临时 skills 目录，隔离全局目录）"""
    import modules.thinking.skills.manager as mgr_mod
    monkeypatch.setattr(mgr_mod, "_get_skills_dir", lambda: tmp_path)
    mgr = mgr_mod.SkillManager()
    import modules.thinking.skills as skills_mod
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    return mgr


class _Skill:
    def __init__(self, **kw):
        self.id = kw.get("id", "s1")
        self.name = kw.get("name", "技能")
        self.description = kw.get("description", "描述")
        self.keywords = kw.get("keywords", [])
        self.source = kw.get("source", "user")
        self.enabled = kw.get("enabled", True)
        self.metadata = kw.get("metadata", {})
        self.tool_rules = kw.get("tool_rules", None)
        self.trigger = kw.get("trigger", None)
        self.raw_content = kw.get("raw_content", "# 技能")
        self.path = kw.get("path", "/tmp/skills/s1/SKILL.md")


def _patch_skill_manager(monkeypatch):
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.to_listing.return_value = [{"id": "s1"}]
    mgr.get_skill.return_value = _Skill()
    mgr.create_skill.return_value = (True, "已创建")
    mgr.update_skill.return_value = (True, "已更新")
    mgr.set_enabled.return_value = (True, "ok")
    mgr.delete_skill.return_value = (True, "已删除")
    mgr.reload.return_value = 3
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    return mgr


def test_list_skills(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.list_skills())
    assert out["data"]["skills"] == [{"id": "s1"}]


def test_get_skill_detail(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.get_skill_detail("s1"))
    assert out["data"]["id"] == "s1"


def test_get_skill_detail_missing(monkeypatch):
    import modules.thinking.skills as skills_mod
    mgr = MagicMock()
    mgr.get_skill.return_value = None
    monkeypatch.setattr(skills_mod, "skill_manager", mgr)
    out = asyncio.run(api_mod.get_skill_detail("nope"))
    assert out["success"] is False


def test_create_skill(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.create_skill({"id": "s2", "name": "新技能"}))
    assert out["success"] is True


def test_update_skill(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.update_skill("s1", {"name": "改名"}))
    assert out["success"] is True


def test_set_skill_enabled(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.set_skill_enabled("s1", {"enabled": False}))
    assert out["success"] is True
    assert out["data"]["enabled"] is False


def test_delete_skill(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.delete_skill("s1"))
    assert out["success"] is True


def test_reload_skills(monkeypatch):
    mgr = _patch_skill_manager(monkeypatch)
    out = asyncio.run(api_mod.reload_skills())
    assert out["data"]["count"] == 3
