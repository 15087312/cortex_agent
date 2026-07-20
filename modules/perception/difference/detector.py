"""差异检测器 — 核心编排器，协调差异源、强度分配、持久化

工作流:
1. 扫描所有启用的差异源 → 获得 Difference 列表
2. IntensityAssigner 分配强度值
3. Repository 持久化
4. 过期的差异标记为 dissolved
5. 高强度差异触发回调
"""
import time
import threading
from typing import List, Optional, Callable

from modules.perception.difference.models import Difference
from modules.perception.difference.intensity import IntensityAssigner
from modules.perception.difference.repository import DifferenceRepository
from modules.perception.difference.sources.base import DifferenceSourceRegistry
from modules.perception.difference.sources.time_source import TimeDifferenceSource
from utils.logger import setup_logger

logger = setup_logger("difference_detector")

# 高强度阈值：强度 >= 50 认为值得关注，触发回调
HIGH_INTENSITY_THRESHOLD = 50.0


class DifferenceDetector:
    """差异检测器

    协调差异源注册、扫描、强度分配、持久化和高强度通知的核心类。
    通过 get_detector() 获取全局单例。
    """

    def __init__(self):
        self.registry = DifferenceSourceRegistry()
        self.intensity_assigner = IntensityAssigner()
        self.repository = DifferenceRepository()
        self._lock = threading.Lock()
        self._scan_count: int = 0
        self._last_scan: float = 0.0
        self._total_differences: int = 0
        self._high_intensity_callbacks: List[Callable[[List[Difference]], None]] = []

        self.registry.register(TimeDifferenceSource())
        logger.info(f"已注册 {len(self.registry.registered_types)} 个差异源: {self.registry.registered_types}")
        logger.info("差异检测器初始化完成 (Stage 1: continuous perception)")

    def scan(self) -> List[Difference]:
        """执行一次完整差异扫描

        遍历所有启用的差异源，收集差异，分配强度，持久化，
        清理过期项，触发高强度回调。
        """
        all_differences: List[Difference] = []

        for source in self.registry.get_enabled_sources():
            try:
                differences = source.detect()
                if differences:
                    all_differences.extend(differences)
                    logger.debug(
                        f"[{source.source_type}] 检测到 {len(differences)} 个差异"
                    )
            except Exception as e:
                logger.error(f"[{source.source_type}] 检测异常: {type(e).__name__}: {e}")

        with self._lock:
            if all_differences:
                self.intensity_assigner.assign_batch(all_differences)

            for diff in all_differences:
                try:
                    self.repository.save(diff)
                except Exception as e:
                    logger.error(f"持久化差异 {diff.id} 失败: {e}")

            if all_differences:
                try:
                    self._log_to_gcm(all_differences)
                except Exception as e:
                    logger.debug(f"事件日志记录失败: {e}")

            try:
                dissolved = self.repository.dissolve_expired()
                if dissolved:
                    logger.debug(f"溶解 {dissolved} 条过期差异")
            except Exception as e:
                logger.debug(f"过期清理失败: {e}")

            self._scan_count += 1
            self._last_scan = time.time()
            self._total_differences += len(all_differences)

        if all_differences:
            self._fire_high_intensity_callbacks(all_differences)

        return all_differences

    def _log_to_gcm(self, differences: List[Difference]) -> None:
        pass

    def get_active(self, source_type: str = None, min_intensity: float = 0.0, limit: int = 50) -> List[dict]:
        """获取活跃差异列表（用于 API / 查询）"""
        return self.repository.get_active(
            source_type=source_type,
            min_intensity=min_intensity,
            limit=limit,
        )

    def get_active_differences(self, source_type: str = None, min_intensity: float = 0.0, limit: int = 50) -> List[Difference]:
        """获取活跃差异，以 Difference 对象（而非 dict）形式返回"""
        return [Difference(**d) for d in self.get_active(source_type=source_type, min_intensity=min_intensity, limit=limit)]

    def get_history(self, limit: int = 100) -> List[dict]:
        """获取历史差异记录"""
        return self.repository.get_history(limit=limit)

    def get_status(self) -> dict:
        stats = self.repository.get_stats()
        return {
            "initialized": True,
            "scan_count": self._scan_count,
            "last_scan": self._last_scan,
            "total_differences_detected": self._total_differences,
            "storage": stats,
            "sources": self.registry.list_sources(),
        }

    def notify_activity(self) -> None:
        """通知用户有活动（重置空闲计时）"""
        time_source = self.registry.get("time")
        if time_source and hasattr(time_source, "notify_activity"):
            time_source.notify_activity()

    _PERCEPTION_CATEGORY_MAP = {
        ("file", "created"):  ("file_created", 30.0),
        ("file", "modified"): ("file_modified", 25.0),
        ("file", "deleted"):  ("file_deleted", 40.0),
        ("file", "moved"):    ("file_moved", 20.0),
        ("dialog", "created"):  ("dialog_new_message", 20.0),
        ("dialog", "modified"): ("dialog_edited", 15.0),
        ("screen", "changed"): ("screen_changed", 30.0),
    }

    def ingest(self, target_type: str, change_type: str,
               target: str = "", details: Optional[dict] = None,
               urgency: float = 0.5) -> Optional[Difference]:
        """从外部摄入感知事件（文件/对话/屏幕变化）

        根据 target_type + change_type 查映射表确定分类和基础强度，
        再按 urgency 调整最终强度。
        """
        key = (target_type, change_type)
        category, base_intensity = self._PERCEPTION_CATEGORY_MAP.get(
            key, (f"{target_type}_{change_type}", 20.0)
        )

        intensity = base_intensity + urgency * 20.0

        diff = Difference(
            source_type="perception",
            category=category,
            intensity=min(intensity, 100.0),
            ttl=15 * 60,
            payload={
                "target_type": target_type,
                "change_type": change_type,
                "target": target,
                "details": details or {},
                "urgency": urgency,
            },
        )

        self.intensity_assigner.assign(diff)
        diff.intensity = max(intensity, diff.intensity)

        with self._lock:
            try:
                self.repository.save(diff)
            except Exception as e:
                logger.error(f"持久化感知差异失败: {e}")
            self._total_differences += 1

        self.notify_activity()  # 用户有操作，重置空闲计时

        if diff.intensity >= HIGH_INTENSITY_THRESHOLD:
            self._fire_high_intensity_callbacks([diff])

        logger.debug(
            f"[ingest] {target_type}/{change_type}: "
            f"category={category}, intensity={diff.intensity:.1f}, "
            f"target={target[:60]}"
        )

        return diff

    def on_high_intensity(self, callback: Callable[[List[Difference]], None]) -> None:
        """注册高强度差异回调（如触发主动思考）"""
        self._high_intensity_callbacks.append(callback)
        logger.debug(f"已注册高强度差异回调 (共 {len(self._high_intensity_callbacks)} 个)")

    def _fire_high_intensity_callbacks(self, differences: List[Difference]) -> None:
        high_intensity = [d for d in differences if d.intensity >= HIGH_INTENSITY_THRESHOLD]
        if not high_intensity:
            return
        for cb in self._high_intensity_callbacks:
            try:
                cb(high_intensity)
            except Exception as e:
                logger.error(f"高强度差异回调异常: {e}")


_detector_instance = None
_detector_lock = threading.Lock()


def get_detector() -> DifferenceDetector:
    global _detector_instance
    if _detector_instance is None:
        with _detector_lock:
            if _detector_instance is None:
                _detector_instance = DifferenceDetector()
    return _detector_instance
