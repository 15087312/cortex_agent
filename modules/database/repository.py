"""
记忆仓储层
封装数据库和缓存操作
"""
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from utils.logger import setup_logger
from datetime import datetime
from .connection import db_manager, Base
from .models import ShortTermMemory, LongTermMemory, ExperienceMemory
from .disk_cache import disk_cache

logger = setup_logger("memory_repository")


@dataclass
class MemoryQuery:
    """记忆查询条件"""
    keywords: List[str] = None
    memory_type: str = None
    region: str = None
    owner: str = None
    session_id: str = None
    min_importance: float = None
    max_age_seconds: int = None
    limit: int = 20


class ShortTermMemoryRepository:
    """短期记忆仓储"""
    
    TIER_TTL = {
        "5min": 5 * 60,
        "30min": 30 * 60,
        "1hour": 60 * 60,
        "3hours": 3 * 60 * 60,
        "12hours": 12 * 60 * 60,
        "24hours": 24 * 60 * 60,
        "3days": 3 * 24 * 60 * 60,
        "7days": 7 * 24 * 60 * 60
    }
    
    def __init__(self):
        self.cache = disk_cache
        self._ensure_initialized()
    
    def _ensure_initialized(self):
        """确保数据库已初始化"""
        try:
            db_manager.initialize()
        except Exception as e:
            logger.warning(f"数据库初始化失败: {e}")
    
    def _get_tier(self, created_at: float) -> str:
        """根据创建时间确定层级"""
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
    
    def add(
        self,
        content: str,
        memory_type: str = "dialog",
        importance: float = 0.5,
        emotion: str = "",
        emotion_intensity: float = 0.0,
        source: str = "system",
        owner: str = "system",
        session_id: str = "",
        tags: List[str] = None,
        metadata: Dict = None
    ) -> str:
        """添加短期记忆"""
        import uuid

        memory_id = f"stm_{uuid.uuid4().hex[:12]}"
        now = time.time()
        tier = self._get_tier(now)
        ttl = self.TIER_TTL.get(tier, 3600)
        expires_at = now + ttl

        now_dt = datetime.fromtimestamp(now)
        expires_dt = datetime.fromtimestamp(expires_at)

        with db_manager.get_session() as session:
            memory = ShortTermMemory(
                id=memory_id,
                content=content,
                memory_type=memory_type,
                importance=importance,
                emotion=emotion,
                emotion_intensity=emotion_intensity,
                source=source,
                owner=owner,
                session_id=session_id,
                tier=tier,
                created_at=now_dt,
                expires_at=expires_dt,
                tags=tags or [],
                extra_data=metadata or {},
                is_active=True
            )
            session.add(memory)
        
        memory_data = {
            "id": memory_id,
            "content": content,
            "memory_type": memory_type,
            "importance": importance,
            "emotion": emotion,
            "tier": tier,
            "created_at": now_dt.isoformat(),
            "tags": tags or [],
            "extra_data": metadata or {},
            "is_active": True
        }
        
        self.cache.cache_short_memory(memory_id, memory_data, ttl=ttl)
        
        logger.debug(f"添加短期记忆: {memory_id}")
        return memory_id
    
    def get(self, memory_id: str) -> Optional[Dict]:
        """获取记忆"""
        cached = self.cache.get_short_memory(memory_id)
        if cached:
            return cached
        
        with db_manager.get_session() as session:
            memory = session.query(ShortTermMemory).filter(
                ShortTermMemory.id == memory_id,
                ShortTermMemory.is_active == True
            ).first()
            
            if memory:
                result = memory.to_dict()
                self.cache.cache_short_memory(memory_id, result)
                return result
        
        return None
    
    def query(self, query: MemoryQuery) -> List[Dict]:
        """查询记忆"""
        query_key = f"{query.keywords}:{query.memory_type}:{query.limit}"
        cached = self.cache.get_cached_query(query_key)
        if cached:
            return cached
        
        with db_manager.get_session() as session:
            q = session.query(ShortTermMemory).filter(
                ShortTermMemory.is_active == True
            )
            
            if query.memory_type:
                q = q.filter(ShortTermMemory.memory_type == query.memory_type)
            
            if query.owner:
                q = q.filter(ShortTermMemory.owner == query.owner)

            if query.session_id:
                q = q.filter(ShortTermMemory.session_id == query.session_id)

            if query.min_importance:
                q = q.filter(ShortTermMemory.importance >= query.min_importance)
            
            if query.keywords:
                for keyword in query.keywords:
                    q = q.filter(ShortTermMemory.content.like(f"%{keyword}%"))

            if query.max_age_seconds:
                from datetime import datetime, timedelta
                cutoff = datetime.utcnow() - timedelta(seconds=query.max_age_seconds)
                q = q.filter(ShortTermMemory.created_at >= cutoff)

            q = q.order_by(ShortTermMemory.created_at.desc())
            
            if query.limit:
                q = q.limit(query.limit)
            
            results = [m.to_dict() for m in q.all()]
            
            self.cache.cache_query_result(query_key, results, ttl=30)
            
            return results
    
    def get_recent(self, tier: str = "1hour", limit: int = 20) -> List[Dict]:
        """获取最近的记忆"""
        tier_order = ["5min", "30min", "1hour", "3hours", "12hours", "24hours", "3days", "7days"]
        
        if tier not in tier_order:
            tier = "1hour"
        
        tier_index = tier_order.index(tier)
        tiers_to_get = tier_order[:tier_index + 1]
        
        results = []
        with db_manager.get_session() as session:
            for t in tiers_to_get:
                memories = session.query(ShortTermMemory).filter(
                    ShortTermMemory.tier == t,
                    ShortTermMemory.is_active == True
                ).order_by(ShortTermMemory.created_at.desc()).limit(limit).all()
                
                results.extend([m.to_dict() for m in memories])
        
        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results[:limit]
    
    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        self.cache.delete(memory_id, prefix="short_term")
        
        with db_manager.get_session() as session:
            memory = session.query(ShortTermMemory).filter(
                ShortTermMemory.id == memory_id
            ).first()
            
            if memory:
                memory.is_active = False
                return True
        
        return False
    
    def cleanup_expired(self) -> int:
        """清理过期记忆"""
        from datetime import datetime as dt
        deleted = 0
        
        with db_manager.get_session() as session:
            expired = session.query(ShortTermMemory).filter(
                ShortTermMemory.expires_at != None,
                ShortTermMemory.is_active == True
            ).all()
            
            now = dt.utcnow()
            for memory in expired:
                if memory.expires_at and memory.expires_at < now:
                    memory.is_active = False
                    deleted += 1
        
        return deleted



