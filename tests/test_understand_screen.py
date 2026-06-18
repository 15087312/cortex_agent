#!/usr/bin/env python3
"""
understand_screen 工具专项测试

屏幕理解流程：截图 → VLM 视觉模型描述 → 返回结构化理解

运行:
  python3 -m pytest tests/test_understand_screen.py -v
"""
from __future__ import annotations

import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
RESET = "\033[0m"


def header(title: str):
    print(f"\n{BOLD}{CYAN}{'='*60}{RESET}")
    print(f"  {title}")
    print(f"{BOLD}{CYAN}{'='*60}{RESET}\n")


def ok(msg: str):
    print(f"  {GREEN}✓{RESET} {msg}")


def info(label: str, value: str):
    print(f"  {DIM}[{label}]{RESET} {value}")


def test_registration():
    header("工具注册")

    from infra.tool_manager.tool_registry import ToolRegistry

    t = ToolRegistry.get_tool("understand_screen")
    assert t is not None, "understand_screen 未注册"
    ok(f"understand_screen core={t.core}")

    params = t.params or {}
    assert "focus" in params, "缺少 focus 参数"
    info("参数", "focus（可选，关注重点描述）")


def test_screen_capture():
    header("截图能力")

    from infra.tool_manager.tools.perception_tools import _capture_screen
    t0 = time.time()
    b64 = _capture_screen()
    elapsed = time.time() - t0

    assert b64, "截图返回空"
    assert isinstance(b64, str)
    img_bytes = base64.b64decode(b64)
    info("格式", f"base64 PNG, {len(img_bytes)} bytes")
    info("耗时", f"{elapsed:.2f}s")
    ok(f"截图成功 ({elapsed:.1f}s, {len(img_bytes)//1024}KB)")


def test_active_window():
    header("活跃窗口检测")

    from infra.tool_manager.tools.perception_tools import _get_active_window
    window = _get_active_window()
    assert window, "_get_active_window 返回空"
    info("当前窗口", window)
    ok(f"活跃窗口: {window}")
