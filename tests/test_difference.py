"""差异检测子系统单元测试

覆盖:
  - Difference 数据模型
  - IntensityAssigner 强度分配
  - DifferenceRepository 持久化
  - DifferenceSource + DifferenceSourceRegistry
  - TimeDifferenceSource 空闲检测
  - ExistentialHeartbeat 心跳
  - DifferenceDetector 核心编排器
"""
import time
import threading
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from modules.perception.difference.models import Difference
from modules.perception.difference.intensity import IntensityAssigner, SOURCE_BASE, CATEGORY_MODIFIERS
from modules.perception.difference.repository import DifferenceRepository
from modules.perception.difference.sources.base import DifferenceSource, DifferenceSourceRegistry
from modules.perception.difference.sources.time_source import (
    TimeDifferenceSource,
    IDLE_WARNING_SECONDS,
    IDLE_CRITICAL_SECONDS,
    IDLE_TTL,
)
from modules.perception.difference.heartbeat import ExistentialHeartbeat
from modules.perception.difference.detector import DifferenceDetector, HIGH_INTENSITY_THRESHOLD


# ====================================================================
# Difference 数据模型
# ====================================================================

class TestDifferenceModel:
    def test_create_default(self):
        d = Difference()
        assert d.id.startswith("diff_")
        assert d.source_type == ""
        assert d.category == ""
        assert d.intensity == 0.0
        assert d.created_at > 0
        assert d.ttl == 3600.0
        assert d.payload == {}
        assert d.related_ids == []
        assert d.status == "active"

    def test_create_with_values(self):
        d = Difference(
            source_type="test",
            category="test_event",
            intensity=50.0,
            ttl=300,
            payload={"key": "value"},
            related_ids=["id1", "id2"],
        )
        assert d.source_type == "test"
        assert d.category == "test_event"
        assert d.intensity == 50.0
        assert d.ttl == 300
        assert d.payload == {"key": "value"}
        assert d.related_ids == ["id1", "id2"]

    def test_to_dict(self):
        d = Difference(
            source_type="test",
            category="test_event",
            intensity=75.5,
            payload={"data": 1},
        )
        data = d.to_dict()
        assert data["source_type"] == "test"
        assert data["category"] == "test_event"
        assert data["intensity"] == 75.5
        assert data["payload"] == {"data": 1}
        assert "id" in data
        assert "created_at" in data
        assert "status" in data
        assert data["status"] == "active"

    def test_unique_ids(self):
        d1 = Difference()
        d2 = Difference()
        assert d1.id != d2.id

    def test_status_dissolved(self):
        d = Difference()
        d.status = "dissolved"
        assert d.status == "dissolved"

    def test_payload_mutation(self):
        d = Difference(payload={"count": 0})
        d.payload["count"] = 5
        assert d.payload["count"] == 5


# ====================================================================
# IntensityAssigner
# ====================================================================

class TestIntensityAssigner:
    def setup_method(self):
        self.assigner = IntensityAssigner()

    def test_unknown_source_default_25(self):
        d = Difference(source_type="unknown_type")
        intensity = self.assigner.assign(d)
        assert 0 <= intensity <= 100
        assert intensity == 25.0

    def test_time_source_base(self):
        d = Difference(source_type="time")
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["time"]

    def test_user_input_high_base(self):
        d = Difference(source_type="user_input")
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["user_input"]

    def test_category_modifier(self):
        d = Difference(source_type="time", category="idle_critical")
        intensity = self.assigner.assign(d)
        expected = SOURCE_BASE["time"] + CATEGORY_MODIFIERS["idle_critical"]
        assert intensity == expected

    def test_category_prefix_match(self):
        d = Difference(source_type="time", category="idle_warning_extended")
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["time"] + CATEGORY_MODIFIERS["idle_warning"]

    def test_payload_idle_minutes_over_60(self):
        d = Difference(source_type="time", payload={"idle_minutes": 70})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["time"] + 15.0

    def test_payload_idle_minutes_over_30(self):
        d = Difference(source_type="time", payload={"idle_minutes": 40})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["time"] + 10.0

    def test_payload_idle_minutes_under_30(self):
        d = Difference(source_type="time", payload={"idle_minutes": 10})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["time"]

    def test_payload_unfinished_count(self):
        d = Difference(source_type="behavioral", payload={"unfinished_count": 5})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["behavioral"] + 15.0

    def test_payload_failed_count(self):
        d = Difference(source_type="behavioral", payload={"failed_count": 3})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["behavioral"] + 9.0

    def test_payload_ratio_high(self):
        d = Difference(source_type="internal", payload={"ratio": 5.0})
        intensity = self.assigner.assign(d)
        assert intensity > SOURCE_BASE["internal"]

    def test_payload_ratio_below_3(self):
        d = Difference(source_type="internal", payload={"ratio": 2.0})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["internal"]

    def test_payload_event_count_high(self):
        d = Difference(source_type="internal", payload={"event_count": 6000})
        intensity = self.assigner.assign(d)
        assert intensity > SOURCE_BASE["internal"]

    def test_payload_event_count_low(self):
        d = Difference(source_type="internal", payload={"event_count": 1000})
        intensity = self.assigner.assign(d)
        assert intensity == SOURCE_BASE["internal"]

    def test_intensity_capped_at_100(self):
        d = Difference(source_type="user_input", payload={"unfinished_count": 100})
        intensity = self.assigner.assign(d)
        assert intensity <= 100.0

    def test_intensity_floor_at_0(self):
        d = Difference(source_type="time", intensity=-50)
        intensity = self.assigner.assign(d)
        assert intensity >= 0.0

    def test_assign_batch_sorts_descending(self):
        diffs = [
            Difference(source_type="time", payload={"idle_minutes": 70}),
            Difference(source_type="internal"),
            Difference(source_type="user_input"),
        ]
        self.assigner.assign_batch(diffs)
        for i in range(len(diffs) - 1):
            assert diffs[i].intensity >= diffs[i + 1].intensity

    def test_assign_batch_sets_intensity(self):
        diffs = [Difference(source_type="time")]
        self.assigner.assign_batch(diffs)
        assert diffs[0].intensity > 0


