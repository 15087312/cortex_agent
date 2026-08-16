"""security_tools 测试：密钥/SQL/危险代码扫描 + 依赖审计"""
import subprocess

from infra.tool_manager.tools.security_tools import (
    scan_secrets, scan_sast, scan_dangerous_code, scan_dependencies,
)


def test_scan_secrets_missing_path():
    assert "error" in scan_secrets("/不存在/路径/x")


def test_scan_secrets_finds_api_key(tmp_path):
    f = tmp_path / "app.py"
    f.write_text("API_KEY = 'sk-abcdef1234567890abcdef'\n", encoding="utf-8")
    out = scan_secrets(str(f))
    assert out["success"] is True
    assert out["total"] >= 1


def test_scan_secrets_skips_git_and_pycache(tmp_path):
    d = tmp_path / "repo/.git"
    d.mkdir(parents=True)
    (d / "secret.py").write_text("PASS='sk-abcdef123456'\n", encoding="utf-8")
    c = tmp_path / "repo/__pycache__"
    c.mkdir(parents=True)
    (c / "c.py").write_text("KEY='sk-abcdef123456'\n", encoding="utf-8")
    out = scan_secrets(str(tmp_path / "repo"))
    assert out["success"] is True
    assert out["total"] == 0


def test_scan_sast_missing_path():
    assert "error" in scan_sast("/不存在/x")


def test_scan_sast_finds_sql_injection(tmp_path):
    f = tmp_path / "x.py"
    f.write_text('cursor.execute(f"SELECT * FROM users WHERE id={uid}")\n', encoding="utf-8")
    out = scan_sast(str(f))
    assert out["success"] is True
    assert out["total"] >= 1
    assert any(v["type"] == "SQL Injection" for v in out["vulnerabilities"])


def test_scan_dangerous_code_finds_eval(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("result = eval(user_input)\n", encoding="utf-8")
    out = scan_dangerous_code(str(f))
    assert out["success"] is True
    assert out["total"] >= 1


def test_scan_dangerous_code_missing_path():
    assert "error" in scan_dangerous_code("/不存在/x")


def test_scan_dependencies_not_installed(monkeypatch):
    """所有扫描工具都 127（未找到）→ 返回 error"""
    def fake_run(*a, **k):
        return subprocess.CompletedProcess([], 127, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = scan_dependencies()
    assert "error" in out


def test_scan_dependencies_found(monkeypatch):
    def fake_run(*a, **k):
        return subprocess.CompletedProcess([], 0, stdout="No known vulnerabilities", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = scan_dependencies()
    assert out.get("success") is True
    assert out.get("vulnerable") is False
