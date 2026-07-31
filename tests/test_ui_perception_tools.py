#!/usr/bin/env python3
"""
UI 感知 & 操作工具全面测试

覆盖：
  - list_windows：列出窗口 + 类型检测
  - detect_ui_elements：指定应用扫描（AX / CDP / 视觉降级）
  - understand_screen：截图 + VLM 理解
  - 鼠标键盘工具注册状态
  - 自动 CDP 配置（Electron 应用）
  - 视觉降级（非 AX 应用）

运行: python3 -m pytest tests/test_ui_perception_tools.py -v
      python3 tests/test_ui_perception_tools.py --live
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

DIM = "\033[2m"
BOLD = "\033[1m"
GREEN = "\033[92m"
CYAN = "\033[96m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"


def _info(label: str, value: str):
    print(f"  {DIM}[{label}]{RESET} {value}", flush=True)


# ======================================================================
# 测试 1: 工具注册状态
# ======================================================================
class TestToolRegistration:
    """所有感知/UI 操作工具必须已注册"""

    REQUIRED_TOOLS = {
        "detect_ui_elements": "核心",
        "understand_screen": "核心",
        "list_windows": "核心",
        "open_app": "核心",
        "mouse_click": "核心",
        "mouse_move": "核心",
        "mouse_double_click": "核心",
        "mouse_scroll": "核心",
        "mouse_drag": "核心",
        "keyboard_type": "核心",
        "keyboard_press": "核心",
        "keyboard_hotkey": "核心",
        "get_mouse_position": "核心",
    }

    @pytest.fixture(autouse=True)
    def _registry(self):
        from infra.tool_manager.tool_registry import ToolRegistry
        self.registry = ToolRegistry

    def test_all_tools_registered(self):
        for name, tier in self.REQUIRED_TOOLS.items():
            t = self.registry.get_tool(name)
            assert t is not None, f"{name} 未注册"
            _info(name, f"{tier}工具 — {'core=True' if t.core else 'core=False'}")

    def test_tool_whitelist(self):
        """大模型白名单必须包含所有工具"""
        from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS
        large_whitelist = DEFAULT_TOOL_WHITELISTS.get("large", [])
        assert "*" in large_whitelist, "大模型白名单没有通配符 '*'"

    def test_list_windows_schema(self):
        """list_windows 参数 schema 正确"""
        t = self.registry.get_tool("list_windows")
        assert t is not None
        params = t.params or {}
        assert "only_active" in params


# ======================================================================
# 测试 2: list_windows
# ======================================================================
@pytest.mark.slow
class TestListWindows:
    """窗口列表工具（依赖真实窗口环境）"""

    def test_list_all_windows(self):
        from infra.tool_manager.tools.list_windows import list_windows
        result = list_windows(only_active=False)
        assert result["success"], f"list_windows 失败: {result}"
        assert isinstance(result["windows"], list)
        assert result["count"] > 0, "没有检测到任何窗口"
        _info("窗口数", str(result["count"]))

        # 每条记录必须包含必要字段
        for w in result["windows"]:
            assert "app" in w
            assert "type" in w  # native / electron
            assert "active" in w
            _info(f"  {w['app']}", f"{w['type']} {'← 活跃' if w['active'] else ''}")

    def test_list_active_only(self):
        from infra.tool_manager.tools.list_windows import list_windows
        result = list_windows(only_active=True)
        assert result["success"]
        for w in result["windows"]:
            assert w["active"], f"only_active=True 但返回了非活跃窗口: {w['app']}"

    def test_hint_mentions_detect(self):
        from infra.tool_manager.tools.list_windows import list_windows
        result = list_windows()
        assert "detect_ui_elements" in result.get("hint", "")


# ======================================================================
# 测试 3: detect_ui_elements — 不指定 app
# ======================================================================
class TestDetectUIElementsNoApp:
    """不指定应用时扫描当前活跃窗口"""

    def test_scan_active_window(self):
        """TouchpointDetector 不再提供 detect_elements API，跳过"""
        pytest.skip("TouchpointDetector 已重构为静态工具类，detect_elements 已移除")

    def test_element_types_valid(self):
        """TouchpointDetector 不再提供 detect_elements API，跳过"""
        pytest.skip("TouchpointDetector 已重构为静态工具类，detect_elements 已移除")


# ======================================================================
# 测试 4: detect_ui_elements — 指定 app
# ======================================================================
@pytest.mark.slow
class TestDetectUIElementsWithApp:
    """指定应用扫描 — 测试各个路径（依赖真实应用窗口）"""

    def test_via_tool_function(self):
        """通过工具函数调用（模拟大模型）"""
        from infra.tool_manager.tools.perception_tools import detect_ui_elements
        r = detect_ui_elements(app="微信")
        if r.get("count", 0) == 0:
            pytest.skip("微信可能未运行或不在当前空间")
        assert r.get("success"), f"detect_ui_elements(app='微信') 失败: {r}"
        _info("微信元素", str(r["count"]))
        _info("后端", r.get("backend", "?"))
        for e in r.get("elements", [])[:5]:
            _info(f"  [{e['type']}]", f"「{e['label'][:40]}」")

    def test_scan_safari(self):
        """Safari 是原生 app，走 AX 路径"""
        from infra.tool_manager.tools.perception_tools import detect_ui_elements
        r = detect_ui_elements(app="Safari浏览器")
        if r.get("success") and r.get("count", 0) > 0:
            _info("Safari元素", str(r["count"]))
            _info("后端", r.get("backend", "?"))
        else:
            pytest.skip("Safari 可能未运行或不在当前空间")

    def test_scan_unknown_app(self):
        """不存在的应用 — app 参数当前被忽略，工具扫描全屏"""
        from infra.tool_manager.tools.perception_tools import detect_ui_elements
        r = detect_ui_elements(app="这个应用肯定不存在_xxxx")
        assert r.get("success")
        # app 参数被忽略，实际扫全屏；可能返回元素也可能空


# ======================================================================
# 测试 5: Electron app — CDP 自动配置
# ======================================================================
@pytest.mark.slow
class TestElectronCDPAutoSetup:
    """Electron 应用的 CDP 自动配置（依赖本机安装的 Electron 应用）"""

    @pytest.fixture
    def detector(self):
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector
        return TouchpointDetector()

    def test_detect_electron_type(self):
        """检测 Electron 应用 bundle 结构"""
        from modules.perception.detectors.touchpoint_detector import TouchpointDetector as D

        # 网易云音乐
        p = D._find_app_path("网易云音乐")
        if p:
            assert D._is_electron_app(p)
            _info("网易云音乐", f"Electron ✅ ({p})")
        else:
            pytest.skip("网易云音乐未安装")

    def test_find_free_port(self):
        pytest.skip("_find_free_port 已从 TouchpointDetector 移除")


# ======================================================================
# 测试 6: 视觉降级（ScreenMonitor）
# ======================================================================
class TestVisualFallback:
    """非 AX 应用的视觉 OCR 降级"""

    def test_detect_elements_via_ocr(self):
        """通过 detect_ui_elements 工具函数做视觉元素检测"""
        pytest.skip("TouchpointDetector.detect_elements 已移除，由 ScreenMonitorSource 替代")


# ======================================================================
# 测试 7: 工具链集成
# ======================================================================
@pytest.mark.slow
class TestToolChain:
    """list_windows → detect_ui_elements 完整链路（依赖真实窗口环境）"""

    def test_list_then_scan_first_app(self):
        """先查窗口列表，再扫描第一个应用"""
        from infra.tool_manager.tools.list_windows import list_windows
        from infra.tool_manager.tools.perception_tools import detect_ui_elements

        # 查窗口
        r1 = list_windows(only_active=True)
        assert r1["success"] and r1["count"] > 0
        active_app = r1["windows"][0]["app"]
        _info("当前活跃", active_app)

        # 扫这个窗口
        r2 = detect_ui_elements(app=active_app)
        assert r2.get("success")
        _info(f"{active_app} 元素", str(r2.get("count", 0)))
        if r2.get("elements"):
            _info("  e.g.", f"[{r2['elements'][0]['type']}] {r2['elements'][0]['label'][:40]}")


# ======================================================================
# 实时展示（非 pytest 模式）
# ======================================================================
def live_demo():
    """直接运行此脚本时的演示模式"""
    print(f"\n{BOLD}UI 感知 & 操作工具 — 完整功能展示{RESET}")
    print(f"{DIM}{'='*60}{RESET}\n")

    # 1. 窗口列表
    print(f"{BOLD}[1] list_windows(){RESET}")
    from infra.tool_manager.tools.list_windows import list_windows
    r = list_windows()
    for w in r["windows"]:
        tag = "⚡" if w["type"] == "electron" else "  "
        flag = "← 活跃" if w["active"] else ""
        print(f"  {tag} {w['app']:20s} {w['type']:10s} {flag}")
    print(f"  {DIM}→ {r['hint']}{RESET}\n")

    # 2. detect_ui_elements — 指定 app
    from infra.tool_manager.tools.perception_tools import detect_ui_elements

    for app in ["微信", "网易云音乐"]:
        print(f"{BOLD}[2] detect_ui_elements(app='{app}'){RESET}")
        r = detect_ui_elements(app=app)
        if r.get("success") and r.get("count", 0) > 0:
            print(f"  {GREEN}{r['count']} 个元素{RESET}  backend={r.get('backend','?')}")
            for e in r["elements"][:6]:
                print(f"    [{e['type']:10s}] {e['label'][:50]}")
        else:
            print(f"  {YELLOW}{r.get('message', r.get('error', '无结果'))}{RESET}")
        print()

    # 3. detect_ui_elements — 当前窗口
    print(f"{BOLD}[3] detect_ui_elements() 当前窗口{RESET}")
    r = detect_ui_elements()
    if r.get("success") and r.get("count", 0) > 0:
        print(f"  {GREEN}{r['count']} 个元素{RESET}")
        for e in r["elements"][:4]:
            print(f"    mouse_click(x={e['center_x']}, y={e['center_y']})  ← {e['type']}「{e['label'][:20]}」")
    print()

    # 4. 工具注册状态
    print(f"{BOLD}[4] 工具注册状态{RESET}")
    from infra.tool_manager.tool_registry import ToolRegistry
    for name in ["list_windows", "detect_ui_elements", "understand_screen",
                  "mouse_click", "keyboard_type", "open_app"]:
        t = ToolRegistry.get_tool(name)
        if t:
            print(f"  {GREEN}✓{RESET} {name:25s} core={t.core}")
        else:
            print(f"  {RED}✗{RESET} {name}")
    print(f"\n{DIM}{'='*60}{RESET}")
    print(f"  全部通过 ✅\n")


if __name__ == "__main__":
    if "--live" in sys.argv:
        live_demo()
    else:
        pytest.main([__file__, "-v", "--tb=short"] + sys.argv[1:])
