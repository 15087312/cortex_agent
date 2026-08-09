"""management/core/collector 测试（此前 44% 覆盖）：模块注册表与状态收集"""
import asyncio
from unittest.mock import MagicMock, patch

from modules.management.core.collector import ModuleInfo, ModuleRegistry, StatusCollector


def test_module_info_defaults():
    m = ModuleInfo(name="x", module_path="/tmp/x")
    assert m.has_api is False
    assert m.status == "discovered"
    assert m.capabilities == []


def test_registry_discover():
    r = ModuleRegistry()
    assert "thinking" in r.modules
    assert "perception" in r.modules
    assert all(hasattr(m, "name") for m in r.modules.values())


def test_registry_get_and_update():
    r = ModuleRegistry()
    info = r.get_module("thinking")
    assert info is not None
    assert len(r.get_all_modules()) == len(r.modules)
    r.update_status("thinking", "healthy", {"extra": 1})
    assert r.modules["thinking"].status == "healthy"
    assert r.modules["thinking"].info == {"extra": 1}
    r.update_status("不存在", "healthy")  # 不影响


def test_status_collector_collect_all():
    r = ModuleRegistry()
    sc = StatusCollector(r)
    results = sc.collect_all()
    assert "memory" in results
    assert results["memory"]["status"] == "healthy"
    assert results["thinking"]["status"] == "healthy"
    assert "attention" in results


def test_status_collector_generic():
    r = ModuleRegistry()
    sc = StatusCollector(r)
    out = sc._collect_generic("thinking")
    assert out["status"] == "available"
    assert "has_api" in out
