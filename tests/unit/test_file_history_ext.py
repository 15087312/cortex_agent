"""file_history_tools 测试 — 经能力端口注入 mock file_history 服务"""
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.service_registry import get_capability, register_capability, unregister_capability
from infra.tool_manager.tools import file_history_tools as fh


@pytest.fixture
def hist(monkeypatch):
    h = MagicMock()
    h.record_initial.return_value = "v1"
    h.record_version.return_value = "v2"
    original = get_capability("file_history")
    register_capability("file_history", lambda: h)
    yield h
    if original is None:
        unregister_capability("file_history")
    else:
        register_capability("file_history", original)


class TestRecordFileChange:
    def test_before_with_existing_file(self, hist, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("code", encoding="utf-8")
        r = fh.record_file_change("before", str(p))
        assert r["success"] is True
        assert r["version_id"] == "v1"
        assert r["action"] == "recorded_initial"
        hist.record_initial.assert_called_once_with("default", str(p), "code")

    def test_before_missing_file(self, hist, tmp_path):
        p = tmp_path / "missing.py"
        r = fh.record_file_change("before", str(p))
        assert r["success"] is True
        hist.record_initial.assert_called_once_with("default", str(p), "")

    def test_before_exception(self, hist, tmp_path):
        hist.record_initial.side_effect = OSError("boom")
        r = fh.record_file_change("before", str(tmp_path / "x.py"))
        assert r["success"] is False
        assert "boom" in r["error"]

    def test_after_requires_content(self, hist):
        r = fh.record_file_change("after", "/x.py")
        assert r["success"] is False
        assert "content" in r["error"]

    def test_after_success(self, hist):
        r = fh.record_file_change("after", "/x.py", content="new")
        assert r["success"] is True
        assert r["action"] == "recorded_version"
        hist.record_version.assert_called_once_with("default", "/x.py", "new")

    def test_unknown_action(self, hist):
        r = fh.record_file_change("bogus", "/x.py")
        assert r["success"] is False
        assert "未知操作" in r["error"]

    def test_service_not_registered(self):
        original = get_capability("file_history")
        unregister_capability("file_history")
        try:
            r = fh.record_file_change("before", "/x.py")
            assert r["success"] is False
            assert "未注册" in r["error"]
        finally:
            if original is None:
                unregister_capability("file_history")
            else:
                register_capability("file_history", original)


class TestRollbackFile:
    def test_service_not_registered(self):
        original = get_capability("file_history")
        unregister_capability("file_history")
        try:
            r = fh.rollback_file("/x.py")
            assert r["success"] is False
            assert "未注册" in r["error"]
        finally:
            if original is None:
                unregister_capability("file_history")
            else:
                register_capability("file_history", original)

    def test_no_initial(self, hist, tmp_path):
        hist.get_initial.return_value = None
        p = tmp_path / "x.py"
        r = fh.rollback_file(str(p))
        assert r["success"] is False
        assert "没有初始版本记录" in r["error"]

    def test_restores_file(self, hist, tmp_path):
        p = tmp_path / "x.py"
        hist.get_initial.return_value = {"content": "original"}
        r = fh.rollback_file(str(p))
        assert r["success"] is True
        assert r["action"] == "restored"
        assert p.read_text(encoding="utf-8") == "original"

    def test_write_error(self, hist, tmp_path, monkeypatch):
        p = tmp_path / "x.py"
        hist.get_initial.return_value = {"content": "original"}

        def boom(path, mode="r", encoding=None):
            if mode == "w":
                raise OSError("disk")
            return open(path, mode, encoding=encoding)

        monkeypatch.setattr("builtins.open", boom)
        r = fh.rollback_file(str(p))
        assert r["success"] is False
        assert "disk" in r["error"]


class TestRollbackSessionFiles:
    def test_service_not_registered(self):
        original = get_capability("file_history")
        unregister_capability("file_history")
        try:
            r = fh.rollback_session_files()
            assert r["success"] is False
            assert "未注册" in r["error"]
        finally:
            if original is None:
                unregister_capability("file_history")
            else:
                register_capability("file_history", original)

    def test_success(self, hist):
        hist.rollback_session.return_value = {"a": "restored", "b": "restored", "c": "failed"}
        r = fh.rollback_session_files()
        assert r["success"] is True
        assert r["restored"] == 2
        assert r["total"] == 3


class TestListFileVersions:
    def test_service_not_registered(self):
        original = get_capability("file_history")
        unregister_capability("file_history")
        try:
            r = fh.list_file_versions()
            assert r["success"] is False
            assert "未注册" in r["error"]
        finally:
            if original is None:
                unregister_capability("file_history")
            else:
                register_capability("file_history", original)

    def test_with_file_path(self, hist, tmp_path):
        p = tmp_path / "a.py"
        hist.list_versions.return_value = [
            {"id": "v1", "version": 1, "content": "abc", "created_at": "t1"},
            {"id": "v2", "version": 2, "content": "abcd", "created_at": "t2"},
        ]
        r = fh.list_file_versions(str(p))
        assert r["success"] is True
        assert r["path"] == str(p)
        assert r["versions"][0]["content_length"] == 3
        assert r["versions"][1]["version"] == 2

    def test_without_file_path(self, hist):
        hist.list_session_files.return_value = [
            {"path": "/a.py", "version": 2, "content": "xy", "created_at": "t"},
        ]
        r = fh.list_file_versions()
        assert r["success"] is True
        assert r["files"][0]["path"] == "/a.py"
        assert r["files"][0]["content_length"] == 2
