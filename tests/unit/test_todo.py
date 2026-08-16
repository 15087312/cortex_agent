"""todo 工具补测 — 覆盖所有 action 分支、异常路径与 session_id 处理

不写真实 ~/.cortex/todos 文件：monkeypatch _todos_path 指向 tmp_path。
"""
import json
import pytest

from infra.tool_manager.tools.todo import todo, _load_todos, _save_todos, _todos_path


@pytest.fixture(autouse=True)
def _tmp_todos(monkeypatch, tmp_path):
    p = tmp_path / "todos"
    monkeypatch.setattr("infra.tool_manager.tools.todo._todos_path", lambda sid="": str(p / f"{sid or 'default'}.json"))
    return p


def test_todos_path_sanitizes_session_id():
    p = _todos_path("  a/b  ")
    assert p.endswith("a_b.json")
    p2 = _todos_path("")
    assert p2.endswith("default.json")


def test_load_todos_missing_file(tmp_path):
    assert _load_todos("nosuch") == []


def test_load_todos_corrupt_json(tmp_path):
    p = tmp_path / "todos" / "bad.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{invalid", encoding="utf-8")
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(p)
    assert _load_todos("bad") == []


def test_save_and_load_roundtrip(tmp_path):
    p = tmp_path / "todos" / "x.json"
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(p)
    _save_todos([{"id": "1", "content": "c"}], "x")
    assert _load_todos("x") == [{"id": "1", "content": "c"}]


def test_list_empty(tmp_path):
    res = todo(action="list", session_id="empty")
    assert res["action"] == "list"
    assert res["total"] == 0


def test_list_with_items(tmp_path):
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(tmp_path / "t" / "x.json")
    _save_todos([{"id": "t1", "content": "a"}], "x")
    res = todo(action="list", session_id="x")
    assert res["total"] == 1
    assert res["items"][0]["id"] == "t1"


def test_create_requires_items(tmp_path):
    res = todo(action="create", items=None)
    assert "error" in res


def test_create_invalid_json(tmp_path):
    res = todo(action="create", items="{oops")
    assert "error" in res


def test_create_with_dict_items(tmp_path):
    res = todo(action="create", items=[{"content": "t1"}, {"content": "t2", "status": "in_progress"}])
    assert res["created"] == 2
    assert res["items"][0]["status"] == "pending"
    assert res["items"][1]["status"] == "in_progress"
    assert res["items"][0]["id"].startswith("task_")


def test_create_from_json_string(tmp_path):
    res = todo(action="create", items=json.dumps([{"content": "a"}]))
    assert res["created"] == 1
    assert res["items"][0]["content"] == "a"


def test_update_requires_items(tmp_path):
    res = todo(action="update", items=None)
    assert "error" in res


def test_update_invalid_json(tmp_path):
    res = todo(action="update", items="[broken")
    assert "error" in res


def test_update_content_and_status(tmp_path):
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(tmp_path / "u" / "x.json")
    todo(action="create", items=json.dumps([{"content": "orig"}]), session_id="x")
    listed = todo(action="list", session_id="x")["items"]
    tid = listed[0]["id"]
    res = todo(action="update", items=json.dumps([{"id": tid, "content": "new", "status": "completed"}]), session_id="x")
    assert res["updated"] == 1
    assert res["items"][0]["content"] == "new"
    assert res["items"][0]["status"] == "completed"
    assert "updated_at" in res["items"][0]


def test_update_content_only_no_status(tmp_path):
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(tmp_path / "cs" / "x.json")
    todo(action="create", items=json.dumps([{"content": "orig"}]), session_id="x")
    tid = todo(action="list", session_id="x")["items"][0]["id"]
    res = todo(action="update", items=[{"id": tid, "content": "only-content"}], session_id="x")
    assert res["updated"] == 1
    assert res["items"][0]["content"] == "only-content"
    assert res["items"][0]["status"] == "pending"


def test_update_missing_id(tmp_path):
    res = todo(action="update", items=json.dumps([{"id": "nonexistent", "status": "completed"}]), session_id="empty")
    assert res["updated"] == 0
    assert res["not_found"] == ["nonexistent"]


def test_update_mixed_found_and_missing(tmp_path):
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(tmp_path / "m" / "x.json")
    todo(action="create", items=json.dumps([{"content": "keep"}]), session_id="x")
    tid = todo(action="list", session_id="x")["items"][0]["id"]
    res = todo(action="update", items=[{"id": tid, "status": "completed"}, {"id": "zzz", "status": "completed"}], session_id="x")
    assert res["updated"] == 1
    assert "items" in res
    assert res["not_found"] == ["zzz"]


def test_delete_requires_items(tmp_path):
    res = todo(action="delete", items=None)
    assert "error" in res


def test_delete_invalid_json(tmp_path):
    res = todo(action="delete", items="[[[")
    assert "error" in res


def test_delete_some_and_missing(tmp_path):
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(tmp_path / "d" / "x.json")
    todo(action="create", items=json.dumps([{"content": "a"}, {"content": "b"}]), session_id="x")
    ids = [i["id"] for i in todo(action="list", session_id="x")["items"]]
    res = todo(action="delete", items=json.dumps([{"id": ids[0]}, {"id": "zzz"}]), session_id="x")
    assert res["deleted"] == 1
    remaining = todo(action="list", session_id="x")["items"]
    assert len(remaining) == 1
    assert remaining[0]["id"] == ids[1]


def test_delete_empty_id_skipped(tmp_path):
    from infra.tool_manager.tools import todo as todo_mod
    todo_mod._todos_path = lambda sid="": str(tmp_path / "e" / "x.json")
    todo(action="create", items=json.dumps([{"content": "a"}]), session_id="x")
    res = todo(action="delete", items=[{"id": ""}, {"id": "zzz"}], session_id="x")
    assert res["deleted"] == 0


def test_unsupported_action():
    res = todo(action="frobnicate")
    assert "不支持的操作" in res["error"]


def test_action_case_insensitive(tmp_path):
    res = todo(action="LIST", session_id="case")
    assert res["action"] == "list"


def test_registered_in_registry():
    from infra.tool_manager.tool_registry import ToolRegistry
    assert ToolRegistry.get_func("todo") is todo


def test_delete_accepts_string_id():
    """delete 兼容字符串 id 列表（模型可能传 ['id']）"""
    todo("create", json.dumps([{"content": "待删"}]))
    r = todo("delete", json.dumps(["id-not-exist"]))
    assert "deleted" in r  # 不抛异常，deleted=0
    assert r["deleted"] == 0


def test_delete_accepts_dict_id():
    """delete 兼容 dict id 列表（[{'id': ...}]）"""
    todo("create", json.dumps([{"content": "待删2"}]))
    lst = todo("list")
    tid = lst["items"][-1]["id"]
    r = todo("delete", json.dumps([{"id": tid}]))
    assert r["deleted"] == 1
