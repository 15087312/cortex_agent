#!/usr/bin/env python3
"""
被动感知系统全链路测试 + 数据展示 + LLM 理解演示

展示：
1. 每一步感知检测的实际输出数据
2. 差异检测器的强度计算
3. 注入模型 prompt 的最终文本
4. LLM 对感知数据的理解（如果可用）
"""
import sys
import os
import time
import tempfile
import shutil
import json
import asyncio

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


def data(label, value):
    """打印数据"""
    print(f"  {DIM}[{label}]{RESET} {value}")


def dump_json(label, obj):
    """打印 JSON 数据"""
    print(f"  {DIM}[{label}]{RESET}")
    for line in json.dumps(obj, ensure_ascii=False, indent=2).split("\n"):
        print(f"    {line}")


# ======================================================================
# Test 1: 文件变化检测 — 打印实际检测数据
# ======================================================================
def test_file_perception():
    header("Test 1: 文件变化检测 (FilePerception)")

    temp_dir = tempfile.mkdtemp(prefix="perception_test_")
    info(f"临时目录: {temp_dir}")

    try:
        from modules.perception.manager import FilePerception

        fp = FilePerception(watch_paths=[temp_dir], enabled=True)

        # 创建文件
        test_file = os.path.join(temp_dir, "config.yaml")
        with open(test_file, "w") as f:
            f.write("database:\n  host: localhost\n  port: 5432")
        time.sleep(1.0)

        changes = fp.check_changes()
        if changes:
            ok(f"检测到 {len(changes)} 个文件变化")
            for c in changes:
                dump_json("ChangeEvent", {
                    "change_type": c.change_type,
                    "target_type": c.target_type,
                    "target": c.target,
                    "details": c.details,
                    "timestamp": c.timestamp,
                })
                data("prompt 文本", c.to_prompt())
        else:
            fail("未检测到文件变化")

        # 修改文件
        with open(test_file, "w") as f:
            f.write("database:\n  host: production.db.com\n  port: 5432\n  password: secret123")
        time.sleep(1.0)

        changes2 = fp.check_changes()
        if changes2:
            ok(f"检测到修改: {len(changes2)} 个变化")
            for c in changes2:
                data("类型", f"{c.change_type} | 目标: {os.path.basename(c.target)}")

        fp.stop()
        ok("FilePerception 启停正常")

    except Exception as e:
        fail(f"FilePerception 测试失败: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ======================================================================
# Test 2: 屏幕捕获 — 打印截图元数据
# ======================================================================
def test_screen_capture():
    header("Test 2: 屏幕捕获 (MSS)")

    try:
        import mss
        with mss.MSS() as sct:
            monitor = sct.monitors[1]
            screenshot = sct.grab(monitor)
            ok(f"MSS 截图成功")
            dump_json("截图元数据", {
                "width": screenshot.width,
                "height": screenshot.height,
                "pixels": screenshot.width * screenshot.height,
                "monitor": monitor,
            })

            from PIL import Image
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

            # 缩放并保存
            w, h = img.size
            ratio = 640 / w
            thumb = img.resize((640, int(h * ratio)))
            thumb_path = "/tmp/perception_test_screenshot.png"
            thumb.save(thumb_path)
            data("缩略图", f"{thumb_path} ({thumb.size[0]}x{thumb.size[1]}, {os.path.getsize(thumb_path)} bytes)")

    except ImportError as e:
        fail(f"mss 未安装: {e}")
    except Exception as e:
        fail(f"屏幕捕获失败: {e}")


# ======================================================================
# Test 3: 帧差检测 — 打印帧差数据
# ======================================================================
def test_frame_diff():
    header("Test 3: 帧差检测 (FrameDiffDetector)")

    try:
        from modules.perception.pipeline.frame_diff import FrameDiffDetector
        import numpy as np

        fdd = FrameDiffDetector()

        # 模拟屏幕变化
        frame1 = np.zeros((200, 300, 3), dtype=np.uint8)
        fdd.detect(frame1)  # 首帧

        # 第二帧：模拟打开一个终端窗口
        frame2 = frame1.copy()
        frame2[20:180, 10:290] = 40  # 深色终端背景
        frame2[25:35, 15:100] = 200  # 标题栏
        r = fdd.detect(frame2)

        ok("帧差检测结果:")
        dump_json("FrameDiffResult", {
            "has_changed": r.has_changed,
            "change_ratio": f"{r.change_ratio:.2%}",
            "changed_regions": [
                {"x": reg[0], "y": reg[1], "w": reg[2], "h": reg[3]}
                if isinstance(reg, (tuple, list)) else
                {"x": reg.x, "y": reg.y, "w": reg.width, "h": reg.height}
                for reg in r.changed_regions
            ],
        })

        # 相同帧 → 无变化
        r2 = fdd.detect(frame2.copy())
        data("相同帧", f"changed={r2.has_changed}, ratio={r2.change_ratio:.2%}")
        if not r2.has_changed:
            ok("相同帧正确识别为无变化")

    except Exception as e:
        fail(f"帧差检测失败: {e}")


# ======================================================================
# Test 4: OCR 文字识别 — 打印识别结果
# ======================================================================
def test_ocr():
    header("Test 4: OCR 文字识别 (RapidOCR)")

    try:
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np

        ocr = RapidOCR()

        # 模拟终端屏幕内容
        img = Image.new("RGB", (640, 400), (30, 30, 30))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18)
        except Exception:
            font = ImageFont.load_default()

        lines = [
            "$ python3 main.py",
            "Starting server on port 8000...",
            "Database connected: postgresql://localhost:5432",
            "WARNING: API key not configured",
            "ERROR: Connection refused to redis://localhost:6379",
            "Server ready at http://localhost:8000",
        ]
        colors = [(0, 255, 0), (200, 200, 200), (200, 200, 200), (255, 255, 0), (255, 0, 0), (0, 255, 0)]
        for i, (line, color) in enumerate(zip(lines, colors)):
            draw.text((15, 20 + i * 28), line, fill=color, font=font)

        img_np = np.array(img)
        result, elapse = ocr(img_np)

        if result:
            ok(f"OCR 识别成功:")
            for item in result:
                text = item[1] if len(item) > 1 else str(item)
                conf = item[2] if len(item) > 2 else "N/A"
                conf_str = f"{float(conf):.2f}" if isinstance(conf, (int, float)) else str(conf)
                data("识别", f"'{text}' (置信度: {conf_str})")
        else:
            fail("OCR 未识别到文字")

    except ImportError:
        fail("rapidocr_onnxruntime 未安装")
    except Exception as e:
        fail(f"OCR 测试失败: {e}")


