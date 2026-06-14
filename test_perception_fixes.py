#!/usr/bin/env python3
"""
屏幕理解和 UI 检测修复验证测试

用法：
  python test_perception_fixes.py              # 全部测试
  python test_perception_fixes.py --quick       # 仅快速检查（不加载模型）
  python test_perception_fixes.py --full        # 完整测试（含截图+OCR）
"""
import asyncio
import sys
import time

PASS = "✓"
FAIL = "✗"


def log_test(num: int, name: str, ok: bool, detail: str = ""):
    icon = PASS if ok else FAIL
    print(f"  {icon} [{num}] {name}")
    if detail and not ok:
        print(f"       {detail}")


# ──────────────────────────────────────────────
# 1. PerceptionSystem.setup() 不崩溃
# ──────────────────────────────────────────────
def test_setup_no_crash() -> bool:
    try:
        from modules.perception.setup import get_perception_system
        ps = get_perception_system()
        ps.setup()
        assert ps.pipeline is not None, "pipeline 未初始化"
        assert ps.event_bus is not None, "event_bus 未初始化"
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 2. ImageAnalyzer 单例警告 + ensure_model_type
# ──────────────────────────────────────────────
def test_image_analyzer_singleton() -> bool:
    try:
        from infra.data_process.core.image_analyzer import ImageAnalyzer

        # 两次构造应返回同一个实例
        a1 = ImageAnalyzer(model_type="auto")
        a2 = ImageAnalyzer(model_type="openai")
        assert a1 is a2, "单例模式失效：返回了不同实例"
        assert hasattr(a1, "ensure_model_type"), "ensure_model_type 方法不存在"
        assert hasattr(a1, "_init_model_type"), "_init_model_type 属性不存在"
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 3. 感知工具可以导入且无语法错误
# ──────────────────────────────────────────────
def test_tool_import() -> bool:
    try:
        from infra.tool_manager.tools.perception_tools import (
            understand_screen,
            detect_ui_elements,
            transcribe_audio,
        )
        assert callable(understand_screen)
        assert callable(detect_ui_elements)
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 4. OmniParserDetector 三级降级逻辑
# ──────────────────────────────────────────────
def test_omniparser_backend_detection() -> bool:
    try:
        from modules.perception.detectors.omniparser_detector import (
            OmniParserDetector,
            UIElement,
        )

        d = OmniParserDetector()
        assert hasattr(d, "_try_auto_start"), "自动启动方法缺失"
        assert hasattr(d, "_process"), "子进程管理缺失"
        assert hasattr(d, "backend"), "backend property 缺失"

        # 验证 backend 是合法的降级值
        assert d.backend in (
            "omniparser_http", "omniparser_local", "ocr_fallback", None
        ), f"未知 backend: {d.backend}"
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 5. UIElement dataclass 可用
# ──────────────────────────────────────────────
def test_ui_element() -> bool:
    try:
        from modules.perception.detectors.omniparser_detector import UIElement
        e = UIElement(
            element_id="e001",
            type="button",
            label="确定",
            bbox=[100, 200, 180, 240],
            center_x=140,
            center_y=220,
            confidence=0.95,
            source="ocr_fallback",
        )
        d = e.to_dict()
        assert d["element_id"] == "e001"
        assert d["type"] == "button"
        assert d["label"] == "确定"
        assert d["center_x"] == 140
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 6. asyncio.to_thread 语法存在
# ──────────────────────────────────────────────
def test_to_thread_in_source() -> bool:
    try:
        with open("infra/tool_manager/tools/perception_tools.py") as f:
            src = f.read()
            count = src.count("asyncio.to_thread")
            assert count >= 2, (
                f"asyncio.to_thread 出现 {count} 次（期待 ≥2）"
            )
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 7. (可选) 实际截图检测 — 需要显示器
# ──────────────────────────────────────────────
def test_screen_capture() -> bool:
    try:
        from utils.screen_capture import capture_screen
        b64 = capture_screen()
        assert b64, "capture_screen 返回空"
        assert len(b64) > 100, f"截图数据太短 ({len(b64)} bytes)"
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 8. (可选) 实际 UI 检测 — 需要显示器
# ──────────────────────────────────────────────
async def test_detect_ui_elements_async() -> bool:
    try:
        from infra.tool_manager.tools.perception_tools import detect_ui_elements
        result = await detect_ui_elements()
        assert isinstance(result, dict), "返回类型错误"
        assert "success" in result, "缺少 success 字段"
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# 9. setup.py 无 _setup_file_monitoring 等死引用
# ──────────────────────────────────────────────
def test_no_dead_refs() -> bool:
    try:
        with open("modules/perception/setup.py") as f:
            src = f.read()
            bad = [
                "_setup_file_monitoring",
                "_setup_mcp_detector",
                "_file_monitor_loop",
                "self.file_perception",
            ]
            for b in bad:
                assert b not in src, f"死引用仍存在: {b}"
        return True
    except Exception as e:
        print(f"       Exception: {e}")
        return False


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="感知系统修复验证测试")
    parser.add_argument("--quick", action="store_true", help="仅快速检查代码结构")
    parser.add_argument("--full", action="store_true", help="完整测试（含截图）")
    args = parser.parse_args()

    print("=" * 56)
    print("  感知系统修复验证测试")
    print("=" * 56)

    # ── 阶段 1: 代码结构测试（不加载模型）──
    print("\n[阶段 1] 代码结构完整性")
    t1 = test_setup_no_crash()
    log_test(1, "PerceptionSystem.setup() 不崩溃", t1)

    t2 = test_image_analyzer_singleton()
    log_test(2, "ImageAnalyzer 单例 + ensure_model_type", t2)

    t3 = test_tool_import()
    log_test(3, "感知工具函数可导入", t3)

    t4 = test_omniparser_backend_detection()
    log_test(4, "OmniParserDetector 二级降级逻辑", t4)

    t5 = test_ui_element()
    log_test(5, "UIElement dataclass 可用", t5)

    t6 = test_to_thread_in_source()
    log_test(6, "源码中存在 asyncio.to_thread 调用", t6)

    t7 = test_no_dead_refs()
    log_test(7, "setup.py 无死方法引用", t7)

    phase1_ok = all([t1, t2, t3, t4, t5, t6, t7])
    print(f"\n  阶段 1 结果: {'全部通过' if phase1_ok else '存在失败'}")

    if args.quick:
        print("\n  (--quick 模式，跳过阶段 2/3)")
        sys.exit(0 if phase1_ok else 1)

    # ── 阶段 2: 截图测试（需要显示器）──
    print("\n[阶段 2] 屏幕捕获")
    t8 = test_screen_capture()
    log_test(8, "capture_screen 截图成功", t8)

    # ── 阶段 3: UI 元素检测（需要显示器）──
    if args.full or t8:
        print("\n[阶段 3] UI 元素检测（异步）")
        t9 = asyncio.run(test_detect_ui_elements_async())
        log_test(9, "detect_ui_elements 调用成功", t9)
    else:
        print("\n[阶段 3] 跳过（截图失败）")
        t9 = None

    all_ok = phase1_ok and t8 and (t9 if t9 is not None else True)
    print(f"\n{'=' * 56}")
    print(f"  总体结果: {'全部通过 ✓' if all_ok else '存在失败 ✗'}")
    print(f"{'=' * 56}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
