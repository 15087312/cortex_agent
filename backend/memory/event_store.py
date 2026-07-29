"""
EventStore — memory event persistence (SQLite + FAISS).
Ported from reference: removed owner_id field.
"""
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("event_store")


@dataclass
class MemoryEvent:
    """Memory event — smallest retrieval unit."""
    id: str = ""
    fact: str = ""
    thought: str = ""
    lesson: str = ""
    keywords: List[str] = field(default_factory=list)
    importance: float = 0.5
    time: str = ""
    session_id: str = ""
    embedding: Optional[List[float]] = None

    type: str = "fact"
    last_accessed: str = ""
    access_count: int = 0
    mention_count: int = 1

    causal_node_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("embedding", None)
        d["keywords"] = json.dumps(self.keywords, ensure_ascii=False)
        d["causal_node_ids"] = json.dumps(self.causal_node_ids, ensure_ascii=False)
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
            mention_count=row.get("mention_count", 1),
            causal_node_ids=json.loads(row.get("causal_node_ids", "[]")),
        )


class EventStore:
    """Event store — SQLite + FAISS dual engine."""

    _instance: "EventStore" = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = None, faiss_index_path: str = None, id_map_path: str = None):
        self._write_lock = threading.Lock()
        self._pending_embeddings: List[str] = []
        self._embedding_worker_started = False
        db_path = db_path or settings.MEMORY_DB_PATH
        faiss_index_path = faiss_index_path or settings.MEMORY_FAISS_INDEX
        id_map_path = id_map_path or settings.MEMORY_ID_MAP

        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        os.makedirs(os.path.dirname(faiss_index_path) or ".", exist_ok=True)

        self._db_path = db_path
        self._faiss_index_path = faiss_index_path
        self._id_map_path = id_map_path
        self._conn: Optional[sqlite3.Connection] = None
        self._faiss_index = None
        self._id_map: List[str] = []
        self._embedding_dim = None

    @classmethod
    def get_instance(cls, **kwargs) -> "EventStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_db()
        else:
            if not os.path.exists(self._db_path):
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
                return self._get_conn()
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
                mention_count INTEGER DEFAULT 1,
                causal_node_ids TEXT DEFAULT '[]',
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
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "type" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN type TEXT DEFAULT 'fact'")
        if "last_accessed" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN last_accessed TEXT DEFAULT ''")
        if "access_count" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN access_count INTEGER DEFAULT 0")
        if "mention_count" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN mention_count INTEGER DEFAULT 1")
        if "causal_node_ids" not in existing:
            conn.execute("ALTER TABLE events ADD COLUMN causal_node_ids TEXT DEFAULT '[]'")
        conn.commit()

    def save_event(self, event: MemoryEvent) -> str:
        with self._write_lock:
            return self._save_event_inner(event)

    def _save_event_inner(self, event: MemoryEvent) -> str:
        if not event.id:
            event.id = uuid.uuid4().hex[:12]
        if not event.time:
            event.time = datetime.now(timezone.utc).isoformat()
        if not event.last_accessed:
            event.last_accessed = event.time

        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO events
               (id, fact, thought, lesson, keywords, importance, time, session_id,
                type, last_accessed, access_count, mention_count, causal_node_ids)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.id, event.fact, event.thought, event.lesson,
                json.dumps(event.keywords, ensure_ascii=False),
                event.importance, event.time, event.session_id,
                event.type, event.last_accessed, event.access_count,
                event.mention_count,
                json.dumps(event.causal_node_ids, ensure_ascii=False),
            ),
        )
        conn.commit()

        if event.embedding is None:
            try:
                from backend.memory.embedding import EmbeddingEngine
                eng = EmbeddingEngine.get_instance()
                if eng._loaded:
                    text = f"{event.fact} {event.thought} {event.lesson} {' '.join(event.keywords)}".strip()
                    if text:
                        vec = eng.embed(text)
                        if vec:
                            self._add_embedding_inner(event.id, vec)
                            event.embedding = vec
                            return event.id
                elif not eng._attempted:
                    self._pending_embeddings.append(event.id)
                    self._start_embedding_worker()
            except Exception as e:
                logger.debug(f"Auto-vectorization failed (non-fatal): {e}")

        return event.id

    def _start_embedding_worker(self):
        if self._embedding_worker_started or not self._pending_embeddings:
            return
        self._embedding_worker_started = True

        def _worker():
            try:
                import time
                time.sleep(2)
                from backend.memory.embedding import EmbeddingEngine
                eng = EmbeddingEngine.get_instance()
                if not (eng._loaded or eng._attempted):
                    self._embedding_worker_started = False
                    return
                pending = list(self._pending_embeddings)
                self._pending_embeddings.clear()
                for eid in pending:
                    ev = self.get_event(eid)
                    if ev and ev.embedding is None:
                        text = f"{ev.fact} {ev.thought} {ev.lesson} {' '.join(ev.keywords)}".strip()
                        if text:
                            vec = eng.embed(text)
                            if vec:
                                self.add_embedding(eid, vec)
            except Exception as e:
                logger.debug(f"Background vectorization failed: {e}")
            finally:
                self._embedding_worker_started = False

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def touch_event(self, event_id: str) -> bool:
        conn = self._get_conn()
        now = datetime.now(timezone.utc).isoformat()
        cur = conn.execute(
            "UPDATE events SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
            (now, event_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def increment_mention(self, event_id: str) -> bool:
        conn = self._get_conn()
        cur = conn.execute(
            "UPDATE events SET mention_count = mention_count + 1 WHERE id = ?",
            (event_id,),
        )
        conn.commit()
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

    # ── FAISS ──

    def _get_embedding_dim(self) -> int:
        if self._embedding_dim is None:
            from backend.memory.embedding import EmbeddingEngine
            eng = EmbeddingEngine.get_instance()
            if not eng._load_model():
                raise RuntimeError("Embedding model failed to load")
            self._embedding_dim = eng.dim
        return self._embedding_dim

    def _load_faiss(self):
        if self._faiss_index is not None:
            return
        try:
            import faiss
            dim = self._get_embedding_dim()
            if os.path.exists(self._faiss_index_path):
                self._faiss_index = faiss.read_index(self._faiss_index_path)
            else:
                self._faiss_index = faiss.IndexFlatIP(dim)
            if os.path.exists(self._id_map_path):
                with open(self._id_map_path, "r") as f:
                    self._id_map = json.load(f)
        except ImportError:
            logger.warning("faiss not installed, vector search unavailable")
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
            logger.warning(f"FAISS save failed: {e}")

    def add_embedding(self, event_id: str, embedding: List[float]):
        with self._write_lock:
            self._add_embedding_inner(event_id, embedding)

    def _add_embedding_inner(self, event_id: str, embedding: List[float]):
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
            logger.warning(f"Add embedding failed: {e}")

    def search_by_vector(self, query_embedding: List[float], top_k: int = 10) -> List[tuple]:
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
                if 0 <= idx < len(self._id_map):
                    results.append((self._id_map[idx], float(score)))
            return results
        except Exception as e:
            logger.warning(f"Vector search failed: {e}")
            return []

    def search_by_keywords(self, keywords: List[str], limit: int = 20) -> List[MemoryEvent]:
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

    def clear_all(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM events")
        conn.commit()
        self._faiss_index = None
        self._id_map = []
        if os.path.exists(self._faiss_index_path):
            os.remove(self._faiss_index_path)
        if os.path.exists(self._id_map_path):
            os.remove(self._id_map_path)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
        self._save_faiss()

    def __del__(self):
        self.close()
