"""
审计器 — 监控 GlobalContextPool 健康状态

职责：
- 冗余检测：扫描事件日志找重复/高度相似记录
- 内存检查：追踪池的内存使用，预警泄漏
- 一致性检查：验证文件和事件的一致性
- 统计视图：池健康指标汇总
"""
import time
import threading
from typing import Dict, List, Optional, Any
from collections import Counter

from utils.logger import setup_logger
from .types import EventRecord, EventType, FileInfo
from .global_context_pool import GlobalContextPool
from .compression import CompressionEngine

logger = setup_logger("auditor")

# 审计阈值
DEFAULT_CHECK_INTERVAL_SECONDS = 60   # 自动检查间隔
DEFAULT_MAX_WARNINGS = 100           # 最大保留警告数
MEMORY_WARNING_MB = 500              # 内存使用超过此值发出警告
EVENT_COUNT_WARNING = 8000           # 事件数接近上限时警告
FILE_COUNT_WARNING = 5000            # 文件缓存过大时警告
REDUNDANCY_HIGH_THRESHOLD = 0.3      # 高冗余阈值
REDUNDANCY_MODERATE_THRESHOLD = 0.1  # 中度冗余阈值


class Auditor:
    """
    审计器 — 单例

    定期扫描池状态，发出健康警告，供管理 API 查询。
    """

    _instance: Optional["Auditor"] = None
    _instance_lock = threading.Lock()

    def __new__(cls) -> "Auditor":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._compressor = CompressionEngine()
        self._last_check_time: float = 0.0
        self._check_interval: float = DEFAULT_CHECK_INTERVAL_SECONDS
        self._warnings: List[Dict[str, Any]] = []
        self._max_warnings = DEFAULT_MAX_WARNINGS
        self._initialized = True

    # ========================================================================
    # 冗余检测
    # ========================================================================

    def check_redundancy(
        self,
        pool: GlobalContextPool,
        threshold: float = 0.85,
        max_samples: int = 1000
    ) -> Dict[str, Any]:
        """
        检测事件日志中的冗余

        使用 Jaccard 相似度对比最近事件。

        Returns:
            {
                "redundant_pairs": int,      # 冗余对数量
                "redundancy_ratio": float,    # 冗余率
                "top_duplicates": [...],      # 最重复的内容
                "recommendation": str         # 建议
            }
        """
        events = pool.get_events(limit=max_samples)
        if len(events) < 2:
            return {
                "redundant_pairs": 0,
                "redundancy_ratio": 0.0,
                "top_duplicates": [],
                "recommendation": "事件量不足，无需检测"
            }

        # 提取事件内容文本
        texts = [str(e.content)[:200] for e in events if e.content]

        # 检查冗余对
        redundant_pairs = 0
        duplicates = []

        for i in range(len(texts)):
            for j in range(i + 1, min(i + 50, len(texts))):
                if self._compressor.is_redundant(texts[i], [texts[j]], threshold):
                    redundant_pairs += 1
                    if len(duplicates) < 5:
                        duplicates.append({
                            "event_a": texts[i][:80],
                            "event_b": texts[j][:80],
                            "index_diff": j - i
                        })

        total_possible = len(texts) * min(49, len(texts) - 1) // 2
        ratio = redundant_pairs / max(total_possible, 1)

        recommendation = "正常"
        if ratio > REDUNDANCY_HIGH_THRESHOLD:
            recommendation = "高冗余 — 建议提高压缩级别或清理旧事件"
        elif ratio > REDUNDANCY_MODERATE_THRESHOLD:
            recommendation = "中度冗余 — 可考虑增量去重"

        result = {
            "redundant_pairs": redundant_pairs,
            "redundancy_ratio": round(ratio, 4),
            "top_duplicates": duplicates,
            "recommendation": recommendation,
            "checked_at": time.time()
        }

        if ratio > REDUNDANCY_HIGH_THRESHOLD:
            self._add_warning("high_redundancy", result)

        return result

    # ========================================================================
    # 内存检查
    # ========================================================================

    def check_memory(self, pool: GlobalContextPool) -> Dict[str, Any]:
        """
        检查池内存使用

        Returns:
            {
                "files_count": int,
                "files_estimated_bytes": int,
                "events_count": int,
                "events_estimated_bytes": int,
                "total_estimated_mb": float,
                "sessions_count": int,
                "warning": str or None
            }
        """
        files_count = pool.file_count()
        events_count = pool.event_count()

        # 估算内存（仅计算实际内容大小，不含对象开销）
        all_files = pool.get_all_files()
        files_bytes = sum(
            len(f.content.encode("utf-8", errors="replace"))
            for f in all_files.values()
        ) if all_files else 0

        all_events = pool.get_all_events()
        events_bytes = sum(
            len(str(e.content).encode("utf-8", errors="replace"))
            for e in all_events
        ) if all_events else 0

        # 会话计数通过 stats 获取
        stats = pool.get_stats()
        sessions_count = stats.get("sessions", 0)

        total_mb = (files_bytes + events_bytes) / (1024 * 1024)

        warning = None
        if total_mb > MEMORY_WARNING_MB:
            warning = f"内存使用超过 {MEMORY_WARNING_MB}MB，建议清理"
            self._add_warning("high_memory", {"total_mb": round(total_mb, 2)})
        elif events_count > EVENT_COUNT_WARNING:
            warning = "事件数接近上限，旧事件将被裁剪"
        elif files_count > FILE_COUNT_WARNING:
            warning = "文件缓存过大，建议限制"

        return {
            "files_count": files_count,
            "files_estimated_bytes": files_bytes,
            "events_count": events_count,
            "events_estimated_bytes": events_bytes,
            "total_estimated_mb": round(total_mb, 2),
            "sessions_count": sessions_count,
            "warning": warning,
            "checked_at": time.time()
        }

    # ========================================================================
    # 一致性检查
    # ========================================================================

    def check_consistency(self, pool: GlobalContextPool) -> Dict[str, Any]:
        """
        检查池数据一致性

        Returns:
            {
                "is_consistent": bool,
                "issues": [...],
                "checked_at": float
            }
        """
        issues = []

        # 检查文件事件引用的文件是否在缓存中
        file_events = pool.get_events(event_type=EventType.FILE_CHANGE, limit=200)
        referenced_paths = set()
        for e in file_events:
            if isinstance(e.metadata, dict):
                fp = e.metadata.get("filepath", "")
                if fp:
                    referenced_paths.add(fp)

        for fp in referenced_paths:
            if pool.get_file(fp) is None:
                issues.append(f"事件引用了不在缓存中的文件: {fp}")

        # 检查事件时间戳是否单调递减（事件按倒序存储：新事件在前）
        # 正常情况：events[i].timestamp >= events[i+1].timestamp（i 更新 >= i+1 更旧）
        events = pool.get_events(limit=100)
        for i in range(len(events) - 1):
            # 异常条件：当前事件时间戳小于下一个事件，违反了倒序
            if events[i].timestamp < events[i + 1].timestamp:
                issues.append(
                    f"事件时间戳顺序异常: idx={i} ts={events[i].timestamp} < idx={i+1} ts={events[i+1].timestamp} "
                    f"(期望倒序递减)"
                )
                break  # 只报告第一个

        is_consistent = len(issues) == 0

        if not is_consistent:
            self._add_warning("consistency", {"issues": issues})

        return {
            "is_consistent": is_consistent,
            "issues": issues,
            "checked_at": time.time()
        }

    # ========================================================================
    # 统计视图
    # ========================================================================

    def get_stats(self, pool: GlobalContextPool) -> Dict[str, Any]:
        """
        获取综合健康统计

        合并 pool.get_stats() + check_redundancy() + check_memory()
        """
        pool_stats = pool.get_stats()

        # 只在超过检查间隔时重新计算
        now = time.time()
        if now - self._last_check_time > self._check_interval:
            redundancy = self.check_redundancy(pool)
            memory = self.check_memory(pool)
            consistency = self.check_consistency(pool)
            self._last_check_time = now
        else:
            redundancy = {"redundancy_ratio": 0, "recommendation": "skip"}
            memory = {"total_estimated_mb": 0, "warning": None}
            consistency = {"is_consistent": True, "issues": []}

        # 事件类型分布
        all_events = pool.get_all_events()
        type_distribution = Counter()
        for e in all_events:
            type_distribution[e.event_type.value if hasattr(e.event_type, 'value') else str(e.event_type)] += 1

        # 源角色分布
        role_distribution = Counter()
        for e in all_events:
            role_distribution[e.source_role] += 1

        return {
            **pool_stats,
            "redundancy": redundancy,
            "memory": memory,
            "consistency": consistency,
            "event_type_distribution": dict(type_distribution.most_common(10)),
            "source_role_distribution": dict(role_distribution.most_common(10)),
            "warnings_count": len(self._warnings),
            "recent_warnings": self._warnings[-5:],
        }

    def get_warnings(self, limit: int = 20) -> List[Dict[str, Any]]:
        """获取最近的警告"""
        return self._warnings[-limit:]

    def clear_warnings(self) -> None:
        """清除所有警告"""
        self._warnings.clear()

    # ========================================================================
    # 内部
    # ========================================================================

    def _add_warning(self, warn_type: str, detail: Dict[str, Any]) -> None:
        """记录警告"""
        warning = {
            "type": warn_type,
            "timestamp": time.time(),
            "detail": detail
        }
        self._warnings.append(warning)
        if len(self._warnings) > self._max_warnings:
            self._warnings = self._warnings[-self._max_warnings:]
        logger.warning("审计警告: %s — %s", warn_type, detail.get("recommendation", str(detail)))


# 模块级便捷访问
auditor = Auditor()
