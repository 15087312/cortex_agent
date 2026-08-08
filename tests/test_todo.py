"""todo 任务工具测试（create/list/update/delete）"""
import pytest

from infra.tool_manager.tools import todo as td
from infra.tool_manager.tools.todo import todo


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(td, "TODOS_FILE", str(tmp_path / "todos.json"))


def test_create_and_list():
    r = todo(action="create", items='[{"content":"任务1"}]')
    assert r["created"] == 1
    r2 = todo(action="list")
    assert r2["total"] == 1
    assert r2["items"][0]["content"] == "任务1"
    assert r2["items"][0]["status"] == "pending"


def test_update_status():
    todo(action="create", items='[{"content":"任务1"}]')
    tid = todo(action="list")["items"][0]["id"]
    todo(action="update", items=f'[{{"id":"{tid}","status":"completed"}}]')
    items = todo(action="list")["items"]
    assert items[0]["status"] == "completed"


def test_delete():
    todo(action="create", items='[{"content":"任务1"}]')
    tid = todo(action="list")["items"][0]["id"]
    todo(action="delete", items=f'[{{"id":"{tid}"}}]')
    assert todo(action="list")["total"] == 0


def test_bad_json_returns_error():
    r = todo(action="create", items="not-json")
    assert "error" in r
