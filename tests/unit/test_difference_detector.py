"""difference/detector 测试：构造 / scan 全路径 / ingest / 回调 / 单例竞态"""
import sys
import threading
from unittest.mock import MagicMock

from config.settings import settings
from modules.perception.difference.detector import (
    DifferenceDetector,
    HIGH_INTENSITY_THRESHOLD,
    get_detector,
)
from modules.perception.difference.models import Difference


def _detector(**kw):
    d = DifferenceDetector.__new__(DifferenceDetector)
    d.registry = kw.get("registry", MagicMock())
    d.intensity_assigner = kw.get("intensity_assigner", MagicMock())
    d.repository = kw.get("repository", MagicMock())
    d._lock = kw.get("lock", threading.Lock())
    d._scan_count = kw.get("scan_count", 0)
    d._last_scan = kw.get("last_scan", 0.0)
    d._total_differences = kw.get("total", 0)
    d._high_intensity_callbacks = kw.get("callbacks", [])
    return d


def _src(diffs=None, source_type="fake"):
    s = MagicMock()
    s.source_type = source_type
    s.detect.return_value = diffs if diffs is not None else []
    return s


# ── __init__ ─────────────────────────────────────────────────────────────────

def test_init_registers_time_source():
    d = DifferenceDetector()
    assert "time" in d.registry.registered_types


# ── scan 全路径 ──────────────────────────────────────────────────────────────

def test_scan_collects_and_saves(monkeypatch):
    d = _detector()
    d._log_to_gcm = d._log_to_gcm  # 真实 pass 方法（108）
    d.registry.get_enabled_sources.return_value = [_src([Difference(category="a"), Difference(category="b")])]
    d.repository.dissolve_expired.return_value = 3
    results = d.scan()
    assert len(results) == 2
    d.intensity_assigner.assign_batch.assert_called_once()
    assert d.repository.save.call_count == 2
    assert d._scan_count == 1
    assert d._last_scan > 0
    assert d._total_differences == 2


def test_scan_source_error_continues():
    bad = MagicMock()
    bad.source_type = "bad"
    bad.detect.side_effect = RuntimeError("boom")
    good = _src([Difference()])
    d = _detector()
    d.registry.get_enabled_sources.return_value = [bad, good]
    d.repository.dissolve_expired.return_value = 0
    results = d.scan()
    assert len(results) == 1  # 坏源被跳过（72-73）


def test_scan_empty_differences():
    d = _detector()
    d.registry.get_enabled_sources.return_value = [_src([])]
    d.repository.dissolve_expired.return_value = 0
    results = d.scan()
    assert results == []
    assert d._scan_count == 1
    d.intensity_assigner.assign_batch.assert_not_called()  # 77 空列表不调用


def test_scan_save_error_logged():
    d = _detector()
    d.registry.get_enabled_sources.return_value = [_src([Difference()])]
    d.repository.save.side_effect = RuntimeError("disk full")
    d.repository.dissolve_expired.return_value = 0
    d.scan()  # 82-83 保存失败被捕获


def test_scan_log_to_gcm_error():
    d = _detector()
    d.registry.get_enabled_sources.return_value = [_src([Difference()])]
    d.repository.dissolve_expired.return_value = 0
    d._log_to_gcm = MagicMock(side_effect=RuntimeError("gcm down"))
    d.scan()  # 88-89


def test_scan_dissolve_error():
    d = _detector()
    d.registry.get_enabled_sources.return_value = [_src([Difference()])]
    d.repository.dissolve_expired.side_effect = RuntimeError("db down")
    d.scan()  # 94-96


def test_scan_fires_high_intensity_callback():
    seen = []

    def cb(diffs):
        seen.append(diffs)

    d = _detector()
    d.registry.get_enabled_sources.return_value = [_src([Difference(intensity=HIGH_INTENSITY_THRESHOLD)])]
    d.repository.dissolve_expired.return_value = 0
    d.on_high_intensity(cb)
    d.scan()
    assert seen and len(seen[0]) == 1


def test_scan_callback_error_suppressed():
    d = _detector()
    d.registry.get_enabled_sources.return_value = [_src([Difference(intensity=99.0)])]
    d.repository.dissolve_expired.return_value = 0

    def boom(diffs):
        raise RuntimeError("cb boom")

    d.on_high_intensity(boom)
    d.scan()  # 217-218 回调异常被捕获


# ── notify_activity ──────────────────────────────────────────────────────────

def test_notify_activity_skips_without_method():
    d = _detector()
    d.registry.get.return_value = object()  # 无 notify_activity → 140 False
    d.notify_activity()
    d.registry.get.return_value = None
    d.notify_activity()


