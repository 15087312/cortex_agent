#!/usr/bin/env python3
"""
UI 元素检测实时测试脚本（touchpoint 无障碍 API）

用法:
    python scripts/test_ui_detect.py                # 默认 depth=3
    python scripts/test_ui_detect.py --depth 1      # 顶层容器
    python scripts/test_ui_detect.py --depth 2      # 子面板
    python scripts/test_ui_detect.py --depth 0      # 全部元素
    python scripts/test_ui_detect.py --role button  # 只看按钮
    python scripts/test_ui_detect.py --loop         # 循环检测
    python scripts/test_ui_detect.py --app PyCharm  # 指定应用
"""
import sys
import os
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def detect_once(depth: int = 3, role_filter: str = "", app: str = "", named_only: bool = True):
    """执行一次 UI 检测并打印结果"""
    from infra.tool_manager.tools.perception_tools import detect_ui_elements

    t0 = time.time()
    result = detect_ui_elements(depth=depth, role_filter=role_filter, named_only=named_only, app=app)
    elapsed = time.time() - t0

    if not result.get("success"):
        print(f"\n  ❌ 错误: {result.get('error', '未知错误')}")
        return

    elements = result.get("elements", [])

    print(f"\n{'─' * 60}")
    print(f"应用: {result.get('app', '?')} | 深度: {depth} | 耗时: {elapsed:.2f}s | 元素数: {len(elements)}")
    if result.get("role_summary"):
        print(f"角色分布: {result['role_summary']}")
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
            x, y = e.get("center_x", 0), e.get("center_y", 0)
            label = e.get("label", "")[:35]
            actions = e.get("actions", [])
            act_str = f" [{','.join(actions[:2])}]" if actions else ""
            print(f"    {label:35s}  center=({x:4d},{y:4d}){act_str}")

    print()


def main():
    parser = argparse.ArgumentParser(description="UI 元素检测测试 (touchpoint)")
    parser.add_argument("--depth", type=int, default=3, help="检测深度 (1=顶层, 2=面板, 3=控件, 0=全部)")
    parser.add_argument("--role", type=str, default="", help="过滤角色 (button/text/text_field/group/...)")
    parser.add_argument("--app", type=str, default="", help="指定应用名")
    parser.add_argument("--all", action="store_true", help="显示无名字的元素")
    parser.add_argument("--loop", action="store_true", help="循环检测 (每2秒)")
    parser.add_argument("--interval", type=float, default=2.0, help="循环间隔秒数")
    args = parser.parse_args()

    print("=" * 60)
    print("  UI 元素检测实时测试 (touchpoint 无障碍 API)")
    print("  Ctrl+C 退出")
    print("=" * 60)

    if args.loop:
        try:
            while True:
                detect_once(args.depth, args.role, args.app, not args.all)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\n已退出")
    else:
        detect_once(args.depth, args.role, args.app, not args.all)


if __name__ == "__main__":
    main()
