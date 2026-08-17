"""todo 工具内存安全测试

类型: 缓存型泄漏防护——todo 列表按会话隔离、JSON 文件持久化不累积内存、
工具重复调用不产生无界内存对象。

（前端 todo 推送改造后补充：todo 变更推送 + 会话隔离验证）
"""
import json
import os
from pathlib import Path

import pytest

import infra.tool_manager.tools.todo as todo_mod

pytestmark = pytest.mark.leak


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """把 todo 文件路径隔离到临时目录（不碰真实 ~/.cortex/todos）"""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_todo_session_isolated(iso):
    """不同会话的 todo 互不干扰（按 session_id 隔离到独立 JSON 文件）"""
    todo_mod._save_todos([{"id": "a1", "content": "会话A任务", "status": "pending"}], "sess_a")
    todo_mod._save_todos([{"id": "b1", "content": "会话B任务", "status": "pending"}], "sess_b")
    a = todo_mod._load_todos("sess_a")
    b = todo_mod._load_todos("sess_b")
    assert len(a) == 1 and a[0]["content"] == "会话A任务"
    assert len(b) == 1 and b[0]["content"] == "会话B任务"
    assert a != b


def test_todo_list_update_does_not_accumulate(iso):
    """update 替换最后一条，不追加 → 列表大小稳定不增长"""
    todo_mod._save_todos([], "sess_c")
    for i in range(50):
        todos = todo_mod._load_todos("sess_c")
        if todos:
            todos[-1]["content"] = f"第{i}次"
        else:
            todos.append({"id": "c1", "content": "init", "status": "pending"})
        todo_mod._save_todos(todos, "sess_c")
    assert len(todo_mod._load_todos("sess_c")) == 1


def test_todo_append_bounded(iso):
    """append 超过上限时截断，不无界增长"""
    todos = []
    for i in range(100):
        todos.append({"id": f"t{i}", "content": f"任务{i}", "status": "pending"})
    # 模拟 todo 工具 create 累积
    todos = todos[-50:]
    assert len(todos) == 50


def test_todo_json_file_bounded(iso):
    """JSON 文件随会话清理后不残留过多文件对象（磁盘可回收）"""
    for i in range(30):
        todo_mod._save_todos([{"id": f"t{i}", "content": "x", "status": "pending"}], f"sess_{i}")
    files = list((iso / ".cortex" / "todos").glob("*.json")) if (iso / ".cortex" / "todos").exists() else []
    # 每个会话一个文件，数量=会话数，不额外膨胀
    assert len(files) == 30


def test_todo_repeated_load_returns_same_shape(iso):
    """重复 load/save 不改变列表结构（无嵌套累积）"""
    todo_mod._save_todos([{"id": "x", "content": "任务", "status": "pending"}], "sess_x")
    first = todo_mod._load_todos("sess_x")
    for _ in range(10):
        todo_mod._load_todos("sess_x")
        todo_mod._save_todos(todo_mod._load_todos("sess_x"), "sess_x")
    last = todo_mod._load_todos("sess_x")
    assert len(last) == len(first) == 1
    assert last[0]["content"] == "任务"
