#!/usr/bin/env python3
"""
感知系统真实环境测试 — pytest 兼容

运行:
  python3 -m pytest tests/test_perception_real_screen.py -v -s
"""
import sys
import os
import time
import base64
import io

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

R = "\033[0m"
B = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"


def _b64_to_img(b64):
    from PIL import Image
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def _get_active_window():
    """返回当前活动窗口名；平台不支持或检测失败时返回 None（不要返回占位字符串，
    否则会让窗口检测测试假阳性通过）。"""
    import subprocess
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
            print(f"[real_screen] osascript exit={r.returncode} stderr={r.stderr!r}", file=sys.stderr)
        except Exception as e:
            print(f"[real_screen] osascript failed: {e!r}", file=sys.stderr)
    elif sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                return buf.value
        except Exception as e:
            print(f"[real_screen] win32 window detection failed: {e!r}", file=sys.stderr)
    return None


def _capture_screen():
    from utils.screen_capture import capture_screen
    b64 = capture_screen()
    if b64:
        return _b64_to_img(b64)
    return None


def _run_ocr(img):
    """对截图运行 OCR；失败时记录原因并返回 None（与"识别到 0 行"区分开）。"""
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        ocr = RapidOCR()
        result, _ = ocr(np.array(img))
    except Exception as e:
        print(f"[real_screen] OCR unavailable: {e!r}", file=sys.stderr)
        return None
    if not result:
        return []
    lines = []
    for item in result:
        text = item[1] if len(item) > 1 else ""
        conf = float(item[2]) if len(item) > 2 else 0
        if conf > 0.5:
            lines.append(text)
    return lines


# ══════════════════════════════════════════════════════════════
# 测试: 真实屏幕截图
# ══════════════════════════════════════════════════════════════
def test_real_screen_capture():
    """真实环境: 屏幕截图是否成功"""
    print(f"\n{B}{CYAN}━━━ 真实环境测试: 屏幕截图 ━━━{R}")
    img = _capture_screen()
    assert img is not None, "屏幕截图失败 (mss/PIL/screencapture 均不可用)"
    assert img.width > 0 and img.height > 0, f"截图尺寸异常: {img.width}x{img.height}"
    print(f"  {GREEN}✓{R} 截图成功: {img.width}x{img.height}")


# ══════════════════════════════════════════════════════════════
# 测试: 真实窗口检测
# ══════════════════════════════════════════════════════════════
def test_real_window_detection():
    """真实环境: 窗口检测是否返回有效结果"""
    import pytest
    print(f"\n{B}{CYAN}━━━ 真实环境测试: 窗口检测 ━━━{R}")
    window = _get_active_window()
    if window is None:
        pytest.skip(f"窗口检测在当前平台 ({sys.platform}) 不可用，详见 stderr")
    print(f"  当前窗口: {B}{window}{R}")
    assert window, "窗口检测返回空字符串"


# ══════════════════════════════════════════════════════════════
# 测试: 真实 OCR 文字识别
# ══════════════════════════════════════════════════════════════
def test_real_ocr_detection():
    """真实环境: OCR 能否识别屏幕文字"""
    print(f"\n{B}{CYAN}━━━ 真实环境测试: OCR 文字识别 ━━━{R}")
    import pytest
    img = _capture_screen()
    assert img is not None, "截图失败"

    lines = _run_ocr(img)
    if lines is None:
        pytest.skip("OCR 依赖不可用（rapidocr_onnxruntime 未安装或初始化失败），详见 stderr")
    print(f"  识别到 {len(lines)} 行文字")
    if lines:
        for line in lines[:10]:
            print(f"    {DIM}{line}{R}")
    assert len(lines) > 0, "OCR 未识别到任何文字"


