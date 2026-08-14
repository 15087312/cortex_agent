"""perception/difference/api 补充测试：详情/启停失败/心跳/截屏端点"""
import asyncio
from unittest.mock import MagicMock

import pytest

from api.errors import AppError, ErrorCode
import modules.perception.difference.api as api_mod


def _patch_deps(monkeypatch):
    det = MagicMock()
    det.get_status.return_value = {"enabled": True}
    det.get_active.return_value = [{"id": "d1", "intensity": 30}]
    det.get_history.return_value = []
    det.repository = MagicMock()
    det.repository.get_by_id.return_value = {"id": "d1"}
    det.registry = MagicMock()
    det.registry.list_sources.return_value = [{"type": "screen"}]
    det.registry.enable.return_value = True
    det.registry.disable.return_value = True
    hb = MagicMock()
    hb.get_status.return_value = {"alive": True, "last_beat": 1.0}
    sds = MagicMock()
    sds.get_stats.return_value = {"captures": 3}
    sds.capture.return_value = {"frames": 1}
    sds.capture_screenshot.return_value = {"base64": "AAAA", "mime": "image/png"}
    monkeypatch.setattr(api_mod, "get_detector", lambda: det)
    monkeypatch.setattr(api_mod, "get_heartbeat", lambda: hb)
    monkeypatch.setattr(api_mod, "get_screen_diff_source", lambda: sds)
    return det, hb, sds


def test_get_difference_found(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_difference("d1"))
    assert out["data"] == {"id": "d1"}
    det.repository.get_by_id.assert_called_once_with("d1")


def test_get_difference_not_found(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    det.repository.get_by_id.return_value = None
    with pytest.raises(AppError) as ei:
        asyncio.run(api_mod.get_difference("missing"))
    assert ei.value.code == ErrorCode.NOT_FOUND


def test_enable_source_not_found(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    det.registry.enable.return_value = False
    with pytest.raises(AppError) as ei:
        asyncio.run(api_mod.enable_source("ghost"))
    assert ei.value.code == ErrorCode.NOT_FOUND
    det.registry.enable.assert_called_once_with("ghost")


def test_disable_source_not_found(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    det.registry.disable.return_value = False
    with pytest.raises(AppError) as ei:
        asyncio.run(api_mod.disable_source("ghost"))
    assert ei.value.code == ErrorCode.NOT_FOUND


def test_get_heartbeat_status(monkeypatch):
    _, hb, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_heartbeat_status())
    assert out["success"] is True
    assert out["data"]["alive"] is True
    hb.get_status.assert_called_once()


def test_capture_screen_snapshot(monkeypatch):
    _, _, sds = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.capture_screen_snapshot())
    assert out["data"]["result"] == {"frames": 1}
    assert out["data"]["stats"] == {"captures": 3}
    sds.capture.assert_called_once()


def test_get_detector_status(monkeypatch):
    det, hb, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_detector_status())
    assert out["success"] is True
    assert out["data"]["detector"]["enabled"] is True
    assert out["data"]["heartbeat"]["alive"] is True


def test_get_active_differences(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(
        api_mod.get_active_differences(source_type=None, min_intensity=0, limit=50)
    )
    assert out["data"]["count"] == 1
    det.get_active.assert_called_once()


def test_get_difference_history(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_difference_history(limit=10))
    assert out["success"] is True
    det.get_history.assert_called_once_with(limit=10)


def test_list_sources(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.list_sources())
    assert out["data"]["sources"] == det.registry.list_sources.return_value


def test_enable_source_success(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.enable_source("screen"))
    assert out["data"]["message"] == "差异源 screen 已启用"
    det.registry.enable.assert_called_once_with("screen")


def test_disable_source_success(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.disable_source("screen"))
    assert out["data"]["message"] == "差异源 screen 已禁用"
    det.registry.disable.assert_called_once_with("screen")


def test_start_screen_diff_source(monkeypatch):
    _, _, sds = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.start_screen_diff_source())
    sds.start.assert_called_once()
    assert out["data"]["stats"] == {"captures": 3}


def test_stop_screen_diff_source(monkeypatch):
    _, _, sds = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.stop_screen_diff_source())
    sds.stop.assert_called_once()
    assert out["data"]["message"] == "屏幕差异源已停止"


def test_restart_screen_diff_source(monkeypatch):
    _, _, sds = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.restart_screen_diff_source())
    sds.stop.assert_called_once()
    sds.start.assert_called_once()
    assert out["data"]["message"] == "屏幕差异源已重启"


def test_get_screen_screenshot(monkeypatch):
    _, _, sds = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_screen_screenshot())
    assert out["data"]["base64"] == "AAAA"
    sds.capture_screenshot.assert_called_once()


def test_get_screen_diff_source_status(monkeypatch):
    _, _, sds = _patch_deps(monkeypatch)
    out = asyncio.run(api_mod.get_screen_diff_source_status())
    assert out["data"] == {"captures": 3}
    sds.get_stats.assert_called_once()


def test_scan_returns_diffs(monkeypatch):
    det, _, _ = _patch_deps(monkeypatch)
    item = MagicMock()
    item.to_dict.return_value = {"id": "x"}
    det.scan.return_value = [item, item]
    out = asyncio.run(api_mod.trigger_scan())
    assert out["data"]["differences_found"] == 2
