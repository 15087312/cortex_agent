"""todo 任务工具测试（create/list/update/delete，按会话隔离）"""
import pytest

from infra.tool_manager.tools import todo as td
from infra.tool_manager.tools.todo import todo


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(td, "_todos_path", lambda sid="": str(tmp_path / f"{(sid or 'default').replace('/','_')}.json"))


def test_create_and_list():
    r = todo(action="create", items='[{"content":"任务1"}]', session_id="sess1")
    assert r["created"] == 1
    r2 = todo(action="list", session_id="sess1")
    assert r2["total"] == 1
    assert r2["items"][0]["content"] == "任务1"
    assert r2["items"][0]["status"] == "pending"


def test_session_isolation():
    todo(action="create", items='[{"content":"会话1任务"}]', session_id="sess1")
    todo(action="create", items='[{"content":"会话2任务"}]', session_id="sess2")
    assert todo(action="list", session_id="sess1")["total"] == 1
    assert todo(action="list", session_id="sess2")["total"] == 1
    assert todo(action="list", session_id="sess1")["items"][0]["content"] == "会话1任务"


def test_update_status():
    todo(action="create", items='[{"content":"任务1"}]', session_id="sess1")
    tid = todo(action="list", session_id="sess1")["items"][0]["id"]
    todo(action="update", items=f'[{{"id":"{tid}","status":"completed"}}]', session_id="sess1")
    items = todo(action="list", session_id="sess1")["items"]
    assert items[0]["status"] == "completed"


def test_delete():
    todo(action="create", items='[{"content":"任务1"}]', session_id="sess1")
    tid = todo(action="list", session_id="sess1")["items"][0]["id"]
    todo(action="delete", items=f'[{{"id":"{tid}"}}]', session_id="sess1")
    assert todo(action="list", session_id="sess1")["total"] == 0


def test_bad_json_returns_error():
    r = todo(action="create", items="not-json", session_id="sess1")
    assert "error" in r
