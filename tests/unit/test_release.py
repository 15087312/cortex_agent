"""scripts/release.py — 版本号统一升级 + 打 tag 流程"""
import json
import subprocess

import pytest

import scripts.release as release


def test_bump_versions():
    assert release.bump("2.0.0", "patch") == "2.0.1"
    assert release.bump("2.0.9", "minor") == "2.1.0"
    assert release.bump("2.9.9", "major") == "3.0.0"
    assert release.bump("2.0.0", "bogus") == "2.0.1"  # 未知 kind 按 patch


def test_read_and_write_version(tmp_path):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"name": "x", "version": "1.0.0"}), encoding="utf-8")
    vfile = tmp_path / "VERSION"
    vfile.write_text("1.0.0\n", encoding="utf-8")

    release.VERSION_FILE = str(vfile)
    release.PKG_JSON = str(pkg)

    assert release.read_version() == "1.0.0"
    release.write_version("2.3.4")
    assert vfile.read_text().strip() == "2.3.4"
    assert json.loads(pkg.read_text())["version"] == "2.3.4"


def test_main_bump_only(tmp_path, monkeypatch, capsys):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
    vfile = tmp_path / "VERSION"
    vfile.write_text("2.0.0\n", encoding="utf-8")
    release.VERSION_FILE = str(vfile)
    release.PKG_JSON = str(pkg)

    monkeypatch.setattr("sys.argv", ["release.py", "patch"])
    assert release.main() == 0
    out = capsys.readouterr().out
    assert "2.0.0 -> 2.0.1" in out
    assert vfile.read_text().strip() == "2.0.1"
    # 未 --tag：不执行 git（提示性文案不算）
    assert "已提交并创建 tag" not in out


def test_main_invalid_version(tmp_path, monkeypatch):
    vfile = tmp_path / "VERSION"
    vfile.write_text("2.0.0\n", encoding="utf-8")
    release.VERSION_FILE = str(vfile)
    monkeypatch.setattr("sys.argv", ["release.py", "abc.def"])
    assert release.main() == 1


def test_main_tag_flow(tmp_path, monkeypatch, capsys):
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
    vfile = tmp_path / "VERSION"
    vfile.write_text("2.0.0\n", encoding="utf-8")
    release.VERSION_FILE = str(vfile)
    release.PKG_JSON = str(pkg)
    release.ROOT = str(tmp_path)

    calls = []

    def fake_run(cmd, cwd=None, check=None):
        calls.append((list(cmd), cwd))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr("scripts.release.subprocess.run", fake_run)
    monkeypatch.setattr("sys.argv", ["release.py", "patch", "--tag"])
    assert release.main() == 0
    out = capsys.readouterr().out
    assert "v2.0.1" in out
    cmds = [c for c, _ in calls]
    assert any("commit" in c for c in cmds)
    assert any("tag" in c for c in cmds)


def test_main_explicit_version(tmp_path, monkeypatch, capsys):
    vfile = tmp_path / "VERSION"
    vfile.write_text("2.0.0\n", encoding="utf-8")
    pkg = tmp_path / "package.json"
    pkg.write_text(json.dumps({"version": "2.0.0"}), encoding="utf-8")
    release.VERSION_FILE = str(vfile)
    release.PKG_JSON = str(pkg)
    monkeypatch.setattr("sys.argv", ["release.py", "9.9.9"])
    assert release.main() == 0
    assert vfile.read_text().strip() == "9.9.9"
