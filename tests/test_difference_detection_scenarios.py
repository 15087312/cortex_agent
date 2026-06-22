#!/usr/bin/env python3
"""
差异检测系统 — 场景测试

测试目标：
1. 各种场景是否正确触发差异检测
2. 触发后的强度计算是否合理
3. 高强度差异是否触发回调
4. 空闲级别升级/降级是否正确
"""
import sys
import os
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _dump_diff(diff):
    d = diff.to_dict() if hasattr(diff, "to_dict") else diff
    print(f"    {DIM}id:{RESET} {d['id']}")
    print(f"    {DIM}source:{RESET} {d['source_type']}  {DIM}category:{RESET} {d['category']}")
    print(f"    {DIM}intensity:{RESET} {d['intensity']:.1f}  {DIM}ttl:{RESET} {d['ttl']}s")
    if d.get("payload"):
        for k, v in d["payload"].items():
            print(f"    {DIM}{k}:{RESET} {v}")


def _dump_status(status):
    print(f"    {DIM}scan_count:{RESET} {status['scan_count']}")
    print(f"    {DIM}total_detected:{RESET} {status['total_differences_detected']}")
    print(f"    {DIM}sources:{RESET} {status['sources']}")
    storage = status.get("storage", {})
    if storage:
        print(f"    {DIM}storage:{RESET} active={storage.get('active', '?')}, "
              f"dissolved={storage.get('dissolved', '?')}, "
              f"total={storage.get('total', '?')}")


# ======================================================================
# 场景 1: 时间空闲检测 — 模拟 5 分钟空闲 → idle_warning
# ======================================================================
def test_time_idle_warning():
    from modules.perception.difference.sources.time_source import (
        TimeDifferenceSource,
        IDLE_WARNING_SECONDS,
    )

    src = TimeDifferenceSource()
    src._last_activity = time.time() - IDLE_WARNING_SECONDS

    diffs = src.detect()
    assert diffs, "idle_warning 未触发：5 分钟空闲应产生至少一个差异"
    for d in diffs:
        _dump_diff(d)
        assert d.intensity >= 0, "差异强度应非负"


# ======================================================================
# 场景 2: 时间空闲检测 — 模拟 30 分钟空闲 → idle_critical
# ======================================================================
def test_time_idle_critical():
    from modules.perception.difference.sources.time_source import (
        TimeDifferenceSource,
        IDLE_CRITICAL_SECONDS,
    )

    src = TimeDifferenceSource()
    src._last_activity = time.time() - IDLE_CRITICAL_SECONDS

    diffs = src.detect()
    assert diffs, "idle_critical 未触发：30 分钟空闲应产生至少一个差异"
    for d in diffs:
        _dump_diff(d)
        assert d.intensity >= 0, "差异强度应非负"


# ======================================================================
# 场景 3: 空闲级别升级 — warning → critical 不会重复触发 warning
# ======================================================================
def test_idle_level_escalation():
    from modules.perception.difference.sources.time_source import (
        TimeDifferenceSource,
        IDLE_WARNING_SECONDS,
        IDLE_CRITICAL_SECONDS,
    )

    src = TimeDifferenceSource()

    # 第一次：5 分钟空闲 → idle_warning
    src._last_activity = time.time() - IDLE_WARNING_SECONDS
    d1 = src.detect()
    assert d1, "首次 5 分钟空闲应触发"

    # 第二次：同一级别，不应重复触发
    d1_again = src.detect()
    assert len(d1_again) == 0, f"同级别重复触发了 {len(d1_again)} 个差异"

    # 第三次：升级到 30 分钟 → idle_critical
    src._last_activity = time.time() - IDLE_CRITICAL_SECONDS
    d2 = src.detect()
    assert d2, "升级到 idle_critical 应产生差异"
    assert d2[0].category == "idle_critical", (
        f"应升级到 idle_critical, got: {d2[0].category}"
    )


