"""ROI 管理器 — ROI 定义的注册、持久化、动态更新"""
import json
import os
from typing import Dict, List, Optional

from modules.perception.pipeline.roi_dispatcher import ROIRegion
from utils.logger import setup_logger

logger = setup_logger("perception_roi_manager")

# 默认 ROI 配置
_DEFAULT_ROIS: List[Dict] = [
    # 用户可根据实际屏幕布局自定义
    # {"name": "notification", "rect": [0, 0, 1920, 50], "detector": "ui", "priority": 10},
    # {"name": "chat_area", "rect": [0, 100, 800, 600], "detector": "ocr", "priority": 5},
]


class ROIManager:
    """ROI 管理器

    管理 ROI 区域定义的注册、持久化、动态更新。
    支持从配置文件加载和保存。
    """

    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path
        self._rois: Dict[str, ROIRegion] = {}

    def load_defaults(self) -> None:
        """加载默认 ROI 配置"""
        for roi_def in _DEFAULT_ROIS:
            self.add(ROIRegion(
                name=roi_def["name"],
                rect=tuple(roi_def["rect"]),
                detector_type=roi_def["detector"],
                priority=roi_def.get("priority", 0),
            ))

    def load_from_file(self, path: Optional[str] = None) -> bool:
        """从 JSON 文件加载 ROI 配置"""
        path = path or self._config_path
        if not path or not os.path.exists(path):
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for roi_def in data.get("rois", []):
                self.add(ROIRegion(
                    name=roi_def["name"],
                    rect=tuple(roi_def["rect"]),
                    detector_type=roi_def["detector"],
                    priority=roi_def.get("priority", 0),
                    overlap_threshold=roi_def.get("overlap_threshold", 0.1),
                ))
            logger.info(f"从文件加载 {len(data.get('rois', []))} 个 ROI")
            return True
        except Exception as e:
            logger.warning(f"加载 ROI 配置失败: {e}")
            return False

    def save_to_file(self, path: Optional[str] = None) -> bool:
        """保存 ROI 配置到 JSON 文件"""
        path = path or self._config_path
        if not path:
            return False
        try:
            data = {
                "rois": [
                    {
                        "name": roi.name,
                        "rect": list(roi.rect),
                        "detector": roi.detector_type,
                        "priority": roi.priority,
                        "overlap_threshold": roi.overlap_threshold,
                    }
                    for roi in self._rois.values()
                ]
            }
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            logger.warning(f"保存 ROI 配置失败: {e}")
            return False

    def add(self, roi: ROIRegion) -> None:
        self._rois[roi.name] = roi

    def remove(self, name: str) -> bool:
        return self._rois.pop(name, None) is not None

    def get(self, name: str) -> Optional[ROIRegion]:
        return self._rois.get(name)

    def get_all(self) -> List[ROIRegion]:
        return list(self._rois.values())

    def apply_to_dispatcher(self, dispatcher) -> None:
        """将所有 ROI 应用到 ROIDispatcher"""
        dispatcher.clear()
        for roi in self._rois.values():
            dispatcher.register_roi(roi)
        logger.info(f"应用 {len(self._rois)} 个 ROI 到分发器")
