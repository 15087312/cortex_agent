"""差异仓库 — 内存持久化，支持增删查和过期溶解

当前使用内存列表存储，未来可替换为数据库。
"""
import time
from typing import List, Optional

from modules.perception.difference.models import Difference
from utils.logger import setup_logger

logger = setup_logger("difference_repository")


class DifferenceRepository:
    """差异仓库（内存实现）

    提供差异的保存、查询（活跃/历史）、过期溶解和统计功能。
    """

    def __init__(self):
        self._differences: List[Difference] = []

    def save(self, diff: Difference) -> str:
        """保存差异（存在则更新，否则新增）

        id 相同视为同一差异，更新强度/状态/TTL/载荷。
        """
        existing = self.get_by_id(diff.id)
        if existing:
            d = next(d for d in self._differences if d.id == diff.id)
            d.intensity = diff.intensity
            d.status = diff.status
            d.ttl = diff.ttl
            d.payload = diff.payload
            d.related_ids = diff.related_ids
            d.category = diff.category
        else:
            self._differences.append(diff)
        return diff.id

    def get_by_id(self, diff_id: str) -> Optional[dict]:
        """根据 ID 查询差异"""

        for d in self._differences:
            if d.id == diff_id:
                return d.to_dict()
        return None

    def get_active(
        self,
        source_type: str = None,
        min_intensity: float = 0.0,
        limit: int = 50,
    ) -> List[dict]:
        """获取活跃差异（状态 active/incubating 且未过期）

        支持按来源类型和最低强度过滤，按强度降序排列。
        """
        now = time.time()
        result = []
        for d in self._differences:
            if d.status not in ("active", "incubating"):
                continue
            if d.created_at + d.ttl <= now:
                continue
            if source_type and d.source_type != source_type:
                continue
            if d.intensity < min_intensity:
                continue
            result.append(d.to_dict())
        result.sort(key=lambda x: x["intensity"], reverse=True)
        return result[:limit]

    def get_history(self, limit: int = 100) -> List[dict]:
        """获取历史记录（按创建时间降序）"""

        sorted_diffs = sorted(self._differences, key=lambda d: d.created_at, reverse=True)
        return [d.to_dict() for d in sorted_diffs[:limit]]

    def dissolve_expired(self) -> int:
        """将所有已过期的活跃差异标记为 dissolved 状态"""

        dissolved = 0
        now = time.time()
        for d in self._differences:
            if d.status in ("active", "incubating") and d.created_at + d.ttl <= now:
                d.status = "dissolved"
                dissolved += 1
        if dissolved:
            logger.debug(f"溶解 {dissolved} 条过期差异")
        return dissolved

    def dissolve_by_id(self, diff_id: str) -> bool:
        """根据 ID 手动溶解一个差异"""

        for d in self._differences:
            if d.id == diff_id:
                d.status = "dissolved"
                return True
        return False

    def get_stats(self) -> dict:
        """获取各状态的数量统计"""

        total = len(self._differences)
        active = sum(1 for d in self._differences if d.status == "active")
        incubating = sum(1 for d in self._differences if d.status == "incubating")
        dissolved = sum(1 for d in self._differences if d.status == "dissolved")
        return {
            "total": total,
            "active": active,
            "incubating": incubating,
            "dissolved": dissolved,
        }
