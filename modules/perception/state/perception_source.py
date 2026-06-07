"""感知差异源 — 从 Event Bus 消费感知事件，转化为 Difference

这是感知层和检测层的桥接。
感知事件通过 Event Bus 到达，此源将它们转化为 DifferenceDetector 能理解的 Difference。
"""
import queue
from typing import List

from modules.difference_detector.models import Difference
from modules.difference_detector.sources.base import DifferenceSource
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_difference_source")

# 感知事件类型 → 差异类别映射
_EVENT_CATEGORY_MAP = {
    PerceptionEventType.SCREEN_OCR: ("screen_ocr", 25.0),
    PerceptionEventType.SCREEN_UI: ("screen_ui", 30.0),
    PerceptionEventType.SCREEN_WINDOW: ("screen_window", 20.0),
    PerceptionEventType.SCREEN_DIFF: ("screen_diff", 15.0),
    PerceptionEventType.FILE_CHANGE: ("file_change", 20.0),
    PerceptionEventType.DIALOG_CHANGE: ("dialog_change", 25.0),
    PerceptionEventType.SPEECH_DETECTED: ("speech", 35.0),
}


class PerceptionDifferenceSource(DifferenceSource):
    """感知差异源

    从 Event Bus 订阅感知事件，将它们转化为 Difference 对象。
    DifferenceDetector 的 scan() 会调用 detect() 来获取这些差异。
    """

    def __init__(self, event_bus=None):
        super().__init__()
        self._event_queue: queue.Queue = queue.Queue(maxsize=1000)
        self._sub_id: str = ""
        self._event_bus = event_bus

    @property
    def source_type(self) -> str:
        return "perception"

    def start(self, event_bus=None) -> None:
        """订阅事件总线"""
        # 防止重复订阅
        if self._sub_id:
            self.stop(event_bus)

        bus = event_bus or self._event_bus
        if bus is None:
            from modules.perception.events.bus import get_event_bus
            bus = get_event_bus()

        self._sub_id = bus.subscribe(
            PerceptionEventType.ALL,
            handler=self._on_event,
        )
        logger.info("PerceptionDifferenceSource 已订阅事件总线")

    def stop(self, event_bus=None) -> None:
        bus = event_bus or self._event_bus
        if bus and self._sub_id:
            bus.unsubscribe(self._sub_id)
            self._sub_id = ""

    def _on_event(self, event: PerceptionEvent):
        """事件回调 — 放入队列（队满时丢弃最旧事件）"""
        if event.event_type in _EVENT_CATEGORY_MAP:
            try:
                self._event_queue.put_nowait(event)
            except queue.Full:
                try:
                    self._event_queue.get_nowait()  # 丢弃最旧
                    self._event_queue.put_nowait(event)
                except (queue.Full, queue.Empty):
                    pass

    def detect(self) -> List[Difference]:
        """从队列中取出感知事件，转化为 Difference"""
        differences = []
        while not self._event_queue.empty():
            try:
                event = self._event_queue.get_nowait()
            except queue.Empty:
                break

            cat_info = _EVENT_CATEGORY_MAP.get(event.event_type)
            if not cat_info:
                continue

            category, base_intensity = cat_info
            diff = Difference(
                source_type="perception",
                category=category,
                intensity=base_intensity,
                payload={
                    "event_type": event.event_type,
                    "source": event.source,
                    "roi_name": event.roi_name,
                    "description": event.short_repr(),
                    **event.payload,
                },
                ttl=300,  # 5 分钟 TTL
            )
            differences.append(diff)

        return differences
