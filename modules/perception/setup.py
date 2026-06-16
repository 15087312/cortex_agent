"""感知系统编排入口 — 根据配置组装所有模块

从 config.settings 读取配置，选择性启动各子系统。

注意：旧版屏幕感知管道（capture/frame_diff/pipeline/detectors）已被移除。
屏幕 UI 检测统一由 TouchpointDetector（检测工具）和 ScreenMonitorMCP（MCP server）处理。
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
        self.pipeline = None
        self.world_state = None
        self.event_bus = None
        self.voice_detector = None
        self.proactive_trigger = None
        self.mcp_detector = None
        self._started = False

    def setup(self, **overrides) -> None:
        if self._started:
            self.stop()

        self.pipeline = None
        self.voice_detector = None
        self.proactive_trigger = None

        from config.settings import settings
        from modules.perception.events.bus import get_event_bus
        from modules.perception.state.world_state import WorldStateManager

        cfg = {
            "voice_enabled": getattr(settings, "PERCEPTION_VOICE_ENABLED", False),
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

        # 4. 主动触发
        if cfg["proactive_enabled"]:
            from modules.perception.trigger import ProactiveTrigger
            self.proactive_trigger = ProactiveTrigger()
            self.proactive_trigger.start(self.event_bus)
            logger.info("主动触发已启动")
        else:
            logger.info("主动触发已禁用")

        logger.info("感知系统组装完成（屏幕管道已移除，使用 Touchpoint + ScreenMonitorMCP）")

    def _setup_voice_detector(self, cfg: dict):
        from modules.perception.detectors.voice_detector import VoiceDetector
        self.voice_detector = VoiceDetector(
            device_index=cfg["voice_device"],
            model_size=cfg["voice_model"],
            language=cfg["voice_language"],
            energy_threshold=cfg["voice_energy"],
            timeout=cfg["voice_timeout"],
        )
        if self.voice_detector and self.voice_detector.is_available():
            self.voice_detector.start()
            logger.info("语音检测器: 已启动")
        else:
            logger.warning("语音检测器: 依赖不可用")
            self.voice_detector = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        logger.info("感知系统已启动")

    def stop(self) -> None:
        if not self._started:
            return
        if self.proactive_trigger:
            self.proactive_trigger.stop()
        if self.world_state:
            from modules.perception.events.bus import get_event_bus
            self.world_state.stop(get_event_bus())
        self._started = False
        logger.info("感知系统已停止")

    def get_status(self) -> dict:
        return {
            "started": self._started,
            "pipeline": None,
            "voice_available": self.voice_detector is not None,
            "voice_detector_type": self.voice_detector.detector_type if self.voice_detector else None,
            "mcp_available": self.mcp_detector is not None,
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
