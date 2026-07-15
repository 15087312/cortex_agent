#!/usr/bin/env python3
"""
UI 元素检测实时测试脚本

用法:
    python scripts/test_ui_detect.py              # 单次检测
    python scripts/test_ui_detect.py --loop       # 循环检测 (每2秒)
    python scripts/test_ui_detect.py --focus 按钮  # 只看特定类型
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def detect_once(focus: str = ""):
    """执行一次 UI 检测并打印结果"""
    from modules.perception.difference.sources.screen_monitor_source import get_screen_monitor_source

    src = get_screen_monitor_source()
    t0 = time.time()
    result = src.analyze_ui_elements()
    elapsed = time.time() - t0

    elements = result.get("elements", [])

    # 过滤
    if focus:
        elements = [e for e in elements if focus.lower() in e.get("text", "").lower()
                     or focus.lower() in e.get("type", "").lower()]

    # 打印
    print(f"\n{'─' * 60}")
    print(f"检测耗时: {elapsed:.2f}s | 元素数: {len(elements)}")
    print(f"{'─' * 60}")

    if not elements:
        print("  (无匹配元素)")
        return

    # 按类型分组
    by_type = {}
    for e in elements:
        t = e.get("type", "unknown")
        by_type.setdefault(t, []).append(e)

    for etype, items in by_type.items():
        print(f"\n  [{etype}] ({len(items)} 个)")
        for e in items[:15]:
            x, y = e.get("x", 0), e.get("y", 0)
            w, h = e.get("w", 0), e.get("h", 0)
            text = e.get("text", "")[:30]
            cx, cy = x + w // 2, y + h // 2
            print(f"    {text:30s}  @ ({x:4d},{y:4d}) {w:3d}x{h:3d}  center=({cx},{cy})")

    print()


def main():
    parser = argparse.ArgumentParser(description="UI 元素检测测试")
    parser.add_argument("--loop", action="store_true", help="循环检测 (每2秒)")
    parser.add_argument("--interval", type=float, default=2.0, help="循环间隔秒数")
    parser.add_argument("--focus", type=str, default="", help="过滤关键词")
    args = parser.parse_args()

    print("=" * 60)
    print("  UI 元素检测实时测试")
    print("  Ctrl+C 退出")
    print("=" * 60)

    if args.loop:
        try:
            while True:
                detect_once(args.focus)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已退出")
    else:
        detect_once(args.focus)


if __name__ == "__main__":
    main()