# ====================================================================
# DifferenceSource + Registry
# ====================================================================

class _TestSource(DifferenceSource):
    @property
    def source_type(self) -> str:
        return "test_source"

    def detect(self):
        return [Difference(source_type="test_source", category="test_event")]


class TestDifferenceSource:
    def test_abstract_source_type(self):
        with pytest.raises(TypeError):
            DifferenceSource()

    def test_concrete_source(self):
        source = _TestSource()
        assert source.source_type == "test_source"
        assert source.enabled is True

    def test_enable_disable(self):
        source = _TestSource()
        source.enabled = False
        assert source.enabled is False
        source.enabled = True
        assert source.enabled is True

    def test_detect_returns_list(self):
        source = _TestSource()
        results = source.detect()
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0].source_type == "test_source"


class TestDifferenceSourceRegistry:
    def setup_method(self):
        self.registry = DifferenceSourceRegistry()

    def test_register_and_get(self):
        source = _TestSource()
        self.registry.register(source)
        assert self.registry.get("test_source") is source

    def test_get_nonexistent(self):
        assert self.registry.get("nope") is None

    def test_get_enabled_sources(self):
        s1 = _TestSource()
        s2 = type("_TestSource2", (_TestSource,), {"source_type": property(lambda self: "test_source2")})()
        self.registry.register(s1)
        self.registry.register(s2)
        s1.enabled = False
        enabled = self.registry.get_enabled_sources()
        assert s2 in enabled
        assert s1 not in enabled

    def test_enable_disable(self):
        source = _TestSource()
        self.registry.register(source)
        assert self.registry.disable("test_source") is True
        assert source.enabled is False
        assert self.registry.enable("test_source") is True
        assert source.enabled is True

    def test_enable_nonexistent(self):
        assert self.registry.enable("nope") is False
        assert self.registry.disable("nope") is False

    def test_list_sources(self):
        source = _TestSource()
        self.registry.register(source)
        sources = self.registry.list_sources()
        assert len(sources) == 1
        assert sources[0]["source_type"] == "test_source"
        assert sources[0]["enabled"] is True
        assert sources[0]["class"] == "_TestSource"

    def test_registered_types(self):
        source = _TestSource()
        self.registry.register(source)
        assert "test_source" in self.registry.registered_types


# ====================================================================
# DifferenceRepository
# ====================================================================

