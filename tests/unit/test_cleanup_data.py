"""scripts/cleanup_data.py — 数据清理脚本"""
import os
import time
from datetime import datetime, timedelta

import pytest

import scripts.cleanup_data as cd


def _touch(path, days_old):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write("x")
    old = time.time() - days_old * 86400
    os.utime(path, (old, old))


def test_cleanup_temp_removes_old_keeps_new(tmp_path):
    _touch(str(tmp_path / "old.txt"), 30)
    _touch(str(tmp_path / "new.txt"), 1)
    cd.cleanup_temp(str(tmp_path), max_age_days=7)
    assert not os.path.exists(tmp_path / "old.txt")
    assert os.path.exists(tmp_path / "new.txt")


def test_cleanup_temp_missing_dir(tmp_path, capsys):
    cd.cleanup_temp(str(tmp_path / "nope"), max_age_days=7)  # 不抛异常
    assert "Cleaning" in capsys.readouterr().out


def test_cleanup_logs_only_removes_log(tmp_path):
    _touch(str(tmp_path / "a.log"), 60)
    _touch(str(tmp_path / "b.txt"), 60)
    cd.cleanup_logs(str(tmp_path), max_age_days=30)
    assert not os.path.exists(tmp_path / "a.log")
    assert os.path.exists(tmp_path / "b.txt")


def test_cleanup_cache_removes_old(tmp_path):
    _touch(str(tmp_path / "c.bin"), 10)
    cd.cleanup_cache(str(tmp_path), max_age_days=3)
    assert not os.path.exists(tmp_path / "c.bin")


def test_cleanup_logs_recent_kept(tmp_path):
    _touch(str(tmp_path / "recent.log"), 1)
    cd.cleanup_logs(str(tmp_path), max_age_days=30)
    assert os.path.exists(tmp_path / "recent.log")


def test_main_runs_all(monkeypatch, capsys):
    called = []
    for fn in ("cleanup_temp", "cleanup_logs", "cleanup_cache"):
        monkeypatch.setattr(cd, fn, lambda _dir="x", _age=1, _fn=fn: called.append(_fn))
    cd.main()
    assert called == ["cleanup_temp", "cleanup_logs", "cleanup_cache"]
    assert "Cleanup completed" in capsys.readouterr().out
