"""
差异检测器 — 聚合、分配强度、持久化

单例通过模块级工厂 get_detector() + threading.Lock 管理
两种输入模式:
  1. 推送模式: 感知系统通过 ingest() 主动推送事件
  2. 拉取模式: scan() 遍历已注册源调用 detect()（兼容）

scan() 流程:
  1. 遍历所有已启用源 → detect()
  2. IntensityAssigner.assign_batch()
  3. Repository.save() 持久化每条差异
  4. gcm_pool.add_event() 记录事件日志
  5. Repository.dissolve_expired() 清理过期
  6. 触发高强度差异回调 (intensity >= 50)

ingest() 流程:
  1. 感知事件 → Difference(source_type="perception")
  2. IntensityAssigner 赋值
  3. 持久化 + 回调
"""
import time
import threading
from typing import List, Optional, Callable

from modules.difference_detector.models import Difference
from modules.difference_detector.intensity import IntensityAssigner
from modules.difference_detector.repository import DifferenceRepository
from modules.difference_detector.sources.base import DifferenceSourceRegistry
from modules.difference_detector.sources.time_source import TimeDifferenceSource
from modules.difference_detector.sources.internal_source import InternalStateDifferenceSource
from modules.difference_detector.sources.behavioral_source import BehavioralDifferenceSource
from modules.difference_detector.sources.expectation_source import ExpectationDifferenceSource
from utils.logger import setup_logger

logger = setup_logger("difference_detector")

HIGH_INTENSITY_THRESHOLD = 50.0