# ======================================================================
# 场景 4: 用户活动重置空闲计时
# ======================================================================
def test_activity_resets_idle():
    from modules.perception.difference.sources.time_source import (
        TimeDifferenceSource,
        IDLE_WARNING_SECONDS,
    )

    src = TimeDifferenceSource()
    src._last_activity = time.time() - IDLE_WARNING_SECONDS
    d1 = src.detect()
    assert d1, "前置条件失败：空闲应触发"

    src.notify_activity()
    assert src.idle_seconds < 1.0, (
        f"notify_activity 后空闲时间应接近 0, got {src.idle_seconds:.1f}s"
    )

    d2 = src.detect()
    assert len(d2) == 0, f"活动后仍触发了 {len(d2)} 个差异"


# ======================================================================
# 场景 5: ingest — 文件创建事件
# ======================================================================
def test_ingest_file_created():
    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="file",
        change_type="created",
        target="src/new_module.py",
        details={"lines": 50, "size": 1024},
        urgency=0.8,
    )

    assert diff is not None, "ingest 文件创建事件返回 None"
    _dump_diff(diff)
    assert diff.intensity >= 0, "差异强度应非负"


# ======================================================================
# 场景 6: ingest — 文件删除事件 (高优先级)
# ======================================================================
def test_ingest_file_deleted():
    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="file",
        change_type="deleted",
        target="src/important.py",
        details={"backup": False},
        urgency=0.9,
    )

    assert diff is not None, "ingest 文件删除事件返回 None"
    _dump_diff(diff)
    assert diff.intensity >= 0, "差异强度应非负"


# ======================================================================
# 场景 7: ingest — 用户发送对话消息
# ======================================================================
def test_ingest_dialog_created():
    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="dialog",
        change_type="created",
        target="user_message",
        details={"text": "帮我重构认证模块"},
        urgency=1.0,
    )

    assert diff is not None, "ingest 对话消息返回 None"
    _dump_diff(diff)
    assert diff.intensity >= 0, "差异强度应非负"


# ======================================================================
# 场景 8: ingest — 屏幕变化事件
# ======================================================================
def test_ingest_screen_changed():
    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="screen",
        change_type="changed",
        target="screen",
        details={"app": "VSCode", "window": "main.py"},
        urgency=0.5,
    )

    assert diff is not None, "ingest 屏幕变化返回 None"
    _dump_diff(diff)
    assert diff.intensity >= 0, "差异强度应非负"


# ======================================================================
# 场景 9: 高强度回调触发
# ======================================================================
def test_high_intensity_callback():
    from modules.perception.difference.detector import (
        get_detector,
        HIGH_INTENSITY_THRESHOLD,
    )

    detector = get_detector()
    callback_results = []

    def my_callback(differences):
        callback_results.extend(differences)

    detector.on_high_intensity(my_callback)

    diff = detector.ingest(
        target_type="file",
        change_type="deleted",
        target="critical_config.yaml",
        urgency=1.0,
    )

    assert diff is not None, "ingest 返回 None"

    if diff.intensity >= HIGH_INTENSITY_THRESHOLD:
        assert callback_results, (
            f"强度 {diff.intensity:.1f} >= {HIGH_INTENSITY_THRESHOLD} 但回调未触发"
        )
    else:
        assert not callback_results, (
            f"强度 {diff.intensity:.1f} < {HIGH_INTENSITY_THRESHOLD} 但回调被触发"
        )


# ======================================================================
# 场景 10: 扫描检测器完整流程
# ======================================================================
def test_detector_scan():
    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    detector.notify_activity()

    diffs = detector.scan()
    assert isinstance(diffs, list), f"scan() 应返回 list, got {type(diffs)}"

    active = detector.get_active(limit=10)
    assert isinstance(active, list), f"get_active() 应返回 list, got {type(active)}"

    status = detector.get_status()
    _dump_status(status)
    assert "scan_count" in status, "状态缺少 scan_count"
    assert "total_differences_detected" in status, "状态缺少 total_differences_detected"
    assert "sources" in status, "状态缺少 sources"
    assert status["scan_count"] >= 1, "scan() 调用后 scan_count 应至少为 1"