class TestDifferenceRepository:
    def setup_method(self):
        self.repo = DifferenceRepository()

    def _make_diff(self, **kwargs) -> Difference:
        params = dict(source_type="test", category="event", intensity=10.0, ttl=3600)
        params.update(kwargs)
        return Difference(**params)

    def test_save_and_get_by_id(self):
        d = self._make_diff()
        self.repo.save(d)
        found = self.repo.get_by_id(d.id)
        assert found is not None
        assert found["id"] == d.id

    def test_save_updates_existing(self):
        d = self._make_diff(intensity=10.0)
        self.repo.save(d)
        d.intensity = 50.0
        d.status = "dissolved"
        self.repo.save(d)
        found = self.repo.get_by_id(d.id)
        assert found["intensity"] == 50.0
        assert found["status"] == "dissolved"

    def test_get_by_id_nonexistent(self):
        assert self.repo.get_by_id("nonexistent") is None

    def test_get_active_returns_active_only(self):
        active = self._make_diff(status="active")
        dissolved = self._make_diff(status="dissolved")
        self.repo.save(active)
        self.repo.save(dissolved)
        results = self.repo.get_active()
        ids = [r["id"] for r in results]
        assert active.id in ids
        assert dissolved.id not in ids

    def test_get_active_filters_by_source_type(self):
        d1 = self._make_diff(source_type="type_a")
        d2 = self._make_diff(source_type="type_b")
        self.repo.save(d1)
        self.repo.save(d2)
        results = self.repo.get_active(source_type="type_a")
        assert len(results) == 1
        assert results[0]["source_type"] == "type_a"

    def test_get_active_filters_by_min_intensity(self):
        d1 = self._make_diff(intensity=10.0)
        d2 = self._make_diff(intensity=50.0)
        self.repo.save(d1)
        self.repo.save(d2)
        results = self.repo.get_active(min_intensity=30.0)
        assert len(results) == 1
        assert results[0]["intensity"] == 50.0

    def test_get_active_ignores_expired(self):
        d = self._make_diff(ttl=0.001)
        self.repo.save(d)
        time.sleep(0.01)
        results = self.repo.get_active()
        assert d.id not in [r["id"] for r in results]

    def test_get_active_sorts_by_intensity(self):
        d1 = self._make_diff(intensity=10.0)
        d2 = self._make_diff(intensity=80.0)
        d3 = self._make_diff(intensity=50.0)
        self.repo.save(d1)
        self.repo.save(d2)
        self.repo.save(d3)
        results = self.repo.get_active()
        intensities = [r["intensity"] for r in results]
        assert intensities == sorted(intensities, reverse=True)

    def test_get_active_limits(self):
        for i in range(10):
            self.repo.save(self._make_diff(intensity=float(i)))
        results = self.repo.get_active(limit=3)
        assert len(results) == 3

    def test_get_history_returns_all(self):
        for i in range(5):
            self.repo.save(self._make_diff())
        history = self.repo.get_history(limit=10)
        assert len(history) == 5

    def test_get_history_newest_first(self):
        self.repo.save(self._make_diff(category="older"))
        time.sleep(0.01)
        self.repo.save(self._make_diff(category="newer"))
        history = self.repo.get_history()
        assert history[0]["category"] == "newer"

    def test_dissolve_expired(self):
        d = self._make_diff(ttl=0.001)
        self.repo.save(d)
        time.sleep(0.01)
        count = self.repo.dissolve_expired()
        assert count >= 1
        assert self.repo.get_by_id(d.id)["status"] == "dissolved"

    def test_dissolve_expired_skips_active(self):
        d = self._make_diff(ttl=3600)
        self.repo.save(d)
        count = self.repo.dissolve_expired()
        assert count == 0

    def test_dissolve_by_id(self):
        d = self._make_diff()
        self.repo.save(d)
        assert self.repo.dissolve_by_id(d.id) is True
        assert self.repo.get_by_id(d.id)["status"] == "dissolved"

    def test_dissolve_by_id_nonexistent(self):
        assert self.repo.dissolve_by_id("nope") is False

    def test_get_stats(self):
        active = self._make_diff(status="active")
        dissolved = self._make_diff(status="dissolved")
        self.repo.save(active)
        self.repo.save(dissolved)
        stats = self.repo.get_stats()
        assert stats["total"] == 2
        assert stats["active"] == 1
        assert stats["dissolved"] == 1

    def test_get_stats_incubating(self):
        d = self._make_diff(status="incubating")
        self.repo.save(d)
        stats = self.repo.get_stats()
        assert stats["incubating"] == 1


# ====================================================================
# TimeDifferenceSource
# ====================================================================

