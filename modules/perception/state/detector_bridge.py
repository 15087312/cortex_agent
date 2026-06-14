"""
差异检测器 → 事件总线桥接

DifferenceDetector 的高强度差异回调 → PerceptionEvent(DIFFERENCE_DETECTED) → EventBus

这样 PerceptionIntegrator（已订阅 DIFFERENCE_DETECTED）自动将高强度差异注入模型上下文。
PerceptionThinkTrigger（也已订阅）可触发主动思考（如果注入了 trigger_port）。

设计原则：
- 桥接器本身在感知层（perception），不污染差异检测层（difference_detector）
- DifferenceDetector 不知道事件总线的存在，通过 callback 模式解耦
"""
import time
from typing import List

from utils.logger import setup_logger
from modules.difference_detector.models import Difference
from modules.perception.events.types import PerceptionEvent, PerceptionEventType

logger = setup_logger("detector_event_bridge")


class DetectorEventBridge:
    """差异检测器 → 事件总线桥接

    注册到 DifferenceDetector.on_high_intensity()，
    将高强度 Difference 对象转换为 PerceptionEvent 发布到 EventBus。
    """

    def __init__(self, event_bus=None):
        self._event_bus = event_bus
        self._published_count = 0
        logger.info("DetectorEventBridge 初始化完成")

    @property
    def published_count(self) -> int:
        return self._published_count

    def handle(self, differences: List[Difference]) -> None:
        """高强度差异回调 — 发布到事件总线

        Args:
            differences: intensity >= 50 的差异列表
        """
        if not differences:
            return

        bus = self._event_bus
        if bus is None:
            try:
                from modules.perception.events.bus import get_event_bus
                bus = get_event_bus()
            except Exception as e:
                logger.debug(f"获取事件总线失败 (非致命): {e}")
                return

        for diff in differences:
            try:
                event = PerceptionEvent(
                    event_type=PerceptionEventType.DIFFERENCE_DETECTED,
                    source=diff.source_type,
                    importance=min(diff.intensity / 100.0, 1.0),
                    payload={
                        "source_type": diff.source_type,
                        "category": diff.category,
                        "intensity": diff.intensity,
                        "description": diff.description
                        if hasattr(diff, "description") and diff.description
                        else self._build_description(diff),
                        "difference_id": diff.id
                        if hasattr(diff, "id")
                        else "",
                        "ttl": diff.ttl if hasattr(diff, "ttl") else 300,
                        "timestamp": time.time(),
                    },
                )
                bus.publish(event)
                self._published_count += 1
            except Exception as e:
                logger.debug(f"发布差异事件失败 (非致命): {e}")

        if differences and self._published_count % 10 < len(differences):
            logger.info(
                f"[桥接] 已发布 {self._published_count} 个 DIFFERENCE_DETECTED 事件"
            )

    @staticmethod
    def _build_description(diff: Difference) -> str:
        """根据 Difference 字段构建可读描述"""
        payload = diff.payload or {}
        target = payload.get("target", "")
        target_type = payload.get("target_type", diff.source_type)
        change_type = payload.get("change_type", diff.category)
        if target:
            return f"{target_type}/{change_type}: {target}"
        return f"[{diff.source_type}] {diff.category} (intensity={diff.intensity:.0f})"