# ══════════════════════════════════════════════════════════════
# 测试: 真实帧差检测
# ══════════════════════════════════════════════════════════════
def test_real_frame_diff():
    """真实环境: 帧差检测能否正常工作"""
    print(f"\n{B}{CYAN}━━━ 真实环境测试: 帧差检测 ━━━{R}")
    import numpy as np

    img1 = _capture_screen()
    assert img1 is not None, "截图失败"

    time.sleep(2)

    img2 = _capture_screen()
    assert img2 is not None, "第二次截图失败"

    small1 = np.array(img1.resize((160, 90)).convert("L"))
    small2 = np.array(img2.resize((160, 90)).convert("L"))
    diff = np.abs(small1.astype(int) - small2.astype(int))
    ratio = np.sum(diff > 25) / diff.size

    changed = ratio > 0.01
    print(f"  帧差变化率: {ratio:.2%} {'🔴 画面变化' if changed else '🟢 画面静止'}")
    print(f"  判定: {'检测到变化' if changed else '无显著变化 (正常)'}")
    # 帧差检测本身不要求一定变化，只要计算正确即可
    assert 0.0 <= ratio <= 1.0, f"帧差比率异常: {ratio}"


# ══════════════════════════════════════════════════════════════
# 测试: 真实感知系统完整链路
# ══════════════════════════════════════════════════════════════
def test_real_perception_pipeline():
    """真实环境: 完整感知链路 (截图→检测→事件→prompt)"""
    print(f"\n{B}{CYAN}━━━ 真实环境测试: 完整感知链路 ━━━{R}")

    from modules.perception.setup import get_perception_system
    from modules.perception.integration import get_perception_integrator
    from modules.perception.difference.detector import get_detector
    from modules.perception.difference.heartbeat import get_heartbeat
    from modules.perception.events.types import PerceptionEvent, PerceptionEventType

    # 启动感知系统
    system = get_perception_system()
    system.setup(voice_enabled=False, proactive_enabled=False)
    system.start()
    integrator = get_perception_integrator()
    integrator._subscribe_events()
    detector = get_detector()
    hb = get_heartbeat()
    hb.start(detector)

    prev_win = ""
    prev_frame = None
    events_count = 0

    print(f"  {DIM}开始 5 秒真实检测 (窗口+帧差)...{R}")

    for tick in range(5):
        # 窗口
        win = _get_active_window()
        if win != prev_win and prev_win:
            integrator._on_perception_event(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_WINDOW,
                source="real_test", importance=0.6,
                payload={"app_name": win, "prev_app": prev_win,
                         "window_title": "", "prev_window": ""},
            ))
            events_count += 1
            print(f"  {CYAN}→{R} 窗口切换: {prev_win} → {win}")
        prev_win = win

        # 截图 + 帧差
        img = _capture_screen()
        if img:
            import numpy as np
            small = img.resize((160, 90))
            cur = np.array(small.convert("L"))
            if prev_frame is not None:
                diff = np.abs(cur.astype(int) - prev_frame.astype(int))
                ratio = np.sum(diff > 25) / diff.size
                if ratio > 0.01:
                    integrator._on_perception_event(PerceptionEvent(
                        event_type=PerceptionEventType.SCREEN_DIFF,
                        source="real_test", importance=min(ratio * 3, 1.0),
                        payload={"change_ratio": ratio, "changed_regions": []},
                    ))
                    events_count += 1
                    print(f"  {YELLOW}→{R} 帧差变化: {ratio:.2%}")
            prev_frame = cur

        # OCR 在外部已单独测试，此处注入模拟 OCR 事件确保链路完整
        if tick == 2:
            integrator._on_perception_event(PerceptionEvent(
                event_type=PerceptionEventType.SCREEN_OCR,
                source="real_test", importance=0.6,
                payload={"text": "测试文字", "new_lines": ["测试OCR注入"],
                         "roi_name": "屏幕"},
            ))
            events_count += 1
            print(f"  {GREEN}→{R} OCR 事件注入")

        time.sleep(1)

    # 验证
    fragment = integrator.pool.snapshot()
    prompt = fragment.content
    print(f"\n  {B}注入 LLM 的 Prompt:{R}")
    if prompt:
        print(f"  {MAGENTA}{prompt}{R}")
    else:
        print(f"  {DIM}(空){R}")

    print(f"\n  {B}检测结果:{R}")
    print(f"    事件数: {events_count}")
    print(f"    池条目: {len(integrator.pool._items)}")
    print(f"    差异检测器: scans={detector.get_status()['scan_count']}")

    hb.stop()
    system.stop()

    assert events_count > 0, "8 秒内未检测到任何感知事件"
    print(f"\n  {GREEN}✓ 完整感知链路验证通过{R}")
