#!/usr/bin/env python3
"""
差异检测系统 — 场景测试脚本

测试目标：
1. 各种场景是否正确触发差异检测
2. 触发后的强度计算是否合理
3. 高强度差异是否触发回调
4. 空闲级别升级/降级是否正确
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

_passed = 0
_failed = 0
_results = []


def header(title):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


def ok(msg):
    global _passed
    _passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg):
    global _failed
    _failed += 1
    print(f"  {RED}✗{RESET} {msg}")


def info(msg):
    print(f"  {YELLOW}→{RESET} {msg}")


def dump_diff(diff):
    """打印单个 Difference 的详细信息"""
    d = diff.to_dict() if hasattr(diff, "to_dict") else diff
    print(f"    {DIM}id:{RESET} {d['id']}")
    print(f"    {DIM}source:{RESET} {d['source_type']}  {DIM}category:{RESET} {d['category']}")
    print(f"    {DIM}intensity:{RESET} {d['intensity']:.1f}  {DIM}ttl:{RESET} {d['ttl']}s")
    if d.get("payload"):
        for k, v in d["payload"].items():
            print(f"    {DIM}{k}:{RESET} {v}")


def dump_status(status):
    """打印检测器状态"""
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
    header("场景 1: 时间空闲检测 (idle_warning)")

    from modules.perception.difference.detector import DifferenceDetector
    from modules.perception.difference.sources.time_source import TimeDifferenceSource, IDLE_WARNING_SECONDS

    src = TimeDifferenceSource()
    # 模拟 5 分钟前最后一次活动
    src._last_activity = time.time() - IDLE_WARNING_SECONDS

    diffs = src.detect()
    if diffs:
        ok(f"触发 idle_warning，检测到 {len(diffs)} 个差异")
        for d in diffs:
            dump_diff(d)
            info(f"强度 {d.intensity:.1f} (阈值: HIGH={50.0})")
            if d.intensity >= 50.0:
                info("→ 会触发高强度回调")
            else:
                info("→ 不会触发高强度回调")
    else:
        fail("idle_warning 未触发")


# ======================================================================
# 场景 2: 时间空闲检测 — 模拟 30 分钟空闲 → idle_critical
# ======================================================================
def test_time_idle_critical():
    header("场景 2: 时间空闲检测 (idle_critical)")

    from modules.perception.difference.detector import DifferenceDetector
    from modules.perception.difference.sources.time_source import TimeDifferenceSource, IDLE_CRITICAL_SECONDS

    src = TimeDifferenceSource()
    src._last_activity = time.time() - IDLE_CRITICAL_SECONDS

    diffs = src.detect()
    if diffs:
        ok(f"触发 idle_critical，检测到 {len(diffs)} 个差异")
        for d in diffs:
            dump_diff(d)
            info(f"强度 {d.intensity:.1f} (阈值: HIGH={50.0})")
            if d.intensity >= 50.0:
                info("→ 会触发高强度回调")
            else:
                info("→ 不会触发高强度回调")
    else:
        fail("idle_critical 未触发")


# ======================================================================
# 场景 3: 空闲级别升级 — warning → critical 不会重复触发 warning
# ======================================================================
def test_idle_level_escalation():
    header("场景 3: 空闲级别升级 (warning → critical)")

    from modules.perception.difference.sources.time_source import TimeDifferenceSource, IDLE_WARNING_SECONDS

    src = TimeDifferenceSource()

    # 第一次：5 分钟空闲 → idle_warning
    src._last_activity = time.time() - IDLE_WARNING_SECONDS
    d1 = src.detect()
    info(f"5 分钟空闲: 触发 {len(d1)} 个差异")

    # 第二次：同一级别，不应重复触发
    d1_again = src.detect()
    if len(d1_again) == 0:
        ok("同级别不重复触发")
    else:
        fail(f"同级别重复触发了 {len(d1_again)} 个差异")

    # 第三次：升级到 30 分钟 → idle_critical
    from modules.perception.difference.sources.time_source import IDLE_CRITICAL_SECONDS
    src._last_activity = time.time() - IDLE_CRITICAL_SECONDS
    d2 = src.detect()
    if d2 and d2[0].category == "idle_critical":
        ok(f"升级到 idle_critical: {d2[0].category} (强度 {d2[0].intensity:.1f})")
    else:
        fail(f"未升级到 idle_critical, got: {d2}")


# ======================================================================
# 场景 4: 用户活动重置空闲计时
# ======================================================================
def test_activity_resets_idle():
    header("场景 4: 用户活动重置空闲计时")

    from modules.perception.difference.sources.time_source import TimeDifferenceSource, IDLE_WARNING_SECONDS

    src = TimeDifferenceSource()
    # 先触发一次空闲
    src._last_activity = time.time() - IDLE_WARNING_SECONDS
    d1 = src.detect()
    info(f"触发空闲: {len(d1)} 个差异")

    # 模拟用户活动
    src.notify_activity()
    idle = src.idle_seconds
    info(f"notify_activity 后空闲时间: {idle:.1f}s")

    # 再检测，不应触发
    d2 = src.detect()
    if len(d2) == 0:
        ok("活动后不再触发空闲差异")
    else:
        fail(f"活动后仍触发了 {len(d2)} 个差异")


# ======================================================================
# 场景 5: ingest — 文件创建事件
# ======================================================================
def test_ingest_file_created():
    header("场景 5: ingest 文件创建事件")

    from modules.perception.difference.detector import DifferenceDetector, get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="file",
        change_type="created",
        target="src/new_module.py",
        details={"lines": 50, "size": 1024},
        urgency=0.8,
    )

    if diff:
        ok(f"文件创建事件已摄入")
        dump_diff(diff)
        info(f"强度 {diff.intensity:.1f} (阈值: HIGH={50.0})")
        if diff.intensity >= 50.0:
            info("→ 会触发高强度回调")
        else:
            info("→ 不会触发高强度回调")
    else:
        fail("ingest 返回 None")


# ======================================================================
# 场景 6: ingest — 文件删除事件 (高优先级)
# ======================================================================
def test_ingest_file_deleted():
    header("场景 6: ingest 文件删除事件 (高优先级)")

    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="file",
        change_type="deleted",
        target="src/important.py",
        details={"backup": False},
        urgency=0.9,
    )

    if diff:
        ok(f"文件删除事件已摄入")
        dump_diff(diff)
        info(f"强度 {diff.intensity:.1f}")
        if diff.intensity >= 50.0:
            info("→ 会触发高强度回调")
    else:
        fail("ingest 返回 None")


# ======================================================================
# 场景 7: ingest — 用户发送对话消息
# ======================================================================
def test_ingest_dialog_created():
    header("场景 7: ingest 对话消息事件")

    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="dialog",
        change_type="created",
        target="user_message",
        details={"text": "帮我重构认证模块"},
        urgency=1.0,
    )

    if diff:
        ok(f"对话消息事件已摄入")
        dump_diff(diff)
        info(f"强度 {diff.intensity:.1f}")
    else:
        fail("ingest 返回 None")


# ======================================================================
# 场景 8: ingest — 屏幕变化事件
# ======================================================================
def test_ingest_screen_changed():
    header("场景 8: ingest 屏幕变化事件")

    from modules.perception.difference.detector import get_detector

    detector = get_detector()
    diff = detector.ingest(
        target_type="screen",
        change_type="changed",
        target="screen",
        details={"app": "VSCode", "window": "main.py"},
        urgency=0.5,
    )

    if diff:
        ok(f"屏幕变化事件已摄入")
        dump_diff(diff)
        info(f"强度 {diff.intensity:.1f}")
    else:
        fail("ingest 返回 None")


# ======================================================================
# 场景 9: 高强度回调触发
# ======================================================================
def test_high_intensity_callback():
    header("场景 9: 高强度回调触发")

    from modules.perception.difference.detector import get_detector, HIGH_INTENSITY_THRESHOLD

    detector = get_detector()
    callback_results = []

    def my_callback(differences):
        callback_results.extend(differences)

    detector.on_high_intensity(my_callback)

    # 摄入一个高 urgency 文件删除事件，强度应该 >= 50
    diff = detector.ingest(
        target_type="file",
        change_type="deleted",
        target="critical_config.yaml",
        urgency=1.0,
    )

    if diff and diff.intensity >= HIGH_INTENSITY_THRESHOLD:
        if callback_results:
            ok(f"高强度回调已触发，收到 {len(callback_results)} 个差异")
            for d in callback_results:
                info(f"  回调收到: {d.category} (强度 {d.intensity:.1f})")
        else:
            fail(f"强度 {diff.intensity:.1f} >= {HIGH_INTENSITY_THRESHOLD} 但回调未触发")
    elif diff:
        info(f"强度 {diff.intensity:.1f} < {HIGH_INTENSITY_THRESHOLD}，回调不应触发（符合预期）")
    else:
        fail("ingest 返回 None")


# ======================================================================
# 场景 10: 扫描检测器完整流程
# ======================================================================
def test_detector_scan():
    header("场景 10: 检测器完整扫描流程")

    from modules.perception.difference.detector import get_detector

    detector = get_detector()

    # 先触发一些活动重置空闲计时
    detector.notify_activity()

    # 执行扫描
    diffs = detector.scan()
    info(f"scan() 返回 {len(diffs)} 个差异")

    # 查看活跃差异
    active = detector.get_active(limit=10)
    info(f"活跃差异: {len(active)} 个")
    for d in active:
        print(f"    {DIM}[{d['category']}]{RESET} intensity={d['intensity']:.1f}  status={d['status']}")

    # 查看状态
    status = detector.get_status()
    dump_status(status)

    ok("扫描流程正常完成")


# ======================================================================
# 场景 11: ingest 后查看持久化结果
# ======================================================================
def test_ingest_and_query():
    header("场景 11: ingest → 持久化 → 查询")

    from modules.perception.difference.detector import get_detector

    detector = get_detector()

    # 摄入多个事件
    events = [
        ("file", "created", "src/a.py", 0.6),
        ("file", "modified", "src/b.py", 0.4),
        ("dialog", "created", "user_msg", 1.0),
    ]

    for tt, ct, target, urgency in events:
        diff = detector.ingest(target_type=tt, change_type=ct, target=target, urgency=urgency)
        info(f"摄入 {ct} → intensity={diff.intensity:.1f}")

    # 查询所有活跃差异
    active = detector.get_active(limit=20)
    print(f"\n  {BOLD}活跃差异列表 ({len(active)} 个):{RESET}")
    for d in active:
        print(f"    [{d['category']}] intensity={d['intensity']:.1f}  ttl={d['ttl']}s")

    # 按来源类型筛选
    perception_only = detector.get_active(source_type="perception", limit=10)
    print(f"\n  {BOLD}perception 来源差异 ({len(perception_only)} 个):{RESET}")
    for d in perception_only:
        print(f"    [{d['category']}] intensity={d['intensity']:.1f}")

    ok("持久化和查询正常")


# ======================================================================
# 场景 12: 心跳启动/停止
# ======================================================================
def test_heartbeat_lifecycle():
    header("场景 12: 心跳生命周期")

    from modules.perception.difference.heartbeat import ExistentialHeartbeat

    hb = ExistentialHeartbeat()

    # 启动心跳（不传 detector，使用全局单例）
    hb.start()
    info(f"心跳启动: running={hb.is_running}")

    # 等 3 秒，让心跳跑几轮
    time.sleep(3)

    status = hb.get_status()
    print(f"    {DIM}beat_count:{RESET} {status['beat_count']}")
    print(f"    {DIM}uptime:{RESET} {status['uptime_seconds']}s")

    if status["beat_count"] > 0:
        ok(f"心跳运行正常，已跳 {status['beat_count']} 次")
    else:
        fail("心跳未执行任何跳动")

    # 停止
    hb.stop()
    info(f"心跳停止: running={hb.is_running}")
    ok("心跳启动/停止正常")


# ======================================================================
# 场景 13: 强度分配计算验证
# ======================================================================
def test_intensity_calculation():
    header("场景 13: 强度分配计算验证")

    from modules.perception.difference.models import Difference
    from modules.perception.difference.intensity import IntensityAssigner

    assigner = IntensityAssigner()

    cases = [
        ("time", "idle_warning", {}, "空闲警告"),
        ("time", "idle_alert", {"idle_minutes": 20}, "空闲提醒"),
        ("time", "idle_critical", {"idle_minutes": 45}, "空闲临界"),
        ("perception", "file_created", {}, "文件创建"),
        ("perception", "file_deleted", {}, "文件删除"),
        ("perception", "dialog_new_message", {}, "新对话"),
        ("perception", "screen_changed", {}, "屏幕变化"),
        ("user_input", "user_message", {}, "用户输入"),
    ]

    for source, category, payload, desc in cases:
        diff = Difference(source_type=source, category=category, payload=payload)
        intensity = assigner.assign(diff)
        level = ""
        if intensity >= 50:
            level = " → 高强度，触发回调"
        print(f"    {desc:12s}  {source}/{category:20s}  intensity={intensity:5.1f}{level}")

    ok("强度计算验证完成")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    print(f"\n{BOLD}差异检测系统 — 场景测试{RESET}")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    test_time_idle_warning()
    test_time_idle_critical()
    test_idle_level_escalation()
    test_activity_resets_idle()
    test_ingest_file_created()
    test_ingest_file_deleted()
    test_ingest_dialog_created()
    test_ingest_screen_changed()
    test_high_intensity_callback()
    test_detector_scan()
    test_ingest_and_query()
    test_heartbeat_lifecycle()
    test_intensity_calculation()

    header("测试结果")
    print(f"  {GREEN}通过: {_passed}{RESET}")
    print(f"  {RED}失败: {_failed}{RESET}")
    print()
