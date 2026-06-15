"""感知流水线端到端测试

用法:
    python tests/test_perception_pipeline.py [--duration 10]

启动截图流水线，运行 N 秒，输出：捕获帧数、事件数、_attention_items 内容。
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
# 把各模块日志调到 INFO，屏蔽 DEBUG 噪音
logging.getLogger("perception_pipeline").setLevel(logging.INFO)
logging.getLogger("perception_integration").setLevel(logging.INFO)
logging.getLogger("perception_capture").setLevel(logging.INFO)
logging.getLogger("perception_window_detector").setLevel(logging.WARNING)
logging.getLogger("perception_ocr_detector").setLevel(logging.WARNING)
logging.getLogger("perception_event_bus").setLevel(logging.WARNING)

from modules.perception.integration import get_perception_integrator
from modules.perception.events.bus import get_event_bus
from modules.perception.events.types import PerceptionEvent, PerceptionEventType


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=8, help="运行秒数")
    parser.add_argument("--ocr", action="store_true", help="启用 OCR 检测器（较慢）")
    parser.add_argument("--omniparser", action="store_true", help="启用 OmniParser 检测器（较慢）")
    args = parser.parse_args()

    event_bus = get_event_bus()
    bus_stats = {"published": []}

    def on_event(event: PerceptionEvent):
        bus_stats["published"].append({
            "type": str(event.event_type.value) if hasattr(event.event_type, 'value') else str(event.event_type),
            "source": event.source,
            "importance": event.importance,
            "roi": getattr(event, 'roi_name', ''),
            "payload_keys": list(event.payload.keys()) if hasattr(event, 'payload') and event.payload else [],
        })

    event_bus.subscribe(PerceptionEventType.SCREEN_DIFF, on_event)
    event_bus.subscribe(PerceptionEventType.SCREEN_WINDOW, on_event)
    event_bus.subscribe(PerceptionEventType.SCREEN_OCR, on_event)
    event_bus.subscribe(PerceptionEventType.DIFFERENCE_DETECTED, on_event)

    from modules.perception.pipeline.capture import create_capture_backend
    from modules.perception.pipeline.frame_diff import FrameDiffDetector
    from modules.perception.pipeline.pipeline import PerceptionPipeline

    capture = create_capture_backend()
    print(f"\n捕获后端: {capture.platform_name} (available={capture.is_available()})")

    if not capture.is_available():
        print("错误: 捕获后端不可用")
        return 1

    detectors = {}

    from modules.perception.detectors.window_detector import WindowDetector
    wd = WindowDetector()
    if wd.is_available():
        detectors["window"] = wd
        print(f"窗口检测器: 已启用")

    if args.ocr:
        from modules.perception.detectors.ocr_detector import OCRDetector
        od = OCRDetector()
        if od.is_available():
            detectors["ocr"] = od
            print(f"OCR 检测器: 已启用")

    if args.omniparser:
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        ud = OmniParserDetector()
        if ud.is_available():
            detectors["ui"] = ud
            print(f"OmniParser 检测器: 已启用")

    pipeline = PerceptionPipeline(
        capture=capture,
        frame_diff=FrameDiffDetector(),
        detectors=detectors,
        event_bus=event_bus,
        fps=5,
    )

    integrator = get_perception_integrator()

    # 手动将 integrator 订阅到同一事件总线，不触发额外 PerceptionSystem 初始化
    from modules.perception.events.types import PerceptionEventType as PET
    for et in [PET.SCREEN_DIFF, PET.SCREEN_WINDOW, PET.SCREEN_OCR, PET.FILE_CHANGE, PET.DIFFERENCE_DETECTED]:
        event_bus.subscribe(et, integrator._on_perception_event)


    print(f"\n流水线运行 {args.duration} 秒，请操作屏幕（切换窗口、打字等）...\n")

    pipeline.start()
    time.sleep(args.duration)
    pipeline.stop()

    stats = pipeline.get_stats()
    print(f"\n=== 流水线统计 ===")
    print(f"  捕获帧数:      {stats['frames_captured']}")
    print(f"  变化帧数:      {stats['frames_with_change']}")
    print(f"  发布事件数:    {stats['events_published']}")
    print(f"  实际 fps:      {stats['actual_fps']}")
    print(f"  检测器调用:    {stats['detectors']}")

    print(f"\n=== 总线事件明细 ({len(bus_stats['published'])} 条) ===")
    for ev in bus_stats["published"][-20:]:
        roi = ev['roi'] or '(none)'
        print(f"  [{ev['type']:25s}] src={ev['source']:8s} roi={roi:20s} keys={ev['payload_keys']}")
    if len(bus_stats["published"]) > 20:
        print(f"  ... 还有 {len(bus_stats['published']) - 20} 条")

    summary = integrator.get_context_summary()
    print(f"\n=== PerceptionIntegrator 注意力池 ===")
    print(f"  _attention_items 总数: {len(integrator._attention_items)}")
    if summary:
        print(f"\n  get_context_summary() 输出 ({len(summary)} 字符):\n{summary}")
    else:
        print(f"  (空 — 无事件注入)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
