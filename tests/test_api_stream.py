"""api_stream 测试：事件构建、scheduler 事件格式化、思考持久化（此前 16% 覆盖）"""
import asyncio
import threading

import pytest

import modules.database.connection as conn
from modules.thinking.api_stream import _build_event, StreamThinkingSystem


def _inst():
    return StreamThinkingSystem.__new__(StreamThinkingSystem)


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def tmp_repo(tmp_path, monkeypatch):
    from modules.database.session_repo import SessionRepository
    import modules.database.session_repo as sr
    monkeypatch.setattr(conn.config, "sqlite_path", str(tmp_path / "test_memory.db"))
    monkeypatch.setattr(conn, "_db_manager", None)
    monkeypatch.setattr(conn, "_db_manager_lock", threading.RLock())
    dm = conn.get_db_manager()
    dm.initialize()
    monkeypatch.setattr(sr, "get_db_manager", lambda: dm)
    return SessionRepository()


def test_build_event_structure():
    ev = _build_event(session_id="s1", msg_type="message", event="message",
                      content="你好", role="assistant", data={"a": 1})
    assert ev["type"] == "message"
    assert ev["event"] == "message"
    assert ev["session_id"] == "s1"
    assert ev["role"] == "assistant"
    assert ev["content"] == "你好"
    assert ev["data"]["a"] == 1
    assert "timestamp" in ev


def test_format_tool_call_event():
    ev = _inst()._format_scheduler_event({
        "type": "tool_call", "target": "read_file", "action": "执行", "success": True,
    })
    assert "read_file" in ev["content"]
    assert "成功" in ev["content"]


def test_format_model_comm_broadcast():
    ev = _inst()._format_scheduler_event({
        "type": "model_comm",
        "source": "orch",
        "target": "chat",
        "payload": {
            "msg_type": "broadcast",
            "content": {
                "content": "我正在分析需求",
                "entry_type": "thought",
                "round": 1,
                "model_id": "large_primary_001",
                "metadata": {"return_to_model_id": ""},
            },
            "metadata": {"dialog_id": "d1"},
        },
    })
    assert ev is not None
    assert "我正在分析需求" in ev["content"]
    assert ev["data"]["event_type"] == "model_comm"


def test_format_model_comm_response_with_parent():
    ev = _inst()._format_scheduler_event({
        "type": "model_comm",
        "source": "exp",
        "target": "chat",
        "payload": {
            "msg_type": "broadcast",
            "content": {
                "content": "代码写好了",
                "entry_type": "response",
                "round": 2,
                "model_id": "expert_001",
                "metadata": {"return_to_model_id": "large_primary_001"},
            },
            "metadata": {"dialog_id": "d1"},
        },
    })
    assert ev is not None
    assert "代码写好了" in ev["content"]
    assert ev["data"]["identity_name"]


def test_format_empty_content_skipped():
    ev = _inst()._format_scheduler_event({
        "type": "model_comm",
        "source": "x", "target": "y",
        "payload": {"msg_type": "broadcast", "content": {"content": "", "metadata": {}}, "metadata": {"dialog_id": "d"}},
    })
    assert ev is None  # 空内容跳过


def test_format_unknown_event():
    ev = _inst()._format_scheduler_event({"type": "scheduler", "action": "tick", "source": "s", "target": "t"})
    assert ev is not None
    assert "tick" in ev["content"]


def test_persist_thought_writes_db(monkeypatch, tmp_repo):
    """思考步骤真实落库（覆盖被 mock 掩盖的 save_message 路径）"""
    inst = _inst()
    inst._get_session_repo = lambda: tmp_repo
    _run(inst._persist_thought("s_thought_1", "正在思考", "large"))
    msgs = tmp_repo.get_messages("s_thought_1")
    assert any(m["content"] == "正在思考" for m in msgs)
    # 思考步骤不写入内存 messages（只落库）
    assert tmp_repo.get_messages("s_thought_1")  # 库里有


def test_persist_thought_empty_content(monkeypatch, tmp_repo):
    inst = _inst()
    inst._get_session_repo = lambda: tmp_repo
    _run(inst._persist_thought("s_thought_1", "", "large"))
    assert tmp_repo.get_messages("s_thought_1") == []