def test_notify_activity_calls_source():
    calls = []

    class Src:
        def notify_activity(self):
            calls.append(1)

    d = _detector()
    d.registry.get.return_value = Src()
    d.notify_activity()
    assert calls == [1]


# ── ingest ───────────────────────────────────────────────────────────────────

def test_ingest_basic():
    d = _detector()
    d.intensity_assigner.assign.return_value = 10.0
    d.notify_activity = MagicMock()
    diff = d.ingest("file", "created", target="a.txt", details={"n": 1}, urgency=0.5)
    assert diff is not None
    assert diff.category == "file_created"
    assert diff.source_type == "perception"
    assert diff.intensity == 40.0  # 30 + 0.5*20，与 assign 取最大
    d.repository.save.assert_called_once()
    d.notify_activity.assert_called_once()


def test_ingest_unknown_category():
    d = _detector()
    d.intensity_assigner.assign.return_value = 5.0
    d.notify_activity = MagicMock()
    diff = d.ingest("weather", "raining", target="")
    assert diff.category == "weather_raining"


def test_ingest_save_error():
    d = _detector()
    d.intensity_assigner.assign.return_value = 10.0
    d.notify_activity = MagicMock()
    d.repository.save.side_effect = RuntimeError("save fail")
    diff = d.ingest("dialog", "created", target="x")
    assert diff is not None  # 188-189 保存失败仍返回


def test_ingest_high_intensity_fires_callback():
    seen = []

    def cb(diffs):
        seen.append(diffs)

    d = _detector()

    def fake_assign(diff):
        diff.intensity = 80.0  # 真实 assign 会回写 diff.intensity

    d.intensity_assigner.assign = fake_assign
    d.notify_activity = MagicMock()
    d.on_high_intensity(cb)
    diff = d.ingest("dialog", "created", target="x", urgency=1.0)
    assert diff.intensity >= HIGH_INTENSITY_THRESHOLD
    assert seen and seen[0][0].id == diff.id  # 195 fire 路径


# ── 查询类接口 ───────────────────────────────────────────────────────────────

_DIFF_DICT = {
    "id": "d1", "source_type": "time", "category": "idle_alert",
    "intensity": 55.0, "created_at": 100.0, "ttl": 3600.0,
    "payload": {}, "related_ids": [], "status": "active",
}


def test_get_active_and_typed():
    d = _detector()
    d.repository.get_active.return_value = [_DIFF_DICT]
    assert d.get_active(source_type="time", min_intensity=50) == [_DIFF_DICT]
    ads = d.get_active_differences(source_type="time")
    assert isinstance(ads[0], Difference)
    assert ads[0].id == "d1"


def test_get_history_and_status():
    d = _detector()
    d.repository.get_history.return_value = [_DIFF_DICT]
    d.repository.get_stats.return_value = {"total": 1, "active": 1, "incubating": 0, "dissolved": 0}
    d.registry.list_sources.return_value = [{"source_type": "time", "enabled": True}]
    d._scan_count = 3
    d._last_scan = 42.0
    d._total_differences = 7
    assert d.get_history(limit=5) == [_DIFF_DICT]
    st = d.get_status()
    assert st["initialized"] is True
    assert st["scan_count"] == 3
    assert st["last_scan"] == 42.0
    assert st["total_differences_detected"] == 7
    assert st["storage"]["total"] == 1


# ── 单例 ─────────────────────────────────────────────────────────────────────

def test_get_detector_race_inner(monkeypatch):
    import modules.perception.difference.detector as det_mod
    existing = det_mod.DifferenceDetector()
    monkeypatch.setattr(det_mod, "_detector_instance", None)

    class FakeLock:
        def __enter__(self):
            det_mod._detector_instance = existing
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(det_mod, "_detector_lock", FakeLock())
    assert get_detector() is existing  # 229->231


def test_get_detector_creates_when_none(monkeypatch):
    import modules.perception.difference.detector as det_mod
    monkeypatch.setattr(det_mod, "_detector_instance", None)
    monkeypatch.setattr(det_mod, "_detector_lock", threading.Lock())
    inst = get_detector()  # 230 内层创建
    assert inst is not None
    assert det_mod._detector_instance is inst


def test_get_detector_already_set(monkeypatch):
    import modules.perception.difference.detector as det_mod
    existing = det_mod.DifferenceDetector()
    monkeypatch.setattr(det_mod, "_detector_instance", existing)
    assert get_detector() is existing