class TestTimeDifferenceSource:
    def setup_method(self):
        self.source = TimeDifferenceSource()

    def test_source_type(self):
        assert self.source.source_type == "time"

    def test_detect_returns_empty_initially(self):
        results = self.source.detect()
        assert results == []

    def test_detect_returns_warning_after_idle(self):
        self.source._last_activity = time.time() - IDLE_WARNING_SECONDS - 1
        results = self.source.detect()
        assert len(results) == 1
        assert results[0].category == "idle_warning"
        assert results[0].source_type == "time"

    def test_detect_returns_critical_after_long_idle(self):
        self.source._last_activity = time.time() - IDLE_CRITICAL_SECONDS - 1
        results = self.source.detect()
        assert len(results) == 1
        assert results[0].category == "idle_critical"

    def test_detect_does_not_duplicate_same_level(self):
        self.source._last_activity = time.time() - IDLE_WARNING_SECONDS - 1
        first = self.source.detect()
        assert len(first) == 1

        second = self.source.detect()
        assert second == []

    def test_detect_escalates_from_warning_to_alert(self):
        self.source._last_reported_category = "idle_warning"
        self.source._last_activity = time.time() - _get_alert_seconds() - 1
        results = self.source.detect()
        assert len(results) == 1
        assert results[0].category == "idle_alert"

    def test_notify_activity_resets(self):
        self.source._last_activity = time.time() - IDLE_WARNING_SECONDS - 1
        self.source.detect()
        self.source.notify_activity()
        results = self.source.detect()
        assert results == []

    def test_idle_seconds_property(self):
        self.source._last_activity = time.time() - 100
        assert 99 <= self.source.idle_seconds <= 101

    def test_intensity_values(self):
        self.source._last_activity = time.time() - IDLE_WARNING_SECONDS - 1
        results = self.source.detect()
        assert results[0].intensity == 30.0

        self.source._last_activity = time.time() - IDLE_CRITICAL_SECONDS - 1
        results = self.source.detect()
        assert results[0].intensity == 55.0

    def test_ttl_is_one_hour(self):
        self.source._last_activity = time.time() - IDLE_WARNING_SECONDS - 1
        results = self.source.detect()
        assert results[0].ttl == IDLE_TTL

    def test_payload_contains_idle_info(self):
        self.source._last_activity = time.time() - IDLE_WARNING_SECONDS - 1
        results = self.source.detect()
        payload = results[0].payload
        assert "idle_seconds" in payload
        assert "idle_minutes" in payload
        assert "threshold" in payload


def _get_alert_seconds():
    try:
        from config.settings import settings
        return settings.PROACTIVE_OUTREACH_IDLE_MINUTES * 60
    except Exception:
        return 15 * 60


# ====================================================================
# ExistentialHeartbeat
# ====================================================================

class TestExistentialHeartbeat:
    def test_initial_state(self):
        hb = ExistentialHeartbeat()
        assert hb.is_running is False
        assert hb.beat_count == 0
        assert hb.uptime == 0.0

    @patch("modules.perception.difference.detector.get_detector")
    def test_start_stop(self, mock_get_detector):
        mock_detector = MagicMock()
        mock_detector.scan = MagicMock(return_value=[])
        mock_get_detector.return_value = mock_detector

        hb = ExistentialHeartbeat()
        hb.start()
        assert hb.is_running is True
        time.sleep(0.1)
        hb.stop()
        assert hb.is_running is False

    def test_idempotent_start(self):
        hb = ExistentialHeartbeat()
        hb.start(detector=MagicMock())
        hb.start(detector=MagicMock())
        assert hb.is_running is True
        hb.stop()

    def test_idempotent_stop(self):
        hb = ExistentialHeartbeat()
        hb.stop()
        assert hb.is_running is False

    def test_uptime_increases(self):
        hb = ExistentialHeartbeat()
        hb._started_at = time.time() - 5
        assert 4.5 <= hb.uptime <= 5.5

    def test_uptime_not_started(self):
        hb = ExistentialHeartbeat()
        assert hb.uptime == 0.0

    def test_get_status(self):
        hb = ExistentialHeartbeat()
        hb._beat_count = 10
        hb._started_at = time.time() - 60
        status = hb.get_status()
        assert status["running"] is False
        assert status["beat_count"] == 10
        assert 59 <= status["uptime_seconds"] <= 61
        assert status["interval"] == 1.0

    @patch("modules.perception.difference.detector.get_detector")
    def test_scan_called_on_beat(self, mock_get_detector):
        mock_detector = MagicMock()
        mock_detector.scan = MagicMock(return_value=[])
        mock_get_detector.return_value = mock_detector

        hb = ExistentialHeartbeat()
        hb._interval = 0.05
        hb.start()
        time.sleep(0.12)
        hb.stop()
        assert hb.beat_count >= 2

    def test_get_heartbeat_singleton(self):
        from modules.perception.difference.heartbeat import get_heartbeat
        h1 = get_heartbeat()
        h2 = get_heartbeat()
        assert h1 is h2
        h1.stop()