class ExperienceRepository:
    """经验仓储"""
    
    def __init__(self):
        pass
    
    def add(
        self,
        situation: str,
        action: str,
        result: str,
        success: bool,
        context: Dict = None,
        tags: List[str] = None,
        reward: float = 0.0,
        owner: str = "system"
    ) -> str:
        """添加经验"""
        import uuid
        
        experience_id = f"exp_{uuid.uuid4().hex[:12]}"
        now = datetime.utcnow()
        
        experience = ExperienceMemory(
            id=experience_id,
            situation=situation,
            action=action,
            result=result,
            success=success,
            context=context or {},
            tags=tags or [],
            success_count=1 if success else 0,
            success_rate=1.0 if success else 0.0,
            avg_reward=reward,
            first_attempt=now,
            last_attempt=now
        )
        
        with db_manager.get_session() as session:
            session.add(experience)
        
        return experience_id
    
    def get_successful_actions(self, situation: str, limit: int = 5) -> List[Dict]:
        """获取成功动作"""
        with db_manager.get_session() as session:
            experiences = session.query(ExperienceMemory).filter(
                ExperienceMemory.situation == situation,
                ExperienceMemory.success == True
            ).order_by(ExperienceMemory.success_rate.desc()).limit(limit).all()
            
            return [e.to_dict() for e in experiences]
    
    def get_failed_actions(self, situation: str, limit: int = 5) -> List[Dict]:
        """获取失败动作"""
        with db_manager.get_session() as session:
            experiences = session.query(ExperienceMemory).filter(
                ExperienceMemory.situation == situation,
                ExperienceMemory.success == False
            ).order_by(ExperienceMemory.last_attempt.desc()).limit(limit).all()
            
            return [e.to_dict() for e in experiences]


short_term_repo = ShortTermMemoryRepository()
long_term_repo = None  # 长期记忆由 MemoryScheduler 独立管理 (JSONL+FAISS)
experience_repo = ExperienceRepository()