# ======================================================================
# Test 5: 感知事件 → 差异检测器 — 打印差异数据
# ======================================================================
def test_perception_to_difference():
    header("Test 5: 感知事件 → 差异检测器")

    try:
        from modules.difference_detector import get_detector

        detector = get_detector()

        # 模拟用户操作序列
        actions = [
            ("file", "modified", "src/main.py", {"lines": 15}, 0.6, "用户修改了主文件"),
            ("file", "created", "src/utils.py", {}, 0.3, "用户创建了新工具文件"),
            ("screen", "changed", "screen", {"app": "VSCode"}, 0.5, "用户切换到 VSCode"),
            ("file", "modified", "requirements.txt", {"lines": 2}, 0.4, "用户更新了依赖"),
            ("dialog", "created", "user_message", {"text": "帮我重构认证模块"}, 0.8, "用户发了新消息"),
        ]

        info("模拟用户操作序列:")
        for tt, ct, target, details, urgency, desc in actions:
            diff = detector.ingest(
                target_type=tt, change_type=ct, target=target,
                details=details, urgency=urgency,
            )
            if diff:
                data("操作", f"{desc}")
                dump_json("Difference", {
                    "id": diff.id,
                    "category": diff.category,
                    "intensity": f"{diff.intensity:.1f}",
                    "source_type": diff.source_type,
                    "ttl": f"{diff.ttl}s",
                })

        # 查看活跃差异摘要
        active = detector.get_active_differences(limit=10)
        ok(f"活跃差异: {len(active)} 个")
        for d in active:
            data("差异", f"[{d.category}] intensity={d.intensity:.1f}")

    except Exception as e:
        fail(f"差异检测器测试失败: {e}")


# ======================================================================
# Test 6: 感知上下文注入 — 打印最终 prompt
# ======================================================================
def test_perception_context():
    header("Test 6: 感知上下文 → 模型 prompt")

    try:
        from modules.perception.integration import get_perception_integrator
        from modules.perception import ChangeEvent

        integrator = get_perception_integrator()

        # 模拟真实用户操作
        changes = [
            ChangeEvent(change_type="modified", target_type="file",
                        target="src/auth.py", details={"lines_changed": 30}, timestamp=time.time()),
            ChangeEvent(change_type="modified", target_type="file",
                        target="src/database.py", details={"lines_changed": 12}, timestamp=time.time()),
            ChangeEvent(change_type="changed", target_type="screen",
                        target="screen", details={"app": "Terminal"}, timestamp=time.time()),
        ]

        for c in changes:
            integrator.perception.add_to_attention(c, urgency=0.6)

        # 获取感知上下文
        summary = integrator.get_context_summary()
        if summary:
            ok(f"感知上下文 ({len(summary)} 字符):")
            print(f"\n{CYAN}{summary}{RESET}\n")
        else:
            fail("无感知上下文")

        # 注入到模型 prompt
        base_prompt = (
            "你是一个 AI 编程助手。用户正在开发一个 Python 后端项目。\n"
            "你需要帮助用户编写代码、调试问题、优化性能。"
        )
        full_prompt = integrator.build_system_prompt(base_prompt)

        ok("最终注入模型的完整 prompt:")
        print(f"\n{YELLOW}{'─'*60}")
        print(full_prompt)
        print(f"{'─'*60}{RESET}\n")

    except Exception as e:
        fail(f"感知上下文测试失败: {e}")