# ====================================================================
# DifferenceDetector
# ====================================================================

class TestDifferenceDetector:
    def setup_method(self):
        self.detector = DifferenceDetector()

    def test_init_registers_time_source(self):
        assert "time" in self.detector.registry.registered_types

    def test_scan_returns_list(self):
        results = self.detector.scan()
        assert isinstance(results, list)

    def test_scan_increments_count(self):
        before = self.detector._scan_count
        self.detector.scan()
        assert self.detector._scan_count == before + 1

    def test_get_active_returns_list(self):
        results = self.detector.get_active()
        assert isinstance(results, list)

    def test_get_history_returns_list(self):
        results = self.detector.get_history()
        assert isinstance(results, list)

    def test_get_status_returns_dict(self):
        status = self.detector.get_status()
        assert status["initialized"] is True
        assert "scan_count" in status
        assert "storage" in status
        assert "sources" in status

    def test_get_status_sources(self):
        status = self.detector.get_status()
        sources = status["sources"]
        assert len(sources) >= 1
        assert sources[0]["source_type"] == "time"

    def test_notify_activity_calls_time_source(self):
        mock_source = MagicMock()
        mock_source.source_type = "time"
        self.detector.registry.register(mock_source)
        self.detector.notify_activity()
        mock_source.notify_activity.assert_called_once()

    def test_ingest_creates_difference(self):
        diff = self.detector.ingest("file", "created", target="/tmp/test")
        assert diff is not None
        assert diff.source_type == "perception"
        assert diff.category == "file_created"
        assert diff.intensity > 0

    def test_ingest_unknown_mapping(self):
        diff = self.detector.ingest("unknown", "event")
        assert diff is not None
        assert diff.category == "unknown_event"

    def test_ingest_increases_total(self):
        before = self.detector._total_differences
        self.detector.ingest("screen", "changed")
        assert self.detector._total_differences == before + 1

    def test_ingest_saves_to_repository(self):
        diff = self.detector.ingest("file", "deleted")
        found = self.detector.repository.get_by_id(diff.id)
        assert found is not None

    def test_ingest_resets_idle(self):
        time_source = self.detector.registry.get("time")
        time_source._last_activity = time.time() - 1000
        self.detector.ingest("file", "created")
        assert time_source.idle_seconds < 5

    def test_on_high_intensity_callback_fired(self):
        callback = MagicMock()
        self.detector.on_high_intensity(callback)
        diff = Difference(source_type="test", category="high", intensity=HIGH_INTENSITY_THRESHOLD + 10)
        self.detector._fire_high_intensity_callbacks([diff])
        callback.assert_called_once()

    def test_on_high_intensity_below_threshold(self):
        callback = MagicMock()
        self.detector.on_high_intensity(callback)
        diff = Difference(source_type="test", intensity=10.0)
        self.detector._fire_high_intensity_callbacks([diff])
        callback.assert_not_called()

    def test_on_high_intensity_multiple_callbacks(self):
        cb1 = MagicMock()
        cb2 = MagicMock()
        self.detector.on_high_intensity(cb1)
        self.detector.on_high_intensity(cb2)
        diff = Difference(source_type="test", intensity=HIGH_INTENSITY_THRESHOLD + 10)
        self.detector._fire_high_intensity_callbacks([diff])
        cb1.assert_called_once()
        cb2.assert_called_once()

    def test_ingest_triggers_callback_on_high_intensity(self):
        callback = MagicMock()
        self.detector.on_high_intensity(callback)
        self.detector.ingest("file", "deleted", urgency=1.0)
        callback.assert_called_once()

    def test_scan_with_disabled_source(self):
        self.detector.registry.disable("time")
        results = self.detector.scan()
        assert results == []

    def test_get_active_differences_returns_objects(self):
        self.detector.ingest("file", "created")
        diffs = self.detector.get_active_differences()
        assert len(diffs) >= 1
        assert isinstance(diffs[0], Difference)

    def test_scan_catches_source_exception(self):
        bad_source = MagicMock()
        bad_source.source_type = "bad"
        bad_source.enabled = True
        bad_source.detect.side_effect = RuntimeError("boom")
        self.detector.registry.register(bad_source)
        self.detector.scan()


# ====================================================================
# get_detector 单例
# ====================================================================

class TestGetDetector:
    def test_singleton(self):
        from modules.perception.difference.detector import get_detector
        d1 = get_detector()
        d2 = get_detector()
        assert d1 is d2
