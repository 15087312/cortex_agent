"""
感知集成器 — 事件总线 → PerceptionPool

订阅感知事件，提取描述，写入统一池。
get_context_summary / get_attention_prompt / _extract_description 已被 PerceptionPool.snapshot() 替代。
"""
import threading
from typing import Dict, Any

from utils.logger import setup_logger

logger = setup_logger("perception_integration")


class PerceptionIntegrator:
    """感知集成器 — 桥接事件总线与 PerceptionPool"""

    def __init__(self):
        from modules.perception.pool import PerceptionPool
        self._auto_monitoring = True
        self._context_injection_enabled = True
        self._sub_id: str = ""
        self.pool = PerceptionPool()
        logger.info("感知集成器初始化完成")

    def start(self) -> None:
        if self._auto_monitoring:
            from modules.perception import get_perception_system
            ps = get_perception_system()
            if not ps._started:
                ps.setup()
                ps.start()
            self._subscribe_events()
            logger.info("感知监控已启动，已订阅感知事件")

    def _subscribe_events(self) -> None:
        try:
            from modules.perception.events.bus import get_event_bus
            from modules.perception.events.types import PerceptionEventType

            event_bus = get_event_bus()
            for event_type in [
                PerceptionEventType.SCREEN_DIFF,
                PerceptionEventType.SCREEN_WINDOW,
                PerceptionEventType.SCREEN_OCR,
                PerceptionEventType.SPEECH_DETECTED,
                PerceptionEventType.DIFFERENCE_DETECTED,
            ]:
                try:
                    event_bus.subscribe(event_type, self._on_perception_event)
                except Exception:
                    pass
            logger.info("已订阅感知事件")
        except Exception as e:
            logger.debug(f"订阅感知事件失败 (非致命): {e}")

    def _on_perception_event(self, event) -> None:
        try:
            payload = event.payload if hasattr(event, 'payload') else {}
            event_type = event.event_type if hasattr(event, 'event_type') else 'unknown'
            source = payload.get('source_type', payload.get('type', 'unknown'))

            description = self._format_description(event_type, payload)
            if description:
                self.pool.add(event_type, source, description, payload)
        except Exception as e:
            logger.debug(f"处理感知事件异常 (非致命): {e}")

    @staticmethod
    def _format_description(event_type: str, payload: Dict[str, Any]) -> str:
        try:
            if event_type == "screen.diff":
                ratio = payload.get("change_ratio", 0)
                if ratio > 0.3:
                    return f"屏幕大幅变化 ({ratio*100:.0f}% 面积)"
                elif ratio > 0.1:
                    return f"屏幕中等变化 ({ratio*100:.0f}% 面积)"
                return f"屏幕小幅变化 ({ratio*100:.0f}% 面积)"

            elif event_type == "screen.window":
                app_name = payload.get("app_name", "")
                window_title = payload.get("window_title", "")
                prev_app = payload.get("prev_app", "")
                if not app_name:
                    return ""  # 空 payload 不上报
                if prev_app and prev_app != app_name:
                    return f"窗口切换: {prev_app} → {app_name} ({window_title})"
                return f"当前窗口: {app_name} - {window_title}"

            elif event_type == "screen.ocr":
                new_lines = payload.get("new_lines", [])
                changed = payload.get("changed_count", len(new_lines))
                top = payload.get("top_elements", [])
                roi = payload.get("roi_name", "屏幕")
                if top:
                    return f"屏幕新文本 [{roi}] ({changed}处新增): " + ", ".join(top[:5])
                if new_lines:
                    return f"屏幕新文本 [{roi}]: " + ", ".join(new_lines[:5])
                return ""

            elif event_type == "screen.ui":
                desc = payload.get("description", "")
                count = payload.get("element_count", 0)
                return f"屏幕UI: {count}个元素" + (f", {desc}" if desc else "")

            elif event_type == "speech.detected":
                text = payload.get("text", "")
                return f"语音: {text}" if text else ""

            elif event_type == "difference.detected":
                description = payload.get("description", "")
                return description[:200] if description else ""

            return payload.get("description", payload.get("text", ""))[:200]
        except Exception:
            return ""

    def stop(self) -> None:
        from modules.perception import get_perception_system
        ps = get_perception_system()
        ps.stop()
        if self._sub_id:
            try:
                from modules.perception.events.bus import get_event_bus
                get_event_bus().unsubscribe(self._sub_id)
            except Exception:
                pass
            self._sub_id = ""
        logger.info("感知监控已停止")


_perception_integrator_instance = None
_perception_integrator_lock = threading.Lock()


def get_perception_integrator() -> PerceptionIntegrator:
    global _perception_integrator_instance
    if _perception_integrator_instance is None:
        with _perception_integrator_lock:
            if _perception_integrator_instance is None:
                _perception_integrator_instance = PerceptionIntegrator()
    return _perception_integrator_instance