class DifferenceDetector:
    """差异检测器 — 聚合所有维度检测"""

    def __init__(self):

        self.registry = DifferenceSourceRegistry()
        self.intensity_assigner = IntensityAssigner()
        self.repository = DifferenceRepository()
        self._lock = threading.Lock()
        self._scan_count: int = 0
        self._last_scan: float = 0.0
        self._total_differences: int = 0

        # 高强度差异回调 (intensity >= HIGH_INTENSITY_THRESHOLD)
        self._high_intensity_callbacks: List[Callable[[List[Difference]], None]] = []

        # 注册默认源
        self._register_default_sources()

        logger.info("差异检测器初始化完成 (Stage 1: continuous perception)")

    def _register_default_sources(self) -> None:
        """注册默认差异源"""
        self.registry.register(TimeDifferenceSource())
        self.registry.register(InternalStateDifferenceSource())
        self.registry.register(BehavioralDifferenceSource())
        self.registry.register(ExpectationDifferenceSource())
        logger.info(f"已注册 {len(self.registry.registered_types)} 个差异源: {self.registry.registered_types}")

    def scan(self) -> List[Difference]:
        """执行一次完整扫描

        CONC-9: Detect from sources outside lock to allow parallel detection
        Only lock during shared state updates (persistence, stats)
        """
        all_differences: List[Difference] = []

        # Detect from each source without lock
        # Sources should be independently thread-safe
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

        # Only lock for shared state updates (intensity, persistence, stats)
        with self._lock:
            # 强度赋值
            if all_differences:
                self.intensity_assigner.assign_batch(all_differences)

            # 持久化每条差异
            for diff in all_differences:
                try:
                    self.repository.save(diff)
                except Exception as e:
                    logger.error(f"持久化差异 {diff.id} 失败: {e}")

            # 事件日志
            if all_differences:
                try:
                    self._log_to_gcm(all_differences)
                except Exception as e:
                    logger.debug(f"事件日志记录失败: {e}")

            # 清理过期
            try:
                dissolved = self.repository.dissolve_expired()
                if dissolved:
                    logger.debug(f"溶解 {dissolved} 条过期差异")
            except Exception as e:
                logger.debug(f"过期清理失败: {e}")

            self._scan_count += 1
            self._last_scan = time.time()
            self._total_differences += len(all_differences)

        # 高强度差异回调（锁外执行，避免回调死锁）
        if all_differences:
            self._fire_high_intensity_callbacks(all_differences)

        return all_differences

    def _log_to_gcm(self, differences: List[Difference]) -> None:
        """将差异记录到全局上下文池事件日志"""
        try:
            from modules.thinking.context.global_context_pool import gcm_pool
            from modules.thinking.context.types import EventRecord, EventType
        except ImportError:
            return

        for diff in differences[:10]:  # 最多记录 10 条避免日志爆炸
            record = EventRecord(
                timestamp=time.time(),
                source_role="difference_detector",
                event_type=EventType.SYSTEM,
                content={
                    "diff_id": diff.id,
                    "source_type": diff.source_type,
                    "category": diff.category,
                    "intensity": diff.intensity,
                },
                importance=diff.intensity / 100.0,
            )
            gcm_pool.add_event(record)

    def get_active(self, source_type: str = None, min_intensity: float = 0.0, limit: int = 50) -> List[dict]:
        return self.repository.get_active(
            source_type=source_type,
            min_intensity=min_intensity,
            limit=limit,
        )

    def get_active_differences(self, source_type: str = None, min_intensity: float = 0.0, limit: int = 50) -> List[Difference]:
        """get_active 的类型化版本 — 返回 Difference 对象列表"""
        return [Difference(**d) for d in self.get_active(source_type=source_type, min_intensity=min_intensity, limit=limit)]

    def get_history(self, limit: int = 100) -> List[dict]:
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
        """通知有时间活动 (重置空闲计时)"""
        time_source = self.registry.get("time")
        if time_source and hasattr(time_source, "notify_activity"):
            time_source.notify_activity()

    # ------------------------------------------------------------------
    # 推送模式: 感知系统主动推送事件
    # ------------------------------------------------------------------

    # 感知事件类型 → (category, base_intensity) 映射
    _PERCEPTION_CATEGORY_MAP = {
        # 文件变化
        ("file", "created"):  ("file_created", 30.0),
        ("file", "modified"): ("file_modified", 25.0),
        ("file", "deleted"):  ("file_deleted", 40.0),
        ("file", "moved"):    ("file_moved", 20.0),
        # 对话变化
        ("dialog", "created"):  ("dialog_new_message", 20.0),
        ("dialog", "modified"): ("dialog_edited", 15.0),
        # 屏幕变化
        ("screen", "changed"): ("screen_changed", 15.0),
    }

    def ingest(self, target_type: str, change_type: str,
               target: str = "", details: Optional[dict] = None,
               urgency: float = 0.5) -> Optional[Difference]:
        """接收感知系统推送的事件，转换为差异并处理

        由 PerceptionManager 在检测到变化时调用。
        推送模式的核心入口，不经过 scan() 流程。

        Args:
            target_type: 感知对象类型 ("file" | "dialog" | "screen")
            change_type: 变化类型 ("created" | "modified" | "deleted" | "moved" | "changed")
            target: 变化目标描述（文件路径、对话内容等）
            details: 附加信息
            urgency: 感知系统计算的紧急程度 (0-1)

        Returns:
            生成的 Difference 对象，或 None（被过滤时）
        """
        # 映射到 category 和基础强度
        key = (target_type, change_type)
        category, base_intensity = self._PERCEPTION_CATEGORY_MAP.get(
            key, (f"{target_type}_{change_type}", 20.0)
        )

        # urgency 调整强度（urgency 0-1 → 强度加成 0-20）
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

        # 强度赋值（合并源强度和系统计算）
        self.intensity_assigner.assign(diff)
        # 取源强度和系统强度的较大值，保留感知系统赋予的语义强度
        diff.intensity = max(intensity, diff.intensity)

        # 持久化
        with self._lock:
            try:
                self.repository.save(diff)
            except Exception as e:
                logger.error(f"持久化感知差异失败: {e}")
            self._total_differences += 1

        # 通知时间源有活动（重置空闲计时）
        self.notify_activity()

        # 高强度回调
        if diff.intensity >= HIGH_INTENSITY_THRESHOLD:
            self._fire_high_intensity_callbacks([diff])

        logger.debug(
            f"[ingest] {target_type}/{change_type}: "
            f"category={category}, intensity={diff.intensity:.1f}, "
            f"target={target[:60]}"
        )

        return diff

    def on_high_intensity(self, callback: Callable[[List[Difference]], None]) -> None:
        """注册高强度差异回调 — 当 scan() 发现 intensity >= 50 的新差异时触发

        回调在 daemon 线程中执行，应尽快返回或自行启动异步任务。
        """
        self._high_intensity_callbacks.append(callback)
        logger.debug(f"已注册高强度差异回调 (共 {len(self._high_intensity_callbacks)} 个)")

    def _fire_high_intensity_callbacks(self, differences: List[Difference]) -> None:
        """触发所有高强度差异回调"""
        high_intensity = [d for d in differences if d.intensity >= HIGH_INTENSITY_THRESHOLD]
        if not high_intensity:
            return
        for cb in self._high_intensity_callbacks:
            try:
                cb(high_intensity)
            except Exception as e:
                logger.error(f"高强度差异回调异常: {e}")


# Thread-safe lazy factory (consolidated from __init__.py)
_detector_instance = None
_detector_lock = threading.Lock()

def get_detector() -> DifferenceDetector:
    """Get or create DifferenceDetector instance (lazy factory, thread-safe)"""
    global _detector_instance
    if _detector_instance is None:
        with _detector_lock:
            if _detector_instance is None:
                _detector_instance = DifferenceDetector()
    return _detector_instance
