"""
EventStore — 事件记忆的持久化存储

存储:
- SQLite: events 表 (fact/thought/lesson/keywords/importance/time)
- FAISS 索引: data/events_faiss.index (向量相似搜索)
- ID 映射: data/events_id_map.json (FAISS position → event_id)
"""
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("event_store")


@dataclass
class MemoryEvent:
    """记忆事件 — 检索的最小单位

    type: emotion|thought|fact|strategy — 决定衰减速率
    last_accessed: 上次被成功召回的 ISO 时间戳（用于 recency_decay）
    access_count: 被成功召回的次数（用于 reinforcement）
    """
    id: str = ""
    fact: str = ""          # 发生了什么
    thought: str = ""       # 思考/反思
    lesson: str = ""        # 学到了什么（可复用经验）
    keywords: List[str] = field(default_factory=list)  # 关键词
    importance: float = 0.5  # 重要性 0.0-1.0
    time: str = ""          # ISO 时间戳（创建时间）
    session_id: str = ""    # 关联会话
    embedding: Optional[List[float]] = None  # 向量（不持久化到 SQLite）

    # ── 遗忘曲线 & 强化 ──
    type: str = "fact"           # emotion | thought | fact | strategy
    last_accessed: str = ""      # ISO 时间戳，初始等于 time
    access_count: int = 0        # 被成功检索的次数

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("embedding", None)
        d["keywords"] = json.dumps(self.keywords, ensure_ascii=False)
        return d

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "MemoryEvent":
        return cls(
            id=row["id"],
            fact=row["fact"],
            thought=row.get("thought", ""),
            lesson=row.get("lesson", ""),
            keywords=json.loads(row.get("keywords", "[]")),
            importance=row.get("importance", 0.5),
            time=row.get("time", ""),
            session_id=row.get("session_id", ""),
            type=row.get("type", "fact"),
            last_accessed=row.get("last_accessed", row.get("time", "")),
            access_count=row.get("access_count", 0),
        )


