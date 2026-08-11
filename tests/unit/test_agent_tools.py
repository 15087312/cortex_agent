"""agent 工具测试（此前 8-18% 覆盖）——纯逻辑工具全覆盖"""
import json
import os

import pytest

from infra.tool_manager.tools import calculator, todo as todo_mod, audit_tools, tools_search


# ── calculator（纯逻辑）─────────────────────────────────────────────────────

def test_calc_basic():
    assert calculator.calculate(2, "+", 3) == "2 + 3 = 5"
    assert calculator.calculate(5, "-", 2) == "5 - 2 = 3"
    assert calculator.calculate(2, "*", 3) == "2 * 3 = 6"
    assert calculator.calculate(2, "**", 10) == "2 ** 10 = 1024"
    assert calculator.calculate(7, "%", 3) == "7 % 3 = 1"


def test_calc_float_and_div():
    assert calculator.calculate(5, "/", 2) == "5 / 2 = 2.5"
    assert "除数不能为零" in calculator.calculate(5, "/", 0)


def test_calc_unsupported_op():
    assert "不支持的运算符" in calculator.calculate(1, "^", 2)


def test_advanced_calc():
    assert calculator.advanced_calc("sqrt", 16) == "16 的 sqrt = 4.0" or "4" in calculator.advanced_calc("sqrt", 16)
    assert "log10" in calculator.advanced_calc("log10", 100)


def test_advanced_calc_unsupported():
    r = calculator.advanced_calc("foo", 1)
    assert "不支持" in r


# ── todo（文件持久化，monkeypatch 到临时目录）────────────────────────────────

@pytest.fixture
def tmp_todos(monkeypatch, tmp_path):
    monkeypatch.setattr(todo_mod, "_todos_path", lambda sid="": str(tmp_path / "todos" / f"{(sid or 'default').replace('/', '_')}.json"))
    return tmp_path


def test_todo_create_list(tmp_todos):
    r = todo_mod.todo(action="create", items=json.dumps([{"content": "任务一"}, {"content": "任务二"}]))
    assert r.get("created") == 2
    lst = todo_mod.todo(action="list")
    assert lst["total"] == 2
    assert any(t["content"] == "任务一" for t in lst["items"])


def test_todo_update_status(tmp_todos):
    todo_mod.todo(action="create", items=json.dumps([{"content": "任务"}]))
    lst = todo_mod.todo(action="list")
    tid = lst["items"][0]["id"]
    r = todo_mod.todo(action="update", items=json.dumps([{"id": tid, "status": "completed"}]))
    assert r.get("updated") == 1
    lst2 = todo_mod.todo(action="list")
    assert lst2["items"][0]["status"] == "completed"


def test_todo_delete(tmp_todos):
    todo_mod.todo(action="create", items=json.dumps([{"content": "任务"}]))
    lst = todo_mod.todo(action="list")
    tid = lst["items"][0]["id"]
    r = todo_mod.todo(action="delete", items=json.dumps([{"id": tid}]))
    assert r.get("deleted") == 1
    assert todo_mod.todo(action="list")["total"] == 0


def test_todo_session_isolation(tmp_todos):
    todo_mod.todo(action="create", items=json.dumps([{"content": "A"}]), session_id="s1")
    todo_mod.todo(action="create", items=json.dumps([{"content": "B"}]), session_id="s2")
    assert todo_mod.todo(action="list", session_id="s1")["total"] == 1
    assert todo_mod.todo(action="list", session_id="s2")["total"] == 1


def test_todo_invalid_action(tmp_todos):
    r = todo_mod.todo(action="bogus")
    assert "error" in r


# ── audit_tools（monkeypatch 日志文件到临时）────────────────────────────────

@pytest.fixture
def tmp_audit(monkeypatch, tmp_path):
    monkeypatch.setattr(audit_tools, "AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(audit_tools, "CHANGE_LOG", str(tmp_path / "changes.jsonl"))
    return tmp_path


def test_log_tool_call_and_report(tmp_audit):
    r = audit_tools.log_tool_call("calc", caller_role="expert", params="{'a':1}", result="ok", success=True)
    assert r["success"] is True
    report = audit_tools.generate_audit_report(limit=10)
    assert report["success"] is True
    assert report["total_logs"] == 1
    assert report["by_tool"].get("calc", 0) == 1


def test_audit_report_limit(tmp_audit):
    for i in range(5):
        audit_tools.log_tool_call(f"tool_{i}")
    report = audit_tools.generate_audit_report(limit=2)
    assert report["total_logs"] == 5  # total_logs 是全部，recent_entries 截断
    assert len(report["recent_entries"]) == 2


# ── tools_search（基于真实注册表）────────────────────────────────────────────

def test_tools_search_keyword():
    r = tools_search.tools_search(keyword="calc")
    assert r["success"] is True
    assert any("calc" in t["name"] for t in r["tools"])


def test_tools_search_category_filter():
    r = tools_search.tools_search(category="admin")
    assert r["success"] is True
    assert all(t["category"] == "admin" for t in r["tools"])


def test_tools_search_no_match():
    r = tools_search.tools_search(keyword="不存在的工具xyz")
    assert r["count"] == 0
