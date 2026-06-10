"""感知系统编排入口 — 根据配置组装所有模块

这是唯一允许 import 所有感知子模块的地方。
从 config.settings 读取配置，选择性启动各子系统。
"""
import threading
from typing import Optional, Tuple

from utils.logger import setup_logger

logger = setup_logger("perception_setup")


class PerceptionSystem:
    """感知系统编排器

    持有所有子模块实例，管理生命周期。
    从 config.settings 读取子系统开关。
    """

    def __init__(self):
        self.pipeline = None
        self.world_state = None
        self.event_bus = None
        self.perception_source = None
        self.voice_detector = None
        self.think_trigger = None
        self.file_perception = None
        self.dialog_perception = None
        self._file_monitor_thread = None
        self._file_monitor_running = False
        self._started = False

    def setup(self, **overrides) -> None:
        """根据配置组装感知系统

        Args:
            **overrides: 覆盖配置的参数（用于测试）
        """
        if self._started:
            self.stop()

        # 重置所有组件
        self.pipeline = None
        self.voice_detector = None
        self.think_trigger = None

        from config.settings import settings
        from modules.perception.events.bus import get_event_bus
        from modules.perception.state.world_state import WorldStateManager
        from modules.perception.state.perception_source import PerceptionDifferenceSource

        # 读取配置（可被 overrides 覆盖）
        cfg = {
            "screen_enabled": getattr(settings, "PERCEPTION_SCREEN_ENABLED", True),
            "file_enabled": getattr(settings, "PERCEPTION_FILE_ENABLED", True),
            "dialog_enabled": getattr(settings, "PERCEPTION_DIALOG_ENABLED", True),
            "voice_enabled": getattr(settings, "PERCEPTION_VOICE_ENABLED", False),
            "trigger_enabled": getattr(settings, "PERCEPTION_TRIGGER_THINK", True),
            "trigger_min_intensity": getattr(settings, "PERCEPTION_TRIGGER_MIN_INTENSITY", 50.0),
            "trigger_cooldown": getattr(settings, "PERCEPTION_TRIGGER_COOLDOWN", 60),
            "voice_device": getattr(settings, "PERCEPTION_VOICE_DEVICE", None),
            "voice_model": getattr(settings, "PERCEPTION_VOICE_MODEL", "tiny"),
            "voice_language": getattr(settings, "PERCEPTION_VOICE_LANGUAGE", "zh"),
            "voice_energy": getattr(settings, "PERCEPTION_VOICE_ENERGY_THRESHOLD", 300),
            "voice_timeout": getattr(settings, "PERCEPTION_VOICE_TIMEOUT", 10.0),
            "fps": 5,
        }
        cfg.update(overrides)

        # 1. 事件总线
        self.event_bus = get_event_bus()

        # 2. 屏幕流水线（根据配置）
        if cfg["screen_enabled"]:
            self._setup_screen_pipeline(cfg)
        else:
            logger.info("屏幕感知已禁用")

        # 3. 文件监控（从旧 PerceptionManager 合并）
        if cfg["file_enabled"]:
            self._setup_file_monitoring(cfg)
        else:
            logger.info("文件感知已禁用")

        # 4. 对话监控（从旧 PerceptionManager 合并）
        if cfg["dialog_enabled"]:
            self._setup_dialog_monitoring()
        else:
            logger.info("对话感知已禁用")

        # 5. 语音检测器（根据配置）
        if cfg["voice_enabled"]:
            self._setup_voice_detector(cfg)
        else:
            logger.info("语音感知已禁用")

        # 4. 世界状态
        self.world_state = WorldStateManager()
        self.world_state.start(self.event_bus)

        # 5. 感知差异源
        self.perception_source = PerceptionDifferenceSource(event_bus=self.event_bus)

        # 6. 差异→思考触发器（根据配置）
        if cfg["trigger_enabled"]:
            from modules.perception.state.think_trigger import PerceptionThinkTrigger
            self.think_trigger = PerceptionThinkTrigger(
                min_intensity=cfg["trigger_min_intensity"],
                cooldown_seconds=cfg["trigger_cooldown"],
            )
            self.think_trigger.start(self.event_bus)
            logger.info(
                f"差异→思考触发器: min_intensity={cfg['trigger_min_intensity']} "
                f"cooldown={cfg['trigger_cooldown']}s"
            )
        else:
            logger.info("差异→思考触发器已禁用")

        logger.info("感知系统组装完成")

    def _setup_screen_pipeline(self, cfg: dict):
        """组装屏幕感知流水线"""
        from modules.perception.pipeline.capture import create_capture_backend
        from modules.perception.pipeline.frame_diff import FrameDiffDetector
        from modules.perception.pipeline.roi_dispatcher import ROIDispatcher
        from modules.perception.pipeline.pipeline import PerceptionPipeline
        from modules.perception.roi.manager import ROIManager

        capture = create_capture_backend()
        logger.info(f"捕获后端: {capture.platform_name} (available={capture.is_available()})")

        frame_diff = FrameDiffDetector()

        roi_dispatcher = ROIDispatcher()
        roi_manager = ROIManager()
        roi_manager.load_defaults()
        roi_manager.apply_to_dispatcher(roi_dispatcher)

        detectors = {}

        # 窗口检测器
        from modules.perception.detectors.window_detector import WindowDetector
        det = WindowDetector()
        detectors["window"] = det
        logger.info(f"窗口检测器: available={det.is_available()}")

        # OCR 检测器（如果有依赖）
        try:
            from modules.perception.detectors.ocr_detector import OCRDetector
            det = OCRDetector()
            if det.is_available():
                detectors["ocr"] = det
                logger.info("OCR 检测器: 已启用")
        except Exception:
            pass

        # UI 检测器
        try:
            from modules.perception.detectors.ui_detector import UIDetector
            det = UIDetector()
            if det.is_available():
                detectors["ui"] = det
                logger.info("UI 检测器: 已启用")
        except Exception:
            pass

        self.pipeline = PerceptionPipeline(
            capture=capture,
            frame_diff=frame_diff,
            roi_dispatcher=roi_dispatcher,
            detectors=detectors,
            event_bus=self.event_bus,
            fps=cfg["fps"],
        )

    def _setup_file_monitoring(self, cfg: dict):
        """组装文件监控（从旧 PerceptionManager 合并）"""
        from modules.perception.file_perception import FilePerception
        from config.settings import settings

        watch_paths = ["./", "data/"]
        self.file_perception = FilePerception(watch_paths, enabled=True)
        logger.info("文件监控已初始化 (watchdog)")

    def _setup_dialog_monitoring(self):
        """组装对话监控（从旧 PerceptionManager 合并）"""
        from modules.perception.dialog_perception import DialogPerception
        self.dialog_perception = DialogPerception(enabled=True)
        logger.info("对话监控已初始化")

    def _setup_voice_detector(self, cfg: dict):
        """组装语音检测器"""
        from modules.perception.detectors.voice_detector import VoiceDetector

        self.voice_detector = VoiceDetector(
            device_index=cfg["voice_device"],
            model_size=cfg["voice_model"],
            language=cfg["voice_language"],
            energy_threshold=cfg["voice_energy"],
            timeout=cfg["voice_timeout"],
        )
        if self.voice_detector.is_available():
            self.voice_detector.start()
            logger.info("语音检测器: 已启动")
        else:
            logger.warning("语音检测器: 依赖不可用 (需要 SpeechRecognition + pyaudio + whisper)")
            self.voice_detector = None

    def set_think_trigger_port(self, port) -> None:
        """注入思考触发实现（由编排层调用）"""
        if self.think_trigger:
            self.think_trigger.set_trigger_port(port)

    def start(self) -> None:
        if self._started:
            return
        if self.pipeline:
            self.pipeline.start()
        if self.perception_source:
            self.perception_source.start()
        # 文件监控后台线程
        if self.file_perception:
            self._file_monitor_running = True
            self._file_monitor_thread = threading.Thread(
                target=self._file_monitor_loop, daemon=True, name="file-perception"
            )
            self._file_monitor_thread.start()
        self._started = True
        logger.info("感知系统已启动")

    def _file_monitor_loop(self):
        """文件监控后台循环"""
        import time
        while self._file_monitor_running:
            try:
                changes = self.file_perception.check_changes()
                if changes and self.event_bus:
                    from modules.perception.events.types import PerceptionEvent, PerceptionEventType
                    for change in changes:
                        event = PerceptionEvent(
                            event_type=PerceptionEventType.FILE_CHANGE,
                            data={"change": change.to_prompt(), "path": change.target},
                        )
                        self.event_bus.publish(event)
            except Exception as e:
                logger.debug(f"文件监控循环异常: {e}")
            time.sleep(2.0)

    def stop(self) -> None:
        if not self._started:
            return
        if self.pipeline:
            self.pipeline.stop()
        if self.voice_detector:
            self.voice_detector.stop()
        if self.world_state and self.event_bus:
            self.world_state.stop(self.event_bus)
        if self.perception_source:
            self.perception_source.stop()
        if self.think_trigger and self.event_bus:
            self.think_trigger.stop(self.event_bus)
        # 停止文件监控
        self._file_monitor_running = False
        if self.file_perception:
            self.file_perception.stop()
        self._started = False
        logger.info("感知系统已停止")

    def get_status(self) -> dict:
        status = {
            "started": self._started,
            "pipeline": self.pipeline.get_stats() if self.pipeline else None,
            "voice_available": self.voice_detector is not None,
            "voice_detector_type": self.voice_detector.detector_type if self.voice_detector else None,
            "think_trigger": self.think_trigger.get_stats() if self.think_trigger else None,
            "world_state": self.world_state.get_state().to_dict() if self.world_state else None,
            "event_bus": self.event_bus.get_stats() if self.event_bus else None,
        }
        return status


_system: Optional[PerceptionSystem] = None
_system_lock = threading.Lock()


def get_perception_system() -> PerceptionSystem:
    global _system
    if _system is None:
        with _system_lock:
            if _system is None:
                _system = PerceptionSystem()
    return _system
