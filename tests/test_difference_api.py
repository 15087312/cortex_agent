"""difference/api 测试（此前 42% 覆盖）：差异检测 REST 端点"""
import asyncio
from unittest.mock import MagicMock, patch

import modules.perception.difference as diff_mod
from modules.perception.difference import api as api_mod


def _patch_deps(monkeypatch):
    det = MagicMock()
    det.get_status.return_value = {"enabled": True}
    det.get_active.return_value = [{"id": "d1", "intensity": 30}]
    det.get_history.return_value = []
    det.get_detail.return_value = {}
    det.get_sources.return_value = [{"type": "screen"}]
    det.registry = MagicMock()
    det.registry.enable.return_value = True
    det.registry.disable.return_value = True
    det.scan.return_value = []
    hb = MagicMock()
    hb.get_status.return_value = {"alive": True}
    sds = MagicMock()
    sds.get_status.return_value = {"running": False}
    monkeypatch.setattr(api_mod, "get_detector", lambda: det)
    monkeypatch.setattr(api_mod, "get_heartbeat", lambda: hb)
    monkeypatch.setattr(api_mod, "get_screen_diff_source", lambda: sds)
    return det, hb, sds


def test_get_detector_status(monkeypatch):
    det, hb, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_detector_status())
    assert out["success"] is True
    assert out["data"]["detector"]["enabled"] is True


def test_get_active_differences(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_active_differences(source_type=None, min_intensity=0, limit=50))
    assert out["data"]["count"] == 1
    det.get_active.assert_called_once()


def test_get_difference_history(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_difference_history(limit=10))
    assert out["success"] is True
    det.get_history.assert_called_once_with(limit=10)


def test_list_sources(monkeypatch):
    _, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.list_sources())
    assert out["success"] is True


def test_enable_and_disable_source(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    assert asyncio.run(api_mod.enable_source("screen"))["success"] is True
    assert asyncio.run(api_mod.disable_source("screen"))["success"] is True
    det.registry.enable.assert_called_once_with("screen")
    det.registry.disable.assert_called_once_with("screen")


def test_trigger_scan(monkeypatch):
    _, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.trigger_scan())
    assert "data" in out


def test_screen_diff_source_endpoints(monkeypatch):
    sds, _, _ = _patch_deps(monkeypatch)
    assert asyncio.run(api_mod.get_screen_diff_source_status())["success"] is True
    assert asyncio.run(api_mod.start_screen_diff_source())["success"] is True
    assert asyncio.run(api_mod.stop_screen_diff_source())["success"] is True
    assert asyncio.run(api_mod.restart_screen_diff_source())["success"] is True