# ======================================================================
# 场景 11: ingest 后查看持久化结果
# ======================================================================
def test_ingest_and_query():
    from modules.perception.difference.detector import get_detector

    detector = get_detector()

    events = [
        ("file", "created", "src/a.py", 0.6),
        ("file", "modified", "src/b.py", 0.4),
        ("dialog", "created", "user_msg", 1.0),
    ]

    ingested = []
    for tt, ct, target, urgency in events:
        diff = detector.ingest(target_type=tt, change_type=ct, target=target, urgency=urgency)
        assert diff is not None, f"ingest({tt}, {ct}) 返回 None"
        ingested.append(diff)

    active = detector.get_active(limit=20)
    assert len(active) >= len(ingested), (
        f"摄入 {len(ingested)} 个事件，活跃差异只有 {len(active)} 个"
    )

    perception_only = detector.get_active(source_type="perception", limit=10)
    assert isinstance(perception_only, list), "source_type 筛选应返回 list"
    for d in perception_only:
        assert d["source_type"] == "perception", (
            f"source_type 筛选未生效, got {d['source_type']}"
        )


# ======================================================================
# 场景 12: 心跳启动/停止
# ======================================================================
def test_heartbeat_lifecycle():
    from modules.perception.difference.heartbeat import ExistentialHeartbeat

    hb = ExistentialHeartbeat()

    hb.start()
    try:
        assert hb.is_running, "start() 后心跳应处于 running 状态"

        time.sleep(3)

        status = hb.get_status()
        assert status["beat_count"] > 0, (
            f"3 秒后心跳应至少跳动一次, beat_count={status['beat_count']}"
        )
    finally:
        hb.stop()

    assert not hb.is_running, "stop() 后心跳应停止"


# ======================================================================
# 场景 13: 强度分配计算验证
# ======================================================================
def test_intensity_calculation():
    from modules.perception.difference.models import Difference
    from modules.perception.difference.intensity import IntensityAssigner

    assigner = IntensityAssigner()

    cases = [
        ("time", "idle_warning", {}),
        ("time", "idle_alert", {"idle_minutes": 20}),
        ("time", "idle_critical", {"idle_minutes": 45}),
        ("perception", "file_created", {}),
        ("perception", "file_deleted", {}),
        ("perception", "dialog_new_message", {}),
        ("perception", "screen_changed", {}),
        ("user_input", "user_message", {}),
    ]

    for source, category, payload in cases:
        diff = Difference(source_type=source, category=category, payload=payload)
        intensity = assigner.assign(diff)
        assert isinstance(intensity, (int, float)), (
            f"{source}/{category}: 强度应为数值, got {type(intensity)}"
        )
        assert 0 <= intensity <= 100, (
            f"{source}/{category}: 强度 {intensity} 越界 [0, 100]"
        )


# ======================================================================
# 场景 14: 强度赋值行为 — 不同事件类型的强度范围
# ======================================================================
class TestIntensityBehavior:
    def setup_method(self):
        from modules.perception.difference.detector import get_detector
        self.detector = get_detector()

    def test_screen_change_intensity_range(self):
        """屏幕变化的强度在 30~50 范围之间（ingest 公式: 30 + urgency * 20）"""
        diff_small = self.detector.ingest(
            "screen", "changed",
            urgency=0.1,
            details={"change_ratio": 0.05},
        )
        assert 30 <= diff_small.intensity <= 34, (
            f"小幅屏幕变化强度={diff_small.intensity}, 期望 30~34"
        )

        diff_large = self.detector.ingest(
            "screen", "changed",
            urgency=1.0,
            details={"change_ratio": 0.50},
        )
        assert diff_large.intensity >= 45, (
            f"大幅屏幕变化强度={diff_large.intensity}, 期望 >= 45"
        )

    def test_file_events_higher_base_intensity(self):
        """文件删除事件基础强度高于屏幕变化"""
        diff = self.detector.ingest("file", "deleted")
        assert diff.intensity >= 50

    def test_ingest_does_not_raise(self):
        """ingest() 在所有合理输入下不抛出异常"""
        for target_type in ("screen", "file", "dialog", "unknown"):
            for change_type in ("changed", "created", "deleted", "modified", "unknown"):
                try:
                    self.detector.ingest(target_type, change_type)
                except Exception as e:
                    pytest.fail(f"ingest({target_type}, {change_type}) 抛出异常: {e}")
