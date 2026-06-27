"""
感知系统输出密度测试 — 审查感知数据是否过度臃肿

运行: cd ai_backend && python3 tests/test_perception_density.py [duration_seconds]
默认采集 30 秒，统计事件数量、类型分布、输出长度。
"""
import sys
import time
import threading
from pathlib import Path

# 确保从项目根目录运行也能找到模块
_sys_path_added = str(Path(__file__).resolve().parent.parent)
if _sys_path_added not in sys.path:
    sys.path.insert(0, _sys_path_added)


def run(duration: float = 30.0):
    print(f"[感知密度测试] 采集 {duration}s，请在此期间操作桌面（切换窗口、输入文字等）...\n")

    from modules.perception.integration import get_perception_integrator
    from modules.perception import get_perception_system
    from modules.perception.difference import get_heartbeat

    ps = get_perception_system()
    if not ps._started:
        ps.setup()
        ps.start()
    pi = get_perception_integrator()
    pi.start()

    # 启动差异检测心跳 + 屏幕源（API 启动时才自动挂载）
    heartbeat = get_heartbeat()
    if not heartbeat.is_running:
        from modules.perception.difference import get_detector
        from modules.perception.difference.sources.mcp_screen_source import ScreenDiffSource
        from modules.perception.difference.sources.screen_monitor_source import ScreenMonitorSource
        detector = get_detector()
        screen = ScreenDiffSource()
        screen.start()
        detector.registry.register(screen)
        screen_monitor = ScreenMonitorSource()
        screen_monitor.start()
        heartbeat.start(detector=detector)
        print(f"[感知密度测试] 心跳 + 屏幕差 + 屏幕内容源 已启动")
    else:
        print(f"[感知密度测试] 心跳已在运行")

    # 每 5 秒打印中间状态
    stop_flag = threading.Event()

    def progress():
        for i in range(int(duration // 5)):
            if stop_flag.is_set():
                break
            time.sleep(5)
            count = len(pi.pool._items)
            frag = pi.pool.snapshot()
            print(f"  [{i*5+5:3d}s] 累积 {count:3d} 条, 上下文 {len(frag.content):4d} 字符")

    monitor = threading.Thread(target=progress, daemon=True)
    monitor.start()

    time.sleep(duration)
    stop_flag.set()
    monitor.join(timeout=3)

    items = pi.pool._items
    frag = pi.pool.snapshot()

    # ── 统计报告 ──
    print(f"\n{'='*60}")
    print(f"采集结果 ({len(items)} 条感知事件)")
    print(f"{'='*60}")

    type_counts: dict = {}
    source_counts: dict = {}
    total_chars = 0
    max_chars = 0

    for item in items:
        et = item.get("event_type", "unknown")
        src = item.get("source", "unknown")
        desc = item.get("description", "")
        desc_len = len(desc)
        total_chars += desc_len
        if desc_len > max_chars:
            max_chars = desc_len

        type_counts[et] = type_counts.get(et, 0) + 1
        source_counts[src] = source_counts.get(src, 0) + 1

    avg_len = total_chars / len(items) if items else 0

    print(f"\n事件类型分布:")
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"  {t:30s} {c:3d} 条")

    print(f"\n来源分布:")
    for s, c in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"  {s:30s} {c:3d} 条")

    print(f"\n描述长度:")
    print(f"  总计: {total_chars} 字符 / {len(items)} 条")
    print(f"  平均: {avg_len:.0f} 字符/条")
    print(f"  最大: {max_chars} 字符")

    print(f"\n上下文摘要长度: {len(frag.content)} 字符")
    if frag.content:
        print(f"\n--- 上下文内容预览 (前 500 字符) ---")
        print(frag.content[:500])

    # ── 结论 ──
    print(f"\n{'='*60}")
    if len(items) == 0:
        print("⚠️  感知系统未采集到任何事件 (可能无活跃差异源)")
    elif len(items) <= 5:
        print("✅ 事件量很低，系统正常 (无过度臃肿风险)")
    elif len(items) <= 20:
        print("✅ 事件量适中，get_context_summary 取 5 条足够")
    else:
        print(f"⚠️  事件量较高 ({len(items)} 条)，建议检查是否需要按主题权重过滤")

    if len(frag.content) > 2000:
        print(f"⚠️  上下文输出偏长 ({len(frag.content)} 字符)")
    else:
        print("✅ 上下文输出长度合理")

    # 清理
    try:
        screen_monitor.stop()
    except Exception:
        pass
    try:
        screen.stop()
    except Exception:
        pass
    try:
        heartbeat.stop()
    except Exception:
        pass
    try:
        ps.stop()
    except Exception:
        pass


if __name__ == "__main__":
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
    run(seconds)
