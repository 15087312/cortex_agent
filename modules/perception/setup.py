"""感知系统编排入口 — 组装所有模块，注入依赖，注册回调

这是唯一允许 import 所有感知子模块的地方。
api/main.py 调用 setup_perception_system() 来启动整个系统。
"""
from typing import Optional, Tuple

from utils.logger import setup_logger

logger = setup_logger("perception_setup")


class PerceptionSystem:
    """感知系统编排器

    持有所有子模块实例，管理生命周期。
    """

    def __init__(self):
        self.pipeline = None
        self.world_state = None
        self.event_bus = None
        self.perception_source = None
        self._started = False

    def setup(
        self,
        fps: int = 5,
        roi_config_path: Optional[str] = None,
        enable_ocr: bool = True,
        enable_ui: bool = True,
        enable_window: bool = True,
        screen_roi: Optional[Tuple[int, int, int, int]] = None,
    ) -> None:
        """组装感知系统

        Args:
            fps: 屏幕捕获帧率
            roi_config_path: ROI 配置文件路径
            enable_ocr: 是否启用 OCR 检测
            enable_ui: 是否启用 UI 检测
            enable_window: 是否启用窗口状态检测
            screen_roi: 屏幕捕获区域 (x, y, w, h)，None=全屏
        """
        # 防止重复 setup 泄漏资源
        if self._started:
            self.stop()
        from modules.perception.events.bus import get_event_bus
        from modules.perception.pipeline.capture import create_capture_backend
        from modules.perception.pipeline.frame_diff import FrameDiffDetector
        from modules.perception.pipeline.roi_dispatcher import ROIDispatcher
        from modules.perception.pipeline.pipeline import PerceptionPipeline
        from modules.perception.state.world_state import WorldStateManager
        from modules.perception.state.perception_source import PerceptionDifferenceSource
        from modules.perception.roi.manager import ROIManager

        # 1. 事件总线
        self.event_bus = get_event_bus()

        # 2. 捕获后端
        capture = create_capture_backend()
        logger.info(f"捕获后端: {capture.platform_name} (available={capture.is_available()})")

        # 3. 帧差检测器
        frame_diff = FrameDiffDetector(threshold=25, change_area_threshold=0.01)

        # 4. ROI 分发器 + 管理器
        roi_dispatcher = ROIDispatcher()
        roi_manager = ROIManager(config_path=roi_config_path)
        if roi_config_path:
            roi_manager.load_from_file()
        else:
            roi_manager.load_defaults()
        roi_manager.apply_to_dispatcher(roi_dispatcher)

        # 5. 检测器
        detectors = {}
        if enable_ocr:
            from modules.perception.detectors.ocr_detector import OCRDetector
            det = OCRDetector()
            detectors["ocr"] = det
            logger.info(f"OCR 检测器: available={det.is_available()}")

        if enable_ui:
            from modules.perception.detectors.ui_detector import UIDetector
            det = UIDetector()
            detectors["ui"] = det
            logger.info(f"UI 检测器: available={det.is_available()}")

        if enable_window:
            from modules.perception.detectors.window_detector import WindowDetector
            det = WindowDetector()
            detectors["window"] = det
            logger.info(f"窗口检测器: available={det.is_available()}")

        # 6. 流水线
        self.pipeline = PerceptionPipeline(
            capture=capture,
            frame_diff=frame_diff,
            roi_dispatcher=roi_dispatcher,
            detectors=detectors,
            event_bus=self.event_bus,
            fps=fps,
        )

        # 7. 世界状态
        self.world_state = WorldStateManager()
        self.world_state.start(self.event_bus)

        # 8. 感知差异源（桥接到 DifferenceDetector）
        self.perception_source = PerceptionDifferenceSource(event_bus=self.event_bus)

        logger.info(
            f"感知系统组装完成: fps={fps} "
            f"detectors={list(detectors.keys())} "
            f"rois={len(roi_manager.get_all())}"
        )

    def start(self) -> None:
        """启动感知系统"""
        if self._started:
            return

        if self.pipeline:
            self.pipeline.start()
        if self.perception_source:
            self.perception_source.start()
        self._started = True
        logger.info("感知系统已启动")

    def stop(self) -> None:
        """停止感知系统"""
        if not self._started:
            return

        if self.pipeline:
            self.pipeline.stop()
        if self.world_state and self.event_bus:
            self.world_state.stop(self.event_bus)
        if self.perception_source:
            self.perception_source.stop()
        self._started = False
        logger.info("感知系统已停止")

    def get_status(self) -> dict:
        """获取系统状态"""
        status = {
            "started": self._started,
            "pipeline": self.pipeline.get_stats() if self.pipeline else None,
            "world_state": self.world_state.get_state().to_dict() if self.world_state else None,
            "event_bus": self.event_bus.get_stats() if self.event_bus else None,
        }
        return status


# 全局单例
_system: Optional[PerceptionSystem] = None


def get_perception_system() -> PerceptionSystem:
    """获取感知系统单例"""
    global _system
    if _system is None:
        _system = PerceptionSystem()
    return _system
