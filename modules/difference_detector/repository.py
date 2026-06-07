"""
差异仓储层 — SQLite 持久化

完全复用 ShortTermMemoryRepository 模式：
- TIER_TTL 字典 (5min → 7day)
- save() / get_active() / dissolve_expired()
- 使用全局 db_manager 单例 + WAL 模式
"""
import time
from typing import List, Optional
from datetime import datetime

from modules.database.connection import db_manager, Base
from modules.difference_detector.models import Difference, DifferenceRecord
from utils.logger import setup_logger

logger = setup_logger("difference_repository")


class DifferenceRepository:
    """差异仓储 — SQLite 持久化"""

    TIER_TTL = {
        "5min": 5 * 60,
        "30min": 30 * 60,
        "1hour": 60 * 60,
        "3hours": 3 * 60 * 60,
        "12hours": 12 * 60 * 60,
        "24hours": 24 * 60 * 60,
        "3days": 3 * 24 * 60 * 60,
        "7days": 7 * 24 * 60 * 60,
    }

    def __init__(self):
        self._ensure_initialized()

    def _ensure_initialized(self):
        try:
            db_manager.initialize()
            # 确保 differences 表已创建 (该模型在 db_manager 初始化后才导入)
            if db_manager._engine is not None:
                Base.metadata.create_all(
                    db_manager._engine,
                    tables=[DifferenceRecord.__table__]
                )
        except Exception as e:
            logger.warning(f"数据库初始化失败 (差异仓储): {e}")

    def _get_tier(self, created_at: float) -> str:
        age = time.time() - created_at
        if age <= 5 * 60:
            return "5min"
        elif age <= 30 * 60:
            return "30min"
        elif age <= 60 * 60:
            return "1hour"
        elif age <= 3 * 60 * 60:
            return "3hours"
        elif age <= 12 * 60 * 60:
            return "12hours"
        elif age <= 24 * 60 * 60:
            return "24hours"
        elif age <= 3 * 24 * 60 * 60:
            return "3days"
        else:
            return "7days"

    def save(self, diff: Difference) -> str:
        """保存或更新差异"""
        ttl = diff.ttl or self.TIER_TTL.get(self._get_tier(diff.created_at), 3600)
        expires_at = diff.created_at + ttl

        with db_manager.get_session() as session:
            existing = session.query(DifferenceRecord).filter(
                DifferenceRecord.id == diff.id
            ).first()

            if existing:
                existing.intensity = diff.intensity
                existing.status = diff.status
                existing.ttl = ttl
                existing.expires_at = expires_at
                existing.payload = diff.payload
                existing.related_ids = diff.related_ids
                existing.category = diff.category
            else:
                record = DifferenceRecord(
                    id=diff.id,
                    source_type=diff.source_type,
                    category=diff.category,
                    intensity=diff.intensity,
                    created_at=diff.created_at,
                    ttl=ttl,
                    expires_at=expires_at,
                    payload=diff.payload,
                    related_ids=diff.related_ids,
                    status=diff.status,
                )
                session.add(record)

        return diff.id

    def get_active(
        self,
        source_type: str = None,
        min_intensity: float = 0.0,
        limit: int = 50,
    ) -> List[dict]:
        """获取活跃差异"""
        with db_manager.get_session() as session:
            q = session.query(DifferenceRecord).filter(
                DifferenceRecord.status.in_(["active", "incubating"])
            )

            if source_type:
                q = q.filter(DifferenceRecord.source_type == source_type)

            if min_intensity > 0:
                q = q.filter(DifferenceRecord.intensity >= min_intensity)

            q = q.order_by(DifferenceRecord.intensity.desc()).limit(limit)
            return [r.to_dict() for r in q.all()]

    def get_by_id(self, diff_id: str) -> Optional[dict]:
        """获取单条差异"""
        with db_manager.get_session() as session:
            record = session.query(DifferenceRecord).filter(
                DifferenceRecord.id == diff_id
            ).first()
            if record:
                return record.to_dict()
        return None

    def get_history(self, limit: int = 100) -> List[dict]:
        """获取历史差异（含已溶解）"""
        with db_manager.get_session() as session:
            q = session.query(DifferenceRecord).order_by(
                DifferenceRecord.created_at.desc()
            ).limit(limit)
            return [r.to_dict() for r in q.all()]

    def dissolve_expired(self) -> int:
        """溶解过期差异 — 将状态改为 dissolved"""
        dissolved = 0
        now = time.time()

        with db_manager.get_session() as session:
            expired = session.query(DifferenceRecord).filter(
                DifferenceRecord.expires_at != None,
                DifferenceRecord.expires_at <= now,
                DifferenceRecord.status.in_(["active", "incubating"]),
            ).all()

            for record in expired:
                record.status = "dissolved"
                dissolved += 1

        if dissolved:
            logger.debug(f"溶解 {dissolved} 条过期差异")
        return dissolved

    def dissolve_by_id(self, diff_id: str) -> bool:
        """手动溶解指定差异"""
        with db_manager.get_session() as session:
            record = session.query(DifferenceRecord).filter(
                DifferenceRecord.id == diff_id
            ).first()
            if record:
                record.status = "dissolved"
                return True
        return False

    def get_stats(self) -> dict:
        """获取存储统计"""
        with db_manager.get_session() as session:
            total = session.query(DifferenceRecord).count()
            active = session.query(DifferenceRecord).filter(
                DifferenceRecord.status == "active"
            ).count()
            incubating = session.query(DifferenceRecord).filter(
                DifferenceRecord.status == "incubating"
            ).count()
            dissolved = session.query(DifferenceRecord).filter(
                DifferenceRecord.status == "dissolved"
            ).count()

        return {
            "total": total,
            "active": active,
            "incubating": incubating,
            "dissolved": dissolved,
        }
