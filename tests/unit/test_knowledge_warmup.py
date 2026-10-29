"""知识库启动预热单测 — 开关门控、进程内幂等、失败不阻断"""
import pytest

import modules.knowledge.warmup as warmup
from config.settings import settings


class _FakeService:
    def __init__(self, result=None, error=None):
        self.calls = 0
        self.paths = []
        self._result = result if result is not None else {
            "added_count": 2, "updated_count": 1, "unchanged_count": 3,
            "skipped_count": 0, "errors": [],
        }
        self._error = error

    def ingest(self, path=""):
        self.calls += 1
        self.paths.append(path)
        if self._error:
            raise self._error
        return self._result


@pytest.fixture(autouse=True)
def _fresh_state():
    warmup.reset_for_tests()
    yield
    warmup.reset_for_tests()


def test_ingest_once_returns_service_result():
    svc = _FakeService()
    result = warmup.ingest_once(svc)
    assert result["added_count"] == 2
    assert svc.paths == [""]


def test_ingest_once_falls_back_to_facade(monkeypatch):
    svc = _FakeService()
    monkeypatch.setattr("modules.knowledge.service.get_knowledge_service", lambda: svc)
    assert warmup.ingest_once()["unchanged_count"] == 3
    assert svc.calls == 1


def test_disabled_by_auto_ingest_switch(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INGEST", False, raising=False)
    svc = _FakeService()
    assert warmup.start_background_ingest(svc) is None
    assert svc.calls == 0


def test_disabled_by_knowledge_switch(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_ENABLED", False, raising=False)
    svc = _FakeService()
    assert warmup.start_background_ingest(svc) is None
    assert svc.calls == 0


def test_background_ingest_runs_once(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INGEST", True, raising=False)
    svc = _FakeService()
    thread = warmup.start_background_ingest(svc)
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert svc.calls == 1
    # 幂等：同一进程内已经跑过，不再重复摄取（避免多次启动叠加读盘/嵌入）
    assert warmup.start_background_ingest(svc) is None
    assert svc.calls == 1


def test_background_ingest_swallows_failure(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INGEST", True, raising=False)
    svc = _FakeService(error=RuntimeError("embedding down"))
    thread = warmup.start_background_ingest(svc)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert svc.calls == 1


def test_background_ingest_handles_error_payload(monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_AUTO_INGEST", True, raising=False)
    svc = _FakeService(result={"error": "知识库已关闭（KNOWLEDGE_ENABLED=false）"})
    thread = warmup.start_background_ingest(svc)
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert svc.calls == 1
