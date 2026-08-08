"""感知系统编排入口 — 根据配置组装所有模块

从 config.settings 读取配置，选择性启动各子系统。

屏幕 UI 检测由 TouchpointDetector（检测工具）和 ScreenMonitorMCP（MCP server）处理。
"""
import threading
from typing import Optional

from utils.logger import setup_logger

logger = setup_logger("perception_setup")


class PerceptionSystem:
    """感知系统编排器

    持有所有子模块实例，管理生命周期。
    从 config.settings 读取子系统开关。

    当前子系统：
    - 语音检测器（可选）
    - 世界状态管理器
    - 主动触发（基于差异检测事件）
    """

    def __init__(self):
        self.world_state = None
        self.event_bus = None
        self.voice_detector = None
        self.proactive_trigger = None
        self.window_detector = None
        self.ocr_detector = None
        self._window_detector_thread = None
        self._window_stop_event = threading.Event()
        self._started = False

    def setup(self, **overrides) -> None:
        if self._started:
            self.stop()

        self.voice_detector = None
        self.proactive_trigger = None
        self.ocr_detector = None

        from config.settings import settings
        from modules.perception.events.bus import get_event_bus
        from modules.perception.state.world_state import WorldStateManager

        cfg = {
            "voice_enabled": getattr(settings, "PERCEPTION_VOICE_ENABLED", False),
            "voice_wake_prefix": getattr(settings, "PERCEPTION_VOICE_WAKE_PREFIX", "科特"),
            "voice_wake_suffix": getattr(settings, "PERCEPTION_VOICE_WAKE_SUFFIX", "完毕"),
            "voice_mode": getattr(settings, "PERCEPTION_VOICE_MODE", "hotkey"),
            "voice_hotkey": getattr(settings, "PERCEPTION_VOICE_HOTKEY", "f8"),
            "voice_end_stop": getattr(settings, "PERCEPTION_VOICE_END_STOP", True),
            "voice_max_duration": getattr(settings, "PERCEPTION_VOICE_MAX_DURATION", 60.0),
            "voice_end_check_interval": getattr(settings, "PERCEPTION_VOICE_END_CHECK_INTERVAL", 3.0),
            "proactive_enabled": getattr(settings, "PROACTIVE_OUTREACH_ENABLED", False),
            "voice_device": getattr(settings, "PERCEPTION_VOICE_DEVICE", None),
            "voice_model": getattr(settings, "PERCEPTION_VOICE_MODEL", "tiny"),
            "voice_language": getattr(settings, "PERCEPTION_VOICE_LANGUAGE", "zh"),
            "voice_energy": getattr(settings, "PERCEPTION_VOICE_ENERGY_THRESHOLD", 300),
            "voice_timeout": getattr(settings, "PERCEPTION_VOICE_TIMEOUT", 10.0),
        }
        cfg.update(overrides)

        # 1. 事件总线
        self.event_bus = get_event_bus()

        # 2. 语音检测器
        if cfg["voice_enabled"]:
            self._setup_voice_detector(cfg)
        else:
            logger.info("语音感知已禁用")

        # 3. 世界状态管理器
        self.world_state = WorldStateManager()
        if self.event_bus:
            self.world_state.start(self.event_bus)

        # 5. 主动触发
        if cfg["proactive_enabled"]:
            from modules.perception.trigger import ProactiveTrigger
            self.proactive_trigger = ProactiveTrigger()
            self.proactive_trigger.start(self.event_bus)
            logger.info("主动触发已启动")
        else:
            logger.info("主动触发已禁用")

        # 5b. 桌面宠物（语音触发 + 主会话对话）
        try:
            from modules.desktop_pet.pet_engine import PetEngine
            self.pet_engine = PetEngine.get_instance(self.event_bus)
            self.pet_engine.start()
        except Exception as e:
            logger.warning(f"桌面宠物启动失败: {e}")
            self.pet_engine = None

        # 6. 窗口检测器（定时 publish SCREEN_WINDOW 到事件总线）
        if getattr(settings, "PERCEPTION_SCREEN_ENABLED", True):
            self._setup_window_detector()

        # 7. OCR 检测器（定时截图 + 识别文字）
        if getattr(settings, "PERCEPTION_SCREEN_ENABLED", True):
            self._setup_ocr_detector()

        logger.info("感知系统组装完成")

    def _setup_voice_detector(self, cfg: dict):
        mode = cfg.get("voice_mode", "hotkey")
        if mode == "hotkey":
            from modules.perception.detectors.hotkey_voice_detector import (
                HotkeyVoiceDetector,
            )
            self.voice_detector = HotkeyVoiceDetector(
                hotkey=cfg.get("voice_hotkey", "f8"),
                device_index=cfg["voice_device"],
                model_size=cfg["voice_model"],
                language=cfg["voice_language"],
                wake_word=cfg.get("voice_wake_prefix", "科特"),
                end_word=cfg.get("voice_wake_suffix", "完毕"),
                end_stop=cfg.get("voice_end_stop", True),
                max_duration=cfg.get("voice_max_duration", 60.0),
                end_check_interval=cfg.get("voice_end_check_interval", 3.0),
                event_bus=self.event_bus,
            )
        else:
            from modules.perception.detectors.voice_detector import VoiceDetector
            self.voice_detector = VoiceDetector(
                device_index=cfg["voice_device"],
                model_size=cfg["voice_model"],
                language=cfg["voice_language"],
                energy_threshold=cfg["voice_energy"],
                timeout=cfg["voice_timeout"],
                wake_word=cfg.get("voice_wake_prefix", "科特"),
                end_word=cfg.get("voice_wake_suffix", "完毕"),
            )
        if self.voice_detector and self.voice_detector.is_available():
            self.voice_detector.start()
            logger.info(f"语音检测器: 已启动 (mode={mode})")
        else:
            logger.warning("语音检测器: 依赖不可用")
            self.voice_detector = None

    def _setup_window_detector(self) -> None:
        """启动窗口检测器后台线程，定时 publish SCREEN_WINDOW 到事件总线"""
        self._window_stop_event.clear()
        try:
            from modules.perception.detectors.window_detector import WindowDetector
            self.window_detector = WindowDetector()
            if self.window_detector.is_available():
                self._window_detector_thread = threading.Thread(
                    target=self._window_detector_loop,
                    daemon=True,
                    name="perception-window",
                )
                self._window_detector_thread.start()
                logger.info("窗口检测器: 已启动 (1Hz → SCREEN_WINDOW 事件)")
            else:
                self.window_detector = None
                logger.info("窗口检测器: 依赖不可用")
        except Exception as e:
            self.window_detector = None
            logger.debug(f"窗口检测器初始化失败 (非致命): {e}")

    def _window_detector_loop(self) -> None:
        """窗口检测器后台循环"""
        import time
        import numpy as np
        interval = 1.0
        while not self._window_stop_event.is_set():
            try:
                if self.window_detector:
                    events = self.window_detector.detect(np.empty(0), "_system")
                    for evt in events:
                        if self.event_bus:
                            self.event_bus.publish(evt)
            except Exception:
                pass
            for _ in range(int(interval * 10)):
                if self._window_stop_event.is_set():
                    return
                time.sleep(0.1)

    def _setup_ocr_detector(self) -> None:
        """启动 OCR 检测器，订阅 SCREEN_DIFF 事件"""
        try:
            from modules.perception.detectors.ocr_detector import OCRDetector
            self.ocr_detector = OCRDetector(threshold=0.35)
            if self.ocr_detector.is_available():
                self.ocr_detector.start(event_bus=self.event_bus)
                logger.info("OCR 检测器: 已启动 (变化>=15% 时触发)")
            else:
                self.ocr_detector = None
                logger.info("OCR 检测器: 依赖不可用 (rapidocr)")
        except Exception as e:
            self.ocr_detector = None
            logger.debug(f"OCR 检测器初始化失败 (非致命): {e}")

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        logger.info("感知系统已启动")
        # 会话定时任务（每会话独立，到点调用逻辑）
        try:
            from modules.thinking.scheduled_tasks import get_task_manager
            get_task_manager().start()
        except Exception as e:
            logger.warning(f"会话定时任务启动失败: {e}")

    def stop(self) -> None:
        if not self._started:
            return
        if self.ocr_detector:
            self.ocr_detector.stop()
        if self.proactive_trigger:
            self.proactive_trigger.stop()
        if self._window_detector_thread:
            self._window_stop_event.set()
            self._window_detector_thread.join(timeout=3)
            self._window_detector_thread = None
        if self.world_state:
            from modules.perception.events.bus import get_event_bus
            self.world_state.stop(get_event_bus())
        self._started = False
        logger.info("感知系统已停止")

    def get_status(self) -> dict:
        return {
            "started": self._started,
            "voice_available": self.voice_detector is not None,
            "voice_detector_type": self.voice_detector.detector_type if self.voice_detector else None,
            "window_detector_available": self.window_detector is not None and self.window_detector.is_available(),
            "ocr_detector_available": self.ocr_detector is not None and self.ocr_detector.is_available(),
            "proactive_trigger": self.proactive_trigger.get_stats() if self.proactive_trigger else None,
            "world_state": self.world_state.get_state().to_dict() if self.world_state else None,
            "event_bus": self.event_bus.get_stats() if self.event_bus else None,
        }



_system: Optional[PerceptionSystem] = None
_system_lock = threading.Lock()


def get_perception_system() -> PerceptionSystem:
    """获取感知系统全局单例（线程安全，双重检查锁）"""
    global _system
    if _system is None:
        with _system_lock:
            if _system is None:
                _system = PerceptionSystem()
    return _system