class EventStore:
    """事件存储器 — SQLite + FAISS 双引擎"""

    _instance: "EventStore" = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = None, faiss_index_path: str = None, id_map_path: str = None):
        db_path = db_path or getattr(settings, "MEMORY_DB_PATH", "data/memory.db")
        faiss_index_path = faiss_index_path or getattr(settings, "MEMORY_FAISS_INDEX", "data/events_faiss.index")
        id_map_path = id_map_path or getattr(settings, "MEMORY_ID_MAP", "data/events_id_map.json")

        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        os.makedirs(os.path.dirname(faiss_index_path), exist_ok=True)

        self._db_path = db_path
        self._faiss_index_path = faiss_index_path
        self._id_map_path = id_map_path
        self._conn: Optional[sqlite3.Connection] = None
        self._faiss_index = None  # 延迟加载
        self._id_map: List[str] = []  # FAISS position → event_id
        self._embedding_dim = None  # 初始化时为 None，实际维度由 EmbeddingEngine 决定
        self.logger = logger

    # ------------------------------------------------------------------
    # 单例
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, **kwargs) -> "EventStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    # ------------------------------------------------------------------
    # 数据库初始化
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_db()
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                fact TEXT NOT NULL,
                thought TEXT DEFAULT '',
                lesson TEXT DEFAULT '',
                keywords TEXT DEFAULT '[]',
                importance REAL DEFAULT 0.5,
                time TEXT NOT NULL,
                session_id TEXT DEFAULT '',
                type TEXT DEFAULT 'fact',
                last_accessed TEXT DEFAULT '',
                access_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_importance ON events(importance DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
        conn.commit()
        self._migrate_schema(conn)

    def _migrate_schema(self, conn: sqlite3.Connection):
        """增量迁移旧表，添加新字段"""
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "type" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN type TEXT DEFAULT 'fact'")
        if "last_accessed" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN last_accessed TEXT DEFAULT ''")
        if "access_count" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN access_count INTEGER DEFAULT 0")
        conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def save_event(self, event: MemoryEvent) -> str:
        """保存事件到 SQLite"""
        if not event.id:
            event.id = uuid.uuid4().hex[:12]
        if not event.time:
            now = datetime.now(timezone.utc)
            event.time = now.isoformat()
        if not event.last_accessed:
            event.last_accessed = event.time

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO events
               (id, fact, thought, lesson, keywords, importance, time, session_id,
                type, last_accessed, access_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id,
                event.fact,
                event.thought,
                event.lesson,
                json.dumps(event.keywords, ensure_ascii=False),
                event.importance,
                event.time,
                event.session_id,
                event.type,
                event.last_accessed,
                event.access_count,
            ),
        )
        conn.commit()
        self.logger.debug(f"[EventStore] 保存事件 {event.id} (type={event.type}, imp={event.importance})")
        return event.id

    def touch_event(self, event_id: str) -> bool:
        """标记事件被成功召回——更新 last_accessed 和递增 access_count"""
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE events SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (now, event_id),
        )
        conn.commit()
        if cur.rowcount:
            self.logger.debug(f"[EventStore] touch {event_id} at {now}")
        return cur.rowcount > 0

    def get_event(self, event_id: str) -> Optional[MemoryEvent]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        if row is None:
            return None
        return MemoryEvent.from_dict(dict(row))

    def delete_event(self, event_id: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
        conn.commit()
        return cur.rowcount > 0

    def list_events(self, limit: int = 50, offset: int = 0) -> List[MemoryEvent]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM events ORDER BY time DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [MemoryEvent.from_dict(dict(r)) for r in rows]

    def count_events(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # FAISS 索引管理
    # ------------------------------------------------------------------

    def _get_embedding_dim(self) -> int:
        """获取实际 embedding 维度"""
        if self._embedding_dim is None:
            try:
                from modules.memory.embedding import EmbeddingEngine
                eng = EmbeddingEngine.get_instance()
                # 触发模型加载以获取维度
                if eng._load_model():
                    self._embedding_dim = eng.dim
                else:
                    self._embedding_dim = 384  # 降级默认值
            except Exception:
                self._embedding_dim = 384
        return self._embedding_dim

    def _load_faiss(self):
        """延迟加载 FAISS 索引"""
        if self._faiss_index is not None:
            return
        try:
            import faiss
            import numpy as np
            dim = self._get_embedding_dim()
            if os.path.exists(self._faiss_index_path):
                self._faiss_index = faiss.read_index(self._faiss_index_path)
                self.logger.info(f"[EventStore] 加载 FAISS 索引: {self._faiss_index_path} ({self._faiss_index.ntotal} 向量, dim={self._faiss_index.d})")
            else:
                self._faiss_index = faiss.IndexFlatIP(dim)
                self.logger.info(f"[EventStore] 创建新 FAISS 索引 (dim={dim})")

            if os.path.exists(self._id_map_path):
                with open(self._id_map_path, "r") as f:
                    self._id_map = json.load(f)
                assert len(self._id_map) == self._faiss_index.ntotal, \
                    f"ID 映射长度 ({len(self._id_map)}) 与 FAISS 向量数 ({self._faiss_index.ntotal}) 不匹配"
        except ImportError:
            self.logger.warning("[EventStore] faiss 未安装，向量检索不可用")
            self._faiss_index = None

    def _save_faiss(self):
        if self._faiss_index is None:
            return
        try:
            import faiss
            faiss.write_index(self._faiss_index, self._faiss_index_path)
            with open(self._id_map_path, "w") as f:
                json.dump(self._id_map, f)
        except Exception as e:
            self.logger.warning(f"[EventStore] 保存 FAISS 索引失败: {e}")

    def add_embedding(self, event_id: str, embedding: List[float]):
        """向 FAISS 添加向量"""
        try:
            import numpy as np
            import faiss
            self._load_faiss()
            if self._faiss_index is None:
                return
            vec = np.array([embedding], dtype=np.float32)
            faiss.normalize_L2(vec)
            self._faiss_index.add(vec)
            self._id_map.append(event_id)
            self._save_faiss()
        except Exception as e:
            self.logger.warning(f"[EventStore] 添加向量失败: {e}")

    def remove_embedding(self, event_id: str):
        """从 FAISS 移除向量（重建索引，低频操作）"""
        try:
            import numpy as np
            import faiss
            self._load_faiss()
            if self._faiss_index is None or event_id not in self._id_map:
                return
            dim = self._get_embedding_dim()
            idx = self._id_map.index(event_id)
            # FAISS 不支持删除，重建索引跳过该向量
            vectors = []
            new_id_map = []
            old_index = self._faiss_index
            for i in range(old_index.ntotal):
                if i == idx:
                    continue
                vec = np.zeros((1, dim), dtype=np.float32)
                old_index.reconstruct(i, vec[0])
                vectors.append(vec[0])
                new_id_map.append(self._id_map[i])
            if vectors:
                new_index = faiss.IndexFlatIP(dim)
                stacked = np.array(vectors, dtype=np.float32)
                faiss.normalize_L2(stacked)
                new_index.add(stacked)
                self._faiss_index = new_index
            else:
                self._faiss_index = faiss.IndexFlatIP(self._embedding_dim)
            self._id_map = new_id_map
            self._save_faiss()
        except Exception as e:
            self.logger.warning(f"[EventStore] 移除向量失败: {e}")

    def search_by_vector(self, query_embedding: List[float], top_k: int = 10) -> List[tuple]:
        """向量搜索，返回 [(event_id, score), ...]"""
        self._load_faiss()
        if self._faiss_index is None or self._faiss_index.ntotal == 0:
            return []
        try:
            import numpy as np
            import faiss
            vec = np.array([query_embedding], dtype=np.float32)
            faiss.normalize_L2(vec)
            scores, indices = self._faiss_index.search(vec, min(top_k, self._faiss_index.ntotal))
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= 0 and idx < len(self._id_map):
                    results.append((self._id_map[idx], float(score)))
            return results
        except Exception as e:
            self.logger.warning(f"[EventStore] 向量搜索失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 关键词检索（SQLite json_each）
    # ------------------------------------------------------------------

    def search_by_keywords(self, keywords: List[str], limit: int = 20) -> List[MemoryEvent]:
        """精确关键词匹配"""
        if not keywords:
            return []
        conn = self._get_conn()
        placeholders = ",".join("?" for _ in keywords)
        rows = conn.execute(
            f"SELECT DISTINCT e.* FROM events e "
            f"WHERE EXISTS (SELECT 1 FROM json_each(e.keywords) AS je "
            f"               WHERE LOWER(je.value) IN ({placeholders})) "
            f"ORDER BY e.importance DESC, e.time DESC LIMIT ?",
            [k.lower() for k in keywords] + [limit],
        ).fetchall()
        return [MemoryEvent.from_dict(dict(r)) for r in rows]

    def search_by_importance(self, min_importance: float = 0.7, limit: int = 20) -> List[MemoryEvent]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM events WHERE importance >= ? ORDER BY importance DESC, time DESC LIMIT ?",
            (min_importance, limit),
        ).fetchall()
        return [MemoryEvent.from_dict(dict(r)) for r in rows]

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def clear_all(self):
        """清空所有数据（测试用）"""
        conn = self._get_conn()
        conn.execute("DELETE FROM events")
        conn.commit()
        self._faiss_index = None
        self._id_map = []
        if os.path.exists(self._faiss_index_path):
            os.remove(self._faiss_index_path)
        if os.path.exists(self._id_map_path):
            os.remove(self._id_map_path)
        self.logger.info("[EventStore] 已清空所有事件")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
        self._save_faiss()

    def __del__(self):
        self.close()
