"""KnowledgeStore — 知识库持久层（SQLite 原文 + FAISS 向量 + FTS5 关键词）

与 EventStore 完全独立：
- 独立库文件 data/knowledge.db、独立索引 knowledge_faiss.index / knowledge_id_map.json
- 不写 events 表、不进因果图、不参与遗忘曲线打分
- 索引用 IndexIDMap2(IndexFlatIP)，支持 remove_ids，删文档无需全量重建

本模块只碰存储，不做业务判断（切分在 chunker、摄取在 ingestor、排序在 retrieval）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from config.settings import settings
from utils.faiss_lock import faiss_file_lock
from utils.logger import setup_logger
from modules.knowledge.chunker import normalize_text
from modules.knowledge.models import KnowledgeChunk, KnowledgeDoc

logger = setup_logger("knowledge_store")

_open = open

_JIEBA = None
_JIEBA_FAILED = False
_STOP_TERMS = {
    "的", "了", "和", "与", "是", "在", "有", "也", "就", "都", "而", "及", "或",
    "一个", "这个", "那个", "我们", "你们", "他们", "the", "and", "for", "with",
    "that", "this", "from", "are", "was", "not", "but", "can", "you",
}


def tokenize_terms(text: str) -> List[str]:
    """jieba 预分词（中文靠它做关键词路；jieba 缺失时退化为正则切分）。"""
    global _JIEBA, _JIEBA_FAILED
    if not text:
        return []
    if _JIEBA is None and not _JIEBA_FAILED:
        try:
            import jieba
            jieba.setLogLevel(60)
            _JIEBA = jieba
        except Exception:
            _JIEBA_FAILED = True

    if _JIEBA is not None:
        raw = _JIEBA.lcut(text)
    else:
        raw = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{1,2}", text)

    seen: Set[str] = set()
    terms: List[str] = []
    for t in raw:
        t = t.strip().lower()
        if not t or t in _STOP_TERMS or t in seen:
            continue
        if len(t) < 2 and not t.isdigit():
            continue
        if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", t):
            continue
        seen.add(t)
        terms.append(t)
        if len(terms) >= 256:
            break
    return terms


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class KnowledgeStore:
    """知识库存储（单例；测试可直接构造独立实例）"""

    _instance: Optional["KnowledgeStore"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        db_path: Optional[str] = None,
        index_path: Optional[str] = None,
        id_map_path: Optional[str] = None,
        embed_fn=None,
    ):
        self._db_path = db_path or getattr(settings, "KNOWLEDGE_DB_PATH", "data/knowledge.db")
        self._index_path = index_path or getattr(
            settings, "KNOWLEDGE_FAISS_INDEX", "data/knowledge_faiss.index")
        self._id_map_path = id_map_path or getattr(
            settings, "KNOWLEDGE_ID_MAP", "data/knowledge_id_map.json")
        for p in (self._db_path, self._index_path, self._id_map_path):
            os.makedirs(os.path.dirname(os.path.abspath(p)) or ".", exist_ok=True)

        self._write_lock = threading.RLock()
        self._conn: Optional[sqlite3.Connection] = None
        self._embed_fn = embed_fn          # 测试注入；None 时用 EmbeddingEngine 单例
        self._index = None                 # faiss.IndexIDMap2，延迟加载
        self._id_map: Dict[str, str] = {}  # str(int_id) -> chunk_id
        self._next_id: int = 1
        self._fts: Optional[bool] = None   # FTS5 可用性，None=未探测

    # ------------------------------------------------------------------
    # 单例 / 连接
    # ------------------------------------------------------------------

    @classmethod
    def get_instance(cls, **kwargs) -> "KnowledgeStore":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """释放单例（切换配置 / 测试隔离用）"""
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.close()
            cls._instance = None

    def close(self) -> None:
        with self._write_lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception as e:
                    logger.debug(f"[knowledge] 关闭连接失败: {e}")
                self._conn = None
            self._index = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            # 级联删除必须显式开启；删除逻辑仍显式删 chunks（级联仅兜底）
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_db()
        return self._conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id            TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                source_path   TEXT NOT NULL UNIQUE,
                content_hash  TEXT NOT NULL,
                version       INTEGER NOT NULL DEFAULT 1,
                mime          TEXT DEFAULT 'text/markdown',
                size_bytes    INTEGER DEFAULT 0,
                chunk_count   INTEGER DEFAULT 0,
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL,
                indexed_at    TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS chunks (
                id            TEXT PRIMARY KEY,
                doc_id        TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                seq           INTEGER NOT NULL,
                heading_path  TEXT DEFAULT '',
                text          TEXT NOT NULL,
                char_start    INTEGER DEFAULT 0,
                char_end      INTEGER DEFAULT 0,
                token_count   INTEGER DEFAULT 0,
                terms         TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id, seq);
            CREATE INDEX IF NOT EXISTS idx_docs_hash ON documents(content_hash);
        """)
        conn.commit()
        # FTS5 可选：不可用时降级为 terms 交集打分（功能降级不报错）
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts "
                "USING fts5(chunk_id UNINDEXED, terms, tokenize='unicode61')"
            )
            conn.commit()
            self._fts = True
        except sqlite3.Error as e:
            logger.warning(f"[knowledge] FTS5 不可用，关键词路降级为词项交集: {e}")
            self._fts = False

    # ------------------------------------------------------------------
    # 文档 CRUD
    # ------------------------------------------------------------------

    def upsert_document(self, doc: KnowledgeDoc) -> str:
        """按 source_path 插入或更新文档元数据，返回 doc_id。"""
        with self._write_lock:
            conn = self._get_conn()
            now = _now()
            if not doc.id:
                doc.id = f"kd_{uuid.uuid4().hex[:12]}"
            existing = conn.execute(
                "SELECT id, created_at FROM documents WHERE source_path = ?",
                (doc.source_path,),
            ).fetchone()
            if existing:
                doc.id = existing["id"]
                doc.created_at = existing["created_at"] or now
            else:
                doc.created_at = doc.created_at or now
            doc.updated_at = doc.updated_at or now
            conn.execute(
                """INSERT INTO documents
                   (id, title, source_path, content_hash, version, mime, size_bytes,
                    chunk_count, created_at, updated_at, indexed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(source_path) DO UPDATE SET
                       title=excluded.title, content_hash=excluded.content_hash,
                       version=excluded.version, mime=excluded.mime,
                       size_bytes=excluded.size_bytes, chunk_count=excluded.chunk_count,
                       updated_at=excluded.updated_at, indexed_at=excluded.indexed_at""",
                (doc.id, doc.title, doc.source_path, doc.content_hash, doc.version,
                 doc.mime, doc.size_bytes, doc.chunk_count, doc.created_at,
                 doc.updated_at, doc.indexed_at),
            )
            conn.commit()
            return doc.id

    def get_document(self, doc_id: str) -> Optional[KnowledgeDoc]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return self._row_to_doc(row) if row else None

    def get_document_by_path(self, source_path: str) -> Optional[KnowledgeDoc]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM documents WHERE source_path = ?", (source_path,)).fetchone()
        return self._row_to_doc(row) if row else None

    def list_documents(self, limit: int = 50, offset: int = 0) -> List[KnowledgeDoc]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM documents ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_doc(r) for r in rows]

    def count_documents(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()
        return int(row["c"]) if row else 0

    def count_chunks(self) -> int:
        conn = self._get_conn()
        row = conn.execute("SELECT COUNT(*) AS c FROM chunks").fetchone()
        return int(row["c"]) if row else 0

    @staticmethod
    def _row_to_doc(row: sqlite3.Row) -> KnowledgeDoc:
        return KnowledgeDoc(
            id=row["id"], title=row["title"], source_path=row["source_path"],
            content_hash=row["content_hash"], version=row["version"],
            mime=row["mime"], size_bytes=row["size_bytes"],
            chunk_count=row["chunk_count"], created_at=row["created_at"],
            updated_at=row["updated_at"], indexed_at=row["indexed_at"] or "",
        )

    # ------------------------------------------------------------------
    # 片段 CRUD
    # ------------------------------------------------------------------

    def replace_chunks(self, doc_id: str, drafts: Sequence[Any]) -> List[KnowledgeChunk]:
        """用新切分结果整体替换文档的片段，返回落库后的片段（含新 ID）。"""
        from modules.knowledge.chunker import chunk_embedding_text

        doc = self.get_document(doc_id)
        title = doc.title if doc else ""
        chunks: List[KnowledgeChunk] = []
        for d in drafts:
            chunks.append(KnowledgeChunk(
                id=f"kc_{uuid.uuid4().hex[:12]}",
                doc_id=doc_id,
                seq=d.seq,
                heading_path=d.heading_path,
                text=d.text,
                char_start=d.char_start,
                char_end=d.char_end,
                token_count=d.token_count,
                terms=" ".join(tokenize_terms(chunk_embedding_text(title, d.heading_path, d.text))),
            ))
        with self._write_lock:
            conn = self._get_conn()
            # 先清 FTS（需在删 chunks 行前拿到旧 ID），再删片段行
            old_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)).fetchall()]
            if self._fts and old_ids:
                try:
                    conn.executemany(
                        "DELETE FROM chunks_fts WHERE chunk_id = ?",
                        [(cid,) for cid in old_ids])
                except sqlite3.Error as e:
                    logger.debug(f"[knowledge] 清理 FTS 失败: {e}")
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            for c in chunks:
                conn.execute(
                    """INSERT INTO chunks
                       (id, doc_id, seq, heading_path, text, char_start, char_end,
                        token_count, terms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (c.id, c.doc_id, c.seq, c.heading_path, c.text, c.char_start,
                     c.char_end, c.token_count, c.terms),
                )
                if self._fts:
                    try:
                        conn.execute(
                            "INSERT INTO chunks_fts (chunk_id, terms) VALUES (?, ?)",
                            (c.id, c.terms))
                    except sqlite3.Error as e:
                        logger.debug(f"[knowledge] 写 FTS 失败: {e}")
            conn.execute(
                "UPDATE documents SET chunk_count = ?, indexed_at = ? WHERE id = ?",
                (len(chunks), _now(), doc_id))
            conn.commit()
        return chunks

    def get_chunk(self, chunk_id: str) -> Optional[KnowledgeChunk]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
        return self._row_to_chunk(row) if row else None

    def get_chunks(self, chunk_ids: Sequence[str]) -> List[KnowledgeChunk]:
        """按传入顺序返回片段（缺失的跳过）。"""
        if not chunk_ids:
            return []
        conn = self._get_conn()
        marks = ",".join("?" * len(chunk_ids))
        rows = conn.execute(
            f"SELECT * FROM chunks WHERE id IN ({marks})", list(chunk_ids)).fetchall()
        by_id = {r["id"]: self._row_to_chunk(r) for r in rows}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def list_chunks_by_doc(self, doc_id: str) -> List[KnowledgeChunk]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM chunks WHERE doc_id = ? ORDER BY seq", (doc_id,)).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def doc_meta_map(self, doc_ids: Sequence[str]) -> Dict[str, Dict[str, str]]:
        """批量取文档标题与来源路径（检索结果补出处用）。"""
        ids = [d for d in dict.fromkeys(doc_ids) if d]
        if not ids:
            return {}
        conn = self._get_conn()
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT id, title, source_path FROM documents WHERE id IN ({marks})",
            ids).fetchall()
        return {r["id"]: {"title": r["title"], "source_path": r["source_path"]} for r in rows}

    @staticmethod
    def _row_to_chunk(row: sqlite3.Row) -> KnowledgeChunk:
        return KnowledgeChunk(
            id=row["id"], doc_id=row["doc_id"], seq=row["seq"],
            heading_path=row["heading_path"] or "", text=row["text"],
            char_start=row["char_start"], char_end=row["char_end"],
            token_count=row["token_count"], terms=row["terms"] or "",
        )

    # ------------------------------------------------------------------
    # FAISS 索引（IndexIDMap2：支持 remove_ids）
    # ------------------------------------------------------------------

    def _embed(self, texts: List[str]) -> List[Optional[List[float]]]:
        if self._embed_fn is not None:
            out: List[Optional[List[float]]] = []
            for t in texts:
                try:
                    out.append(self._embed_fn(t))
                except Exception as e:
                    logger.warning(f"[knowledge] 注入向量化失败: {e}")
                    out.append(None)
            return out
        from modules.memory.embedding import EmbeddingEngine
        return EmbeddingEngine.get_instance().embed_batch(texts)

    def embed_texts(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量向量化（供 ingestor 使用；失败位置为 None，不抛错）。"""
        return self._embed(texts)

    def embed_query(self, text: str) -> Optional[List[float]]:
        vecs = self._embed([text])
        return vecs[0] if vecs else None

    def touch_indexed(self, doc_id: str) -> None:
        """仅刷新 indexed_at（哈希未变时的轻量更新）。"""
        with self._write_lock:
            conn = self._get_conn()
            conn.execute(
                "UPDATE documents SET indexed_at = ? WHERE id = ?", (_now(), doc_id))
            conn.commit()

    def _ensure_index(self, dim: int):
        if self._index is not None:
            return self._index
        import faiss
        with faiss_file_lock(self._index_path):
            if os.path.exists(self._index_path):
                try:
                    idx = faiss.read_index(self._index_path)
                except Exception as e:
                    logger.warning(f"[knowledge] 索引读取失败，重建: {e}")
                    idx = None
            else:
                idx = None
            if idx is None or idx.d != dim:
                if idx is not None:
                    logger.info(f"[knowledge] 索引维度不匹配（旧={idx.d}, 新={dim}），重建索引")
                idx = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
                self._id_map, self._next_id = {}, 1
            else:
                if os.path.exists(self._id_map_path):
                    try:
                        with _open(self._id_map_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, dict) and "ids" in data:
                            self._id_map = {str(k): v for k, v in (data.get("ids") or {}).items()}
                            self._next_id = int(data.get("next_id") or (len(self._id_map) + 1))
                        elif isinstance(data, list):
                            # 兼容纯列表格式：位置即内层顺序（IndexIDMap2 自增 ID）
                            self._id_map = {str(i + 1): v for i, v in enumerate(data)}
                            self._next_id = len(self._id_map) + 1
                    except Exception as e:
                        logger.warning(f"[knowledge] id_map 读取失败，按空处理: {e}")
                        self._id_map, self._next_id = {}, 1
            self._index = idx
        return self._index

    def _save_index(self) -> None:
        if self._index is None:
            return
        import faiss
        with faiss_file_lock(self._index_path):
            try:
                faiss.write_index(self._index, self._index_path)
                with _open(self._id_map_path, "w", encoding="utf-8") as f:
                    json.dump({"next_id": self._next_id, "ids": self._id_map},
                              f, ensure_ascii=False)
            except Exception as e:
                logger.warning(f"[knowledge] 索引保存失败: {e}")

    # @link:block Knowledge_AddVectors = Write
    def add_vectors(self, chunk_ids: Sequence[str], vectors: Sequence[Sequence[float]]) -> int:
        """写入片段向量。返回成功写入数量（向量为空/维度不符的跳过）。"""
        pairs = [(cid, v) for cid, v in zip(chunk_ids, vectors) if v]
        if not pairs:
            return 0
        import numpy as np
        import faiss

        with self._write_lock:
            mat = np.array([v for _, v in pairs], dtype=np.float32)
            faiss.normalize_L2(mat)
            idx = self._ensure_index(mat.shape[1])
            ids = np.array(list(range(self._next_id, self._next_id + len(pairs))), dtype=np.int64)
            idx.add_with_ids(mat, ids)
            for raw_id, (cid, _) in zip(ids.tolist(), pairs):
                self._id_map[str(raw_id)] = cid
            self._next_id += len(pairs)
            self._save_index()
            return len(pairs)

    def remove_vectors(self, chunk_ids: Sequence[str]) -> int:
        """按 chunk_id 删除向量（IndexIDMap2.remove_ids，无需重建）。"""
        if not chunk_ids:
            return 0
        if self._index is None and not os.path.exists(self._index_path):
            return 0
        import numpy as np

        with self._write_lock:
            if self._index is None:
                dim = self._index_dim_guess()
                if dim <= 0:
                    return 0
                self._ensure_index(dim)
            if self._index is None:
                return 0
            wanted = set(chunk_ids)
            int_ids = [int(k) for k, v in self._id_map.items() if v in wanted]
            if not int_ids:
                return 0
            self._index.remove_ids(np.array(int_ids, dtype=np.int64))
            for raw in int_ids:
                self._id_map.pop(str(raw), None)
            self._save_index()
            return len(int_ids)

    def _index_dim_guess(self) -> int:
        """索引已存在但本实例未加载时，用文件记录的真实维度。"""
        import faiss
        try:
            return int(faiss.read_index(self._index_path).d)
        except Exception as e:
            logger.debug(f"[knowledge] 读取索引维度失败: {e}")
            return 0

    def reset_index(self) -> None:
        """清空向量索引与映射（全文重建前的准备）。"""
        with self._write_lock:
            self._index = None
            self._id_map, self._next_id = {}, 1
            for p in (self._index_path, self._id_map_path):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except OSError as e:
                        logger.warning(f"[knowledge] 删除索引文件失败: {e}")

    def search_vector(self, vector: Sequence[float], top_k: int = 10) -> List[tuple]:
        """向量召回，返回 [(chunk_id, cosine)]。"""
        if not vector:
            return []
        import numpy as np
        import faiss
        try:
            idx = self._ensure_index(len(vector))
            if idx is None or idx.ntotal == 0:
                return []
            q = np.array([vector], dtype=np.float32)
            faiss.normalize_L2(q)
            scores, labels = idx.search(q, min(top_k, idx.ntotal))
            out = []
            for score, label in zip(scores[0], labels[0]):
                cid = self._id_map.get(str(int(label))) if label >= 0 else None
                if cid:
                    out.append((cid, float(score)))
            return out
        except Exception as e:
            logger.warning(f"[knowledge] 向量检索失败: {e}")
            return []

    # ------------------------------------------------------------------
    # 关键词检索（FTS5 / 降级词项交集）
    # ------------------------------------------------------------------

    # @link:block Knowledge_KeywordSearch = Retrieve
    def search_keyword(self, query: str, top_k: int = 10) -> List[tuple]:
        """关键词召回，返回 [(chunk_id, score)]（score 越大越相关）。"""
        terms = tokenize_terms(query)
        if not terms:
            return []
        conn = self._get_conn()  # 确保 _init_db 已探测 FTS
        terms = terms[:8]

        if self._fts:
            expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
            try:
                rows = conn.execute(
                    "SELECT chunk_id, bm25(chunks_fts) AS s FROM chunks_fts "
                    "WHERE terms MATCH ? ORDER BY s LIMIT ?",
                    (expr, top_k),
                ).fetchall()
                return [(r["chunk_id"], -float(r["s"])) for r in rows]
            except sqlite3.Error as e:
                logger.debug(f"[knowledge] FTS 查询失败，降级: {e}")
                self._fts = False

        # 降级：词项交集打分
        conds = " OR ".join(["terms LIKE ?"] * len(terms))
        rows = conn.execute(
            f"SELECT id, terms FROM chunks WHERE {conds} LIMIT 5000",
            [f"%{t}%" for t in terms],
        ).fetchall()
        scored = []
        for r in rows:
            row_terms = set((r["terms"] or "").split())
            hits = sum(1 for t in terms if t in row_terms)
            if hits:
                scored.append((r["id"], float(hits) / len(terms)))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------------
    # 文档级删除 / 统计
    # ------------------------------------------------------------------

    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """删除文档：先删向量，再删片段行，最后删文档行。"""
        with self._write_lock:
            doc = self.get_document(doc_id)
            if doc is None:
                return {"deleted": False, "reason": "文档不存在"}
            chunk_ids = [c["id"] for c in self._get_conn().execute(
                "SELECT id FROM chunks WHERE doc_id = ?", (doc_id,)).fetchall()]
            removed_vecs = self.remove_vectors(chunk_ids)
            conn = self._get_conn()
            if self._fts:
                try:
                    conn.executemany(
                        "DELETE FROM chunks_fts WHERE chunk_id = ?",
                        [(cid,) for cid in chunk_ids])
                except sqlite3.Error as e:
                    logger.debug(f"[knowledge] 清理 FTS 失败: {e}")
            conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            conn.commit()
            logger.info(f"[knowledge] 删除文档 {doc.source_path}（{len(chunk_ids)} 块）")
            return {
                "deleted": True,
                "doc_id": doc_id,
                "source_path": doc.source_path,
                "chunks_removed": len(chunk_ids),
                "vectors_removed": removed_vecs,
            }

    def stats(self) -> Dict[str, Any]:
        docs = self.count_documents()
        chunks = self.count_chunks()
        index_size = os.path.getsize(self._index_path) if os.path.exists(self._index_path) else 0
        dim = None
        if self._index is not None:
            dim = self._index.d
        else:
            try:
                import faiss
                if os.path.exists(self._index_path):
                    dim = faiss.read_index(self._index_path).d
            except Exception:
                dim = None
        return {
            "enabled": bool(getattr(settings, "KNOWLEDGE_ENABLED", True)),
            "documents": docs,
            "chunks": chunks,
            "index_vectors": (self._index.ntotal if self._index is not None else None),
            "index_size_bytes": index_size,
            "dim": dim,
            "fts5": bool(self._fts),
            "db_path": self._db_path,
        }


def get_knowledge_store() -> KnowledgeStore:
    return KnowledgeStore.get_instance()


def is_enabled() -> bool:
    return bool(getattr(settings, "KNOWLEDGE_ENABLED", True))


__all__ = [
    "KnowledgeStore", "get_knowledge_store", "tokenize_terms",
    "normalize_text", "is_enabled",
]