# ======================================================================
# Test 7: LLM 对感知数据的理解
# ======================================================================
def test_llm_understanding():
    header("Test 7: LLM 对感知数据的理解")

    try:
        from modules.thinking.experts.pre_gen_experts import _get_lite_model, _is_lite_model_available

        if not _is_lite_model_available():
            info("LiteModel 不可用（无 API Key），跳过 LLM 理解测试")
            info("配置 LARGE_MODEL_API_KEY 后可启用")
            return

        model = _get_lite_model()
        ok("LiteModel 已加载")

        # 模拟感知数据
        perception_data = (
            "【外部状态变化】(最近10秒)\n"
            "- 📝 Modified: src/auth.py (30 lines changed)\n"
            "- 📝 Modified: src/database.py (12 lines changed)\n"
            "- 🖥️ screen: 画面变化 (app: Terminal)\n"
        )

        # 模拟 OCR 数据
        ocr_data = (
            "$ python3 main.py\n"
            "Starting server on port 8000...\n"
            "ERROR: Connection refused to redis://localhost:6379\n"
            "WARNING: API key not configured\n"
        )

        prompt = (
            f"你是一个 AI 编程助手。请根据以下感知数据分析用户当前在做什么，并给出结构化理解。\n\n"
            f"## 感知数据\n{perception_data}\n"
            f"## 屏幕 OCR 文字\n{ocr_data}\n\n"
            f"请用以下格式回答（中文，不超过 150 字）：\n"
            f"1. 用户当前在做什么\n"
            f"2. 遇到了什么问题\n"
            f"3. 建议的操作"
        )

        info("发送给 LLM 的 prompt:")
        print(f"\n{DIM}{prompt}{RESET}\n")

        info("LLM 思考中...")
        result = asyncio.run(model.generate(prompt, max_tokens=300, temperature=0.3))

        ok("LLM 理解结果:")
        print(f"\n{GREEN}{'─'*60}")
        print(result.strip())
        print(f"{'─'*60}{RESET}\n")

    except Exception as e:
        fail(f"LLM 理解测试失败: {e}")


# ======================================================================
# Test 8: 主动感知工具 — understand_screen
# ======================================================================
def test_active_perception():
    header("Test 8: 主动感知工具 (understand_screen)")

    try:
        from infra.tool_manager import ToolRegistry
        tools = ToolRegistry.list_tools()

        if "understand_screen" in tools:
            ok("understand_screen 工具已注册")
        else:
            fail("understand_screen 工具未注册")
            return

        if "transcribe_audio" in tools:
            ok("transcribe_audio 工具已注册")
        else:
            fail("transcribe_audio 工具未注册")

        # 测试 understand_screen 工具
        info("调用 understand_screen(focus='关注错误信息')...")
        from infra.tool_manager.tools.perception_tools import understand_screen
        result = asyncio.run(understand_screen(focus="关注错误信息"))

        if result.get("success"):
            ok("understand_screen 执行成功:")
            data("窗口", result.get("window", "?"))
            ocr_text = result.get("ocr_text", "")
            data("OCR 文字", f"({len(ocr_text)} 字符)")
            if ocr_text:
                for line in ocr_text.split("\n")[:5]:
                    if line.strip():
                        info(f"  {line.strip()[:80]}")
            understanding = result.get("understanding", "")
            if understanding:
                ok("LLM 理解:")
                print(f"\n{GREEN}{'─'*60}")
                print(understanding)
                print(f"{'─'*60}{RESET}\n")
        else:
            info(f"understand_screen 返回: {result.get('error', 'unknown')}")

    except Exception as e:
        fail(f"主动感知测试失败: {e}")


# ======================================================================
# Main
# ======================================================================
if __name__ == "__main__":
    print(f"\n{BOLD}被动感知系统全链路测试 + 数据展示{RESET}")
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    test_file_perception()
    test_screen_capture()
    test_frame_diff()
    test_ocr()
    test_perception_to_difference()
    test_perception_context()
    test_llm_understanding()
    test_active_perception()

    header("测试结果")
    print(f"  {GREEN}通过: {_passed}{RESET}")
    print(f"  {RED}失败: {_failed}{RESET}")
    print()
