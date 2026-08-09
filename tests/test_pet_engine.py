"""PetEngine 测试（此前 19% 覆盖）：启用开关、消息构建、会话 id"""
from unittest.mock import MagicMock

import pytest

from config.settings import settings
from modules.desktop_pet.pet_engine import PetEngine


def _engine():
    pe = PetEngine.__new__(PetEngine)
    pe._client = None
    pe._event_bus = None
    pe._sub_id = ""
    pe._running = False
    pe._reply_listeners = []
    pe.last_reply = None
    return pe


def test_enabled_property(monkeypatch):
    pe = _engine()
    monkeypatch.setattr(settings, "DESKTOP_PET_ENABLED", True)
    assert pe.enabled is True
    monkeypatch.setattr(settings, "DESKTOP_PET_ENABLED", False)
    assert pe.enabled is False


def test_pet_session_id_default(monkeypatch):
    pe = _engine()
    monkeypatch.setattr(settings, "DESKTOP_PET_SESSION_ID", "pet_main")
    assert pe.pet_session_id == "pet_main"


def test_build_messages(monkeypatch):
    pe = _engine()
    pe._client = MagicMock()

    class FakeRepo:
        @staticmethod
        def get_recent_messages(sid, limit=20):
            return [{"role": "user", "content": "旧消息"}, {"role": "assistant", "content": "旧回复"}]

    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: FakeRepo())
    msgs = pe._build_messages("你好")
    assert msgs[0].role == "system"
    assert "桌宠" in msgs[0].content
    # 历史消息加入
    assert any(m.role == "user" and m.content == "旧消息" for m in msgs)
    assert msgs[-1].role == "user"
    assert msgs[-1].content == "你好"


def test_build_messages_extra_system(monkeypatch):
    pe = _engine()
    pe._client = MagicMock()

    class EmptyRepo:
        @staticmethod
        def get_recent_messages(sid, limit=20):
            return []

    monkeypatch.setattr("modules.database.session_repo.get_session_repo", lambda: EmptyRepo())
    msgs = pe._build_messages("嗨", extra_system="当前心情很好")
    assert "当前心情很好" in msgs[0].content
    assert msgs[-1].content == "嗨"
