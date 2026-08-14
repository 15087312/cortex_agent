"""modules/management/core/collector 补充测试：各收集器成功/失败降级"""
import importlib
import sqlite3
from unittest.mock import MagicMock

import pytest

import modules.management.core.collector as coll_mod
from modules.management.core.collector import ModuleRegistry, StatusCollector

_disk_cache_mod = importlib.import_module("modules.database.disk_cache")


def test_update_status_without_info():
    r = ModuleRegistry()
    r.update_status("thinking", "healthy")  # info 为 None → 不覆盖 info
    assert r.modules["thinking"].status == "healthy"


def test_collect_all_collector_error():
    r = ModuleRegistry()
    sc = StatusCollector(r)

    def boom():
        raise RuntimeError("collector crash")

    sc._collectors["thinking"] = boom
    results = sc.collect_all()
    assert results["thinking"]["status"] == "error"
    assert results["thinking"]["error"] == "Module collection failed"
    # 其它模块不受影响
    assert results["memory"]["status"] == "healthy"


def test_collect_all_generic():
    """注册表中无对应收集器的模块 → 走 _collect_generic 兜底"""
    from modules.management.core.collector import ModuleInfo

    r = ModuleRegistry()
    r.modules["unknown_mod"] = ModuleInfo(name="unknown_mod", module_path="/x", has_api=True)
    sc = StatusCollector(r)
    results = sc.collect_all()
    assert results["unknown_mod"]["status"] == "available"
    assert results["unknown_mod"]["has_api"] is True


def test_collect_attention_ok():
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_attention()
    assert out["status"] == "healthy"
    assert "weight_calculation" in out["capabilities"]


def test_collect_info_process_ok(monkeypatch):
    import infra.data_process.core.image_analyzer as ia_mod
    import infra.data_process.core.speech_recognizer as sr_mod

    class FakeAnalyzer:
        model_type = "openai"
        _initialized = True

    class FakeRecognizer:
        model_name = "whisper"
        _initialized = False

    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda: FakeAnalyzer())
    monkeypatch.setattr(sr_mod, "SpeechRecognizer", lambda: FakeRecognizer())
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_info_process()
    assert out["status"] == "healthy"
    assert out["image_analyzer"]["type"] == "openai"


def test_collect_info_process_error(monkeypatch):
    import infra.data_process.core.image_analyzer as ia_mod

    monkeypatch.setattr(ia_mod, "ImageAnalyzer", lambda: (_ for _ in ()).throw(RuntimeError("no")))
    sc = StatusCollector(ModuleRegistry())
    assert sc._collect_info_process()["status"] == "error"


def test_collect_perception_ok(monkeypatch):
    import modules.management.core.interfaces as ifaces

    port = MagicMock()
    port.get_status.return_value = {"status": "healthy", "started": True}
    monkeypatch.setattr(ifaces, "get_perception_status_port", lambda: port)
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_perception()
    assert out["status"] == "healthy"


def test_collect_perception_error(monkeypatch):
    import modules.management.core.interfaces as ifaces

    def boom():
        raise RuntimeError("perception down")

    monkeypatch.setattr(ifaces, "get_perception_status_port", boom)
    sc = StatusCollector(ModuleRegistry())
    assert sc._collect_perception()["status"] == "error"


def test_collect_security_ok(monkeypatch):
    import modules.management.core.interfaces as ifaces

    port = MagicMock()
    port.get_status.return_value = {"status": "healthy", "audit_enabled": True}
    monkeypatch.setattr(ifaces, "get_security_status_port", lambda: port)
    sc = StatusCollector(ModuleRegistry())
    assert sc._collect_security()["status"] == "healthy"


def test_collect_security_error(monkeypatch):
    import modules.management.core.interfaces as ifaces

    def boom():
        raise RuntimeError("security down")

    monkeypatch.setattr(ifaces, "get_security_status_port", boom)
    sc = StatusCollector(ModuleRegistry())
    assert sc._collect_security()["status"] == "error"


def test_collect_output_ok():
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_output()
    assert out["status"] == "healthy"
    assert "archiver" in out["capabilities"]


def test_collect_tool_manager_ok():
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_tool_manager()
    assert out["status"] == "healthy"


def test_collect_database_ok(monkeypatch):
    monkeypatch.setattr(_disk_cache_mod.disk_cache, "get_stats", lambda: {"mode": "disk", "size": 10})
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_database()
    assert out["status"] == "healthy"
    assert out["cache_mode"] == "disk"


def test_collect_database_with_tables(monkeypatch, tmp_path):
    db = tmp_path / "data" / "memory.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE foo (id INTEGER)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(coll_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_disk_cache_mod.disk_cache, "get_stats", lambda: {"mode": "disk", "size": 1})
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_database()
    assert out["status"] == "healthy"
    assert out["tables"] == ["foo"]
    assert out["row_counts"] == {"foo": 0}


def test_collect_database_connect_fail(monkeypatch, tmp_path):
    """SQLite 连接失败 → 降级为 healthy + 空表列表（内部异常被吞）"""
    monkeypatch.setattr(coll_mod, "PROJECT_ROOT", tmp_path / "nonexistent")
    monkeypatch.setattr(_disk_cache_mod.disk_cache, "get_stats", lambda: {"mode": "disk"})
    sc = StatusCollector(ModuleRegistry())
    out = sc._collect_database()
    assert out["status"] == "healthy"
    assert out["tables"] == []


def test_collect_database_error(monkeypatch):
    monkeypatch.setattr(_disk_cache_mod.disk_cache, "get_stats", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    sc = StatusCollector(ModuleRegistry())
    assert sc._collect_database()["status"] == "error"
