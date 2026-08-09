"""thinking/runtime_expert 测试（此前 46% 覆盖）：运行时专家"""
from unittest.mock import MagicMock

from modules.thinking.runtime_expert import RuntimeExpert


class _ConcreteExpert(RuntimeExpert):
    async def process(self, request_text, messages):
        return "ok"


def _expert(**kw):
    e = _ConcreteExpert.__new__(_ConcreteExpert)
    e._blackboard = kw.get("blackboard", None)
    e.model_id = kw.get("model_id", "expert_x")
    e.session_id = "s1"
    ident = MagicMock()
    ident.name = kw.get("name", "代码实现专家")
    ident.role = kw.get("role", "code_writer")
    ident.tier = "expert"
    e.identity = ident
    e._seen_request_entry_ids = set()
    e.logger = MagicMock()
    return e


def test_is_relevant():
    e = _expert(name="代码实现专家", role="code_writer")
    assert e._is_relevant("请代码实现专家处理") is True
    assert e._is_relevant("请处理一下这个无关请求") is False


def test_read_requests_without_dialog():
    e = _expert(blackboard=None)
    assert e.read_requests() == []


def test_read_requests_filters(monkeypatch):
    dialog = MagicMock()
    dialog.read_dialog.return_value = [
        {"entry_id": "e1", "model_id": "other", "tier": "expert", "metadata": {}, "content": "代码实现专家请看"},
        {"entry_id": "e2", "model_id": None, "tier": "system", "metadata": {}, "content": "系统"},
        {"entry_id": "e3", "model_id": None, "tier": "expert", "metadata": {"internal_protocol": True}, "content": "内部"},
        {"entry_id": "e4", "model_id": None, "tier": "expert", "metadata": {}, "content": "普通无关内容"},
    ]
    e = _expert(blackboard=dialog)
    e._get_dialog = lambda: dialog
    reqs = e.read_requests()
    assert len(reqs) == 1
    assert reqs[0]["entry_id"] == "e1"
    assert "e1" in e._seen_request_entry_ids


def test_write_thought(monkeypatch):
    dialog = MagicMock()
    entry = MagicMock()
    entry.entry_id = "t1"
    dialog.write_thought.return_value = entry
    e = _expert(blackboard=dialog)
    e._get_dialog = lambda: dialog
    assert e.write_thought("思考", 1) == "t1"
    dialog.write_thought.assert_called_once()


def test_write_response_without_dialog():
    e = _expert(blackboard=None)
    assert e.write_response("内容") is None
    assert e.write_thought("内容") is None
