"""批量工具测试：plan_tools / security_tools / file_history_tools"""
import pathlib
from unittest.mock import MagicMock

import pytest

from infra.tool_manager.tools import plan_tools, security_tools, file_history_tools


# ── plan_tools（方案管理，真实临时文件）──────────────────────────────────────

@pytest.fixture
def plans(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_tools, "PLANS_DIR", tmp_path)
    return tmp_path


def test_plan_create_and_list(plans):
    r = plan_tools.plan("create", title="方案A", content="内容")
    assert r["success"] is True
    lst = plan_tools.plan("list")
    assert any(p["title"] == "方案A" for p in lst["plans"])


def test_plan_create_requires_title(plans):
    r = plan_tools.plan("create")
    assert r["success"] is False
    assert "title" in r["error"]


def test_plan_get(plans):
    plan_tools.plan("create", title="T", content="C")
    pid = plan_tools.plan("list")["plans"][0]["id"]
    r = plan_tools.plan("get", plan_id=pid)
    assert r["success"] is True
    assert r["content"] == "C"


def test_plan_update_status(plans):
    plan_tools.plan("create", title="T")
    pid = plan_tools.plan("list")["plans"][0]["id"]
    r = plan_tools.plan("update", plan_id=pid, status="completed")
    assert r["success"] is True
    got = plan_tools.plan("get", plan_id=pid)
    assert got["status"] == "completed"


def test_plan_delete(plans):
    plan_tools.plan("create", title="T")
    pid = plan_tools.plan("list")["plans"][0]["id"]
    assert plan_tools.plan("delete", plan_id=pid)["success"] is True
    assert plan_tools.plan("list")["plans"] == []


def test_plan_unknown_action(plans):
    assert plan_tools.plan("bogus")["success"] is False


# ── security_tools（扫描）───────────────────────────────────────────────────

def test_scan_secrets_finds_pattern(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("API_KEY = 'sk-1234567890abcdef'\n", encoding="utf-8")
    r = security_tools.scan_secrets(str(tmp_path))
    assert r["success"] is True
    assert r["total"] >= 1


def test_scan_secrets_path_missing():
    r = security_tools.scan_secrets("/不存在/路径")
    assert "路径不存在" in r["error"]


def test_scan_sast_finds_injection(tmp_path):
    f = tmp_path / "vuln.py"
    f.write_text("cursor.execute(f\"SELECT * FROM users WHERE id='{uid}'\")\n", encoding="utf-8")
    r = security_tools.scan_sast(str(tmp_path))
    assert r["success"] is True
    assert r["total"] >= 1
    assert r["vulnerabilities"][0]["type"] == "SQL Injection"


def test_scan_dangerous_code(tmp_path):
    f = tmp_path / "danger.py"
    f.write_text("eval(user_input)\n", encoding="utf-8")
    r = security_tools.scan_dangerous_code(str(tmp_path))
    assert r["success"] is True
    assert r["total"] >= 1


def test_scan_dependencies(monkeypatch):
    monkeypatch.setattr(security_tools, "subprocess", MagicMock())
    r = security_tools.scan_dependencies()
    assert r["success"] is True


# ── file_history_tools（mock get_file_history）───────────────────────────────

@pytest.fixture
def fh(monkeypatch):
    hist = MagicMock()
    hist.record_initial.return_value = "v1"
    import modules.cortex.file_history as fh_mod
    monkeypatch.setattr(fh_mod, "get_file_history", lambda: hist)
    return hist


def test_record_before(fh, tmp_path):
    p = tmp_path / "a.py"
    p.write_text("code", encoding="utf-8")
    r = file_history_tools.record_file_change("before", str(p))
    assert r["success"] is True
    assert r["version_id"] == "v1"
    fh.record_initial.assert_called_once()


def test_record_after_requires_content(fh):
    r = file_history_tools.record_file_change("after", "/x.py")
    assert r["success"] is False
    assert "content" in r["error"]


def test_record_unknown_action(fh):
    r = file_history_tools.record_file_change("bogus", "/x.py")
    assert r["success"] is False
