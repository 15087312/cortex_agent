"""世界状态管理器 — 从事件总线消费事件，维护当前世界状态"""
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("perception_world_state")


@dataclass
class WorldState:
    """当前世界状态快照"""
    active_window: str = ""                  # 当前活跃窗口标题
    active_app: str = ""                     # 当前活跃应用名
    screen_text: str = ""                    # 当前屏幕文本（OCR 累积）
    recent_ocr: List[str] = field(default_factory=list)  # 最近 OCR 文本（保留 10 条）
    ui_elements: List[Dict[str, Any]] = field(default_factory=list)  # 检测到的 UI 元素
    recent_events: List[Dict[str, Any]] = field(default_factory=list)  # 最近事件（保留 20 条）
    last_update: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_window": self.active_window,
            "active_app": self.active_app,
            "screen_text": self.screen_text[:500],
            "recent_ocr_count": len(self.recent_ocr),
            "ui_elements_count": len(self.ui_elements),
            "recent_events_count": len(self.recent_events),
            "last_update": self.last_update,
        }

    def get_summary(self) -> str:
        """获取状态摘要（供 LLM 使用）"""
        parts = []
        if self.active_app:
            parts.append(f"当前应用: {self.active_app}")
        if self.active_window:
            parts.append(f"窗口: {self.active_window[:50]}")
        if self.screen_text:
            parts.append(f"屏幕文本: {self.screen_text[:200]}")
        if self.ui_elements:
            parts.append(f"UI 元素: {len(self.ui_elements)} 个")
        return " | ".join(parts) if parts else "无感知信息"


class WorldStateManager:
    """世界状态管理器

    从 Event Bus 订阅事件，维护当前世界状态。
    线程安全，支持并发读写。
    """

    _MAX_RECENT_OCR = 10
    _MAX_RECENT_EVENTS = 20
    _MAX_UI_ELEMENTS = 50

    def __init__(self):
        self._state = WorldState()
        self._lock = threading.Lock()
        self._sub_ids: List[str] = []

    def start(self, event_bus) -> None:
        """订阅事件总线"""
        self._sub_ids.append(
            event_bus.subscribe(PerceptionEventType.SCREEN_WINDOW, self._on_window)
        )
        self._sub_ids.append(
            event_bus.subscribe(PerceptionEventType.SCREEN_OCR, self._on_ocr)
        )
        self._sub_ids.append(
            event_bus.subscribe(PerceptionEventType.SCREEN_UI, self._on_ui)
        )
        logger.info("WorldStateManager 已订阅事件总线")

    def stop(self, event_bus) -> None:
        """取消订阅"""
        for sub_id in self._sub_ids:
            event_bus.unsubscribe(sub_id)
        self._sub_ids.clear()

    def get_state(self) -> WorldState:
        """获取当前状态快照"""
        with self._lock:
            return WorldState(
                active_window=self._state.active_window,
                active_app=self._state.active_app,
                screen_text=self._state.screen_text,
                recent_ocr=list(self._state.recent_ocr),
                ui_elements=list(self._state.ui_elements),
                recent_events=list(self._state.recent_events),
                last_update=self._state.last_update,
            )

    def _on_window(self, event: PerceptionEvent):
        with self._lock:
            self._state.active_window = event.payload.get("window_title", "")
            self._state.active_app = event.payload.get("app_name", "")
            self._state.last_update = event.timestamp
            self._add_event(event)

    def _on_ocr(self, event: PerceptionEvent):
        with self._lock:
            new_lines = event.payload.get("new_lines", [])
            if new_lines:
                self._state.recent_ocr.extend(new_lines)
                if len(self._state.recent_ocr) > self._MAX_RECENT_OCR:
                    self._state.recent_ocr = self._state.recent_ocr[-self._MAX_RECENT_OCR:]
                self._state.screen_text = "\n".join(self._state.recent_ocr)
            self._state.last_update = event.timestamp
            self._add_event(event)

    def _on_ui(self, event: PerceptionEvent):
        with self._lock:
            self._state.ui_elements.append(event.payload)
            if len(self._state.ui_elements) > self._MAX_UI_ELEMENTS:
                self._state.ui_elements = self._state.ui_elements[-self._MAX_UI_ELEMENTS:]
            self._state.last_update = event.timestamp
            self._add_event(event)

    def _add_event(self, event: PerceptionEvent):
        self._state.recent_events.append(event.to_dict())
        if len(self._state.recent_events) > self._MAX_RECENT_EVENTS:
            self._state.recent_events = self._state.recent_events[-self._MAX_RECENT_EVENTS:]
