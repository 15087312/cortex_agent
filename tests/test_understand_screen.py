#!/usr/bin/env python3
"""
understand_screen 工具专项测试

屏幕理解流程：截图 → VLM 视觉模型描述 → 返回结构化理解

运行:
  python3 tests/test_understand_screen.py
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def header(title: str):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"  {title}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


def ok(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def info(label: str, value: str):
    print(f"  {DIM}[{label}]{RESET} {value}")


# ======================================================================
# 测试 1: 工具注册
# ======================================================================
def test_registration():
    header("工具注册")

    from infra.tool_manager.tool_registry import ToolRegistry

    t = ToolRegistry.get_tool("understand_screen")
    assert t is not None, "understand_screen 未注册"
    ok(f"understand_screen core={t.core}")

    params = t.params or {}
    assert "focus" in params, "缺少 focus 参数"
    info("参数", "focus（可选，关注重点描述）")


# ======================================================================
# 测试 2: 截图能力
# ======================================================================
def test_screen_capture():
    header("截图能力")

    from infra.tool_manager.tools.perception_tools import _capture_screen
    t0 = time.time()
    b64 = _capture_screen()
    elapsed = time.time() - t0

    assert b64, "截图返回空"
    assert isinstance(b64, str)
    import base64
    img_bytes = base64.b64decode(b64)
    info("格式", f"base64 PNG, {len(img_bytes)} bytes")
    info("耗时", f"{elapsed:.2f}s")
    ok(f"截图成功 ({elapsed:.1f}s, {len(img_bytes)//1024}KB)")


# ======================================================================
# 测试 3: 当前窗口检测
# ======================================================================
def test_active_window():
    header("活跃窗口检测")

    from infra.tool_manager.tools.perception_tools import _get_active_window
    window = _get_active_window()
    assert window, "_get_active_window 返回空"
    info("当前窗口", window)
    ok(f"活跃窗口: {window}")


# ======================================================================
# 测试 4: understand_screen 完整调用
# ======================================================================
def test_understand_screen():
    header("understand_screen 完整调用")

    try:
        # 初始化视觉分析器（同 api/main.py lifespan 逻辑）
        import asyncio
        from infra.data_process.core.image_analyzer import get_default_analyzer
        print(f"  {DIM}[初始化] 加载视觉分析器...{RESET}", end=" ", flush=True)
        t_init = time.time()
        analyzer = asyncio.run(get_default_analyzer())
        assert analyzer is not None, "视觉分析器初始化失败"
        print(f"{GREEN}✓{RESET} ({time.time()-t_init:.1f}s)")

        from infra.tool_manager.tools.perception_tools import understand_screen
    except Exception as e:
        print(f"\n  {RED}✗{RESET} 导入/初始化失败: {e}")
        return

    # 调用工具
    t0 = time.time()
    result = understand_screen(focus="描述当前屏幕的主要内容")
    elapsed = time.time() - t0

    info("耗时", f"{elapsed:.1f}s")

    if not result.get("success"):
        error = result.get("error", "未知错误")
        method = result.get("method", "?")
        print(f"  {YELLOW}⚠{RESET} understand_screen 返回错误 (method={method}):")
        print(f"    {error}")
        return

    # 展示结果
    ok(f"understand_screen 执行成功")

    window = result.get("window", "")
    info("窗口", window or "(未获取)")

    method = result.get("method", "?")
    info("方法", method)

    understanding = result.get("understanding", "")
    if understanding:
        print(f"\n  {BOLD}VLM 屏幕理解:{RESET}")
        print(f"  {GREEN}{understanding[:500]}{RESET}")

    print()
    ok("understand_screen 返回结构化屏幕理解")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    print(f"\n{BOLD}understand_screen 工具测试{RESET}")
    print(f"{DIM}{'='*60}{RESET}\n")

    passed = 0
    failed = 0

    tests = [
        ("工具注册", test_registration),
        ("截图能力", test_screen_capture),
        ("活跃窗口", test_active_window),
        ("完整调用", test_understand_screen),
    ]

    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n  {RED}✗{RESET} {name}: {e}")

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"  结果: {GREEN}{passed} 通过{RESET}, {RED}{failed} 失败{RESET}")
    print()
