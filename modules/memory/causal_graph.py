"""
CausalGraph — 因果图存储与邻域扩散

持久化:
- data/causal.db 中 nodes / edges 两张表
"""
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("causal_graph")

CAUSAL_RELATIONS = {"causes", "prevents", "requires", "alternatives"}

NODE_TYPES = {"root", "cause", "effect", "condition", "counterfactual"}


@dataclass
class CausalNode:
    id: str = ""
    label: str = ""              # 节点名称（如“项目延期”）
    node_type: str = "cause"     # root / cause / effect / condition / counterfactual
    description: str = ""        # 详细描述
    keywords: List[str] = field(default_factory=list)
    importance: float = 0.5      # 节点重要性
    confidence: float = 0.5      # 因果置信度 0-1
    event_count: int = 0         # 挂载的事件数

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "node_type": self.node_type,
            "description": self.description,
            "keywords": json.dumps(self.keywords, ensure_ascii=False),
            "importance": self.importance,
            "confidence": self.confidence,
            "event_count": self.event_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CausalNode":
        return cls(
            id=d["id"],
            label=d["label"],
            node_type=d.get("node_type", "cause"),
            description=d.get("description", ""),
            keywords=json.loads(d.get("keywords", "[]")),
            importance=d.get("importance", 0.5),
            confidence=d.get("confidence", 0.5),
            event_count=d.get("event_count", 0),
        )


@dataclass
class CausalEdge:
    id: str = ""
    from_id: str = ""
    to_id: str = ""
    relation: str = "causes"     # causes / prevents / requires / alternatives
    confidence: float = 0.5      # 边置信度
    label: str = ""              # 可选：关系描述

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation": self.relation,
            "confidence": self.confidence,
            "label": self.label,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CausalEdge":
        return cls(
            id=d["id"],
            from_id=d["from_id"],
            to_id=d["to_id"],
            relation=d.get("relation", "causes"),
            confidence=d.get("confidence", 0.5),
            label=d.get("label", ""),
        )


class CausalGraph:
    """因果图 — 节点和边的持久化存储，支持邻域扩散"""

    _instance: "CausalGraph" = None
    _lock = threading.Lock()

    def __init__(self, db_path: str = None):
        db_path = db_path or getattr(settings, "CAUSAL_DB_PATH", "data/causal.db")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @classmethod
    def get_instance(cls, **kwargs) -> "CausalGraph":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(**kwargs)
        return cls._instance

    # ── DB ──

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_db()
        return self._conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id TEXT PRIMARY KEY,
                label TEXT NOT NULL,
                node_type TEXT DEFAULT 'cause',
                description TEXT DEFAULT '',
                keywords TEXT DEFAULT '[]',
                importance REAL DEFAULT 0.5,
                confidence REAL DEFAULT 0.5,
                event_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL REFERENCES nodes(id),
                to_id TEXT NOT NULL REFERENCES nodes(id),
                relation TEXT DEFAULT 'causes',
                confidence REAL DEFAULT 0.5,
                label TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(label);
        """)
        conn.commit()

    # ── Node CRUD ──

    def save_node(self, node: CausalNode) -> str:
        if not node.id:
            node.id = f"cn_{uuid.uuid4().hex[:12]}"
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, label, node_type, description, keywords, importance, confidence, event_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (node.id, node.label, node.node_type, node.description,
             json.dumps(node.keywords, ensure_ascii=False),
             node.importance, node.confidence, node.event_count),
        )
        conn.commit()
        return node.id

    def get_node(self, node_id: str) -> Optional[CausalNode]:
        row = self._get_conn().execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        return CausalNode.from_dict(dict(row)) if row else None

    def delete_node(self, node_id: str) -> bool:
        conn = self._get_conn()
        conn.execute("DELETE FROM edges WHERE from_id=? OR to_id=?", (node_id, node_id))
        cur = conn.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        conn.commit()
        return cur.rowcount > 0

    def find_nodes_by_label(self, label: str) -> List[CausalNode]:
        rows = self._get_conn().execute(
            "SELECT * FROM nodes WHERE label LIKE ? ORDER BY importance DESC",
            (f"%{label}%",),
        ).fetchall()
        return [CausalNode.from_dict(dict(r)) for r in rows]

    def list_nodes(self, limit: int = 100, offset: int = 0) -> List[CausalNode]:
        rows = self._get_conn().execute(
            "SELECT * FROM nodes ORDER BY importance DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [CausalNode.from_dict(dict(r)) for r in rows]

    # ── Edge CRUD ──

    def save_edge(self, edge: CausalEdge) -> str:
        if not edge.id:
            edge.id = f"ce_{uuid.uuid4().hex[:12]}"
        conn = self._get_conn()
        conn.execute(
            """INSERT OR REPLACE INTO edges
               (id, from_id, to_id, relation, confidence, label)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (edge.id, edge.from_id, edge.to_id, edge.relation, edge.confidence, edge.label),
        )
        conn.commit()
        return edge.id

    def get_edge(self, edge_id: str) -> Optional[CausalEdge]:
        row = self._get_conn().execute(
            "SELECT * FROM edges WHERE id = ?", (edge_id,)
        ).fetchone()
        return CausalEdge.from_dict(dict(row)) if row else None

    def delete_edge(self, edge_id: str) -> bool:
        cur = self._get_conn().execute("DELETE FROM edges WHERE id=?", (edge_id,))
        self._get_conn().commit()
        return cur.rowcount > 0

    # ── 邻域扩散 ──

    def get_neighbors(
        self, node_id: str, hops: int = 1,
        relation_filter: Optional[str] = None,
        min_confidence: float = 0.0,
    ) -> List[Tuple[CausalNode, CausalEdge, int]]:
        """获取指定节点的邻域节点

        Returns:
            [(node, edge_connecting_to_it, hop_distance), ...]
        """
        conn = self._get_conn()
        visited: set = set()
        results: List[Tuple[CausalNode, CausalEdge, int]] = []
        current: List[Tuple[str, int]] = [(node_id, 0)]
        visited.add(node_id)

        while current:
            nid, dist = current.pop(0)
            if dist >= hops:
                continue

            # 用不同的别名避免 n.id 歧义
            query = """
                SELECT e.id AS eid, e.from_id, e.to_id, e.relation, e.confidence AS e_conf, e.label AS e_label,
                       n.id AS nid, n.label, n.node_type, n.description, n.keywords,
                       n.importance, n.confidence AS n_conf, n.event_count
                FROM edges e JOIN nodes n ON n.id = e.from_id
                WHERE e.to_id = ? AND e.confidence >= ?
                UNION
                SELECT e.id AS eid, e.from_id, e.to_id, e.relation, e.confidence AS e_conf, e.label AS e_label,
                       n.id AS nid, n.label, n.node_type, n.description, n.keywords,
                       n.importance, n.confidence AS n_conf, n.event_count
                FROM edges e JOIN nodes n ON n.id = e.to_id
                WHERE e.from_id = ? AND e.confidence >= ?
            """
            params = (nid, min_confidence, nid, min_confidence)
            if relation_filter:
                query = query.replace("WHERE e", f"WHERE e.relation = ? AND e")
                params = (relation_filter, nid, min_confidence, relation_filter, nid, min_confidence)

            rows = conn.execute(query, params).fetchall()
            for r in rows:
                neighbor_id = r["from_id"] if r["from_id"] != nid else r["to_id"]
                if neighbor_id in visited:
                    continue
                visited.add(neighbor_id)
                node = CausalNode.from_dict(dict(
                    zip(("id", "label", "node_type", "description", "keywords",
                         "importance", "confidence", "event_count"),
                        (r["nid"], r["label"], r["node_type"], r["description"],
                         r["keywords"], r["importance"], r["n_conf"], r["event_count"]))))
                edge = CausalEdge(
                    id=r["eid"], from_id=r["from_id"], to_id=r["to_id"],
                    relation=r["relation"], confidence=r["e_conf"], label=r["e_label"],
                )
                results.append((node, edge, dist + 1))
                current.append((neighbor_id, dist + 1))

        return results

    def get_predecessors(self, node_id: str, min_confidence: float = 0.0) -> List[CausalNode]:
        """获取前驱因节点（溯源用）"""
        rows = self._get_conn().execute(
            """SELECT n.* FROM nodes n
               JOIN edges e ON e.from_id = n.id
               WHERE e.to_id = ? AND e.confidence >= ?
               ORDER BY e.confidence DESC""",
            (node_id, min_confidence),
        ).fetchall()
        return [CausalNode.from_dict(dict(r)) for r in rows]

    def get_successors(self, node_id: str, min_confidence: float = 0.0) -> List[CausalNode]:
        """获取后继果节点（预测用）"""
        rows = self._get_conn().execute(
            """SELECT n.* FROM nodes n
               JOIN edges e ON e.to_id = n.id
               WHERE e.from_id = ? AND e.confidence >= ?
               ORDER BY e.confidence DESC""",
            (node_id, min_confidence),
        ).fetchall()
        return [CausalNode.from_dict(dict(r)) for r in rows]

    def find_anchor_nodes(self, query: str, top_k: int = 5) -> List[Tuple[CausalNode, float]]:
        """根据关键词找到锚点节点（用于深度回忆入口定位）"""
        keywords = self._extract_keywords(query)
        if not keywords:
            return []
        # 也尝试拆分多词中文短语（如"为什么项目延期"→"项目延期"）
        import re as _re
        split_keywords = set()
        for kw in keywords:
            split_keywords.add(kw)
            # 按常见虚词拆分
            parts = _re.split(r'[的怎么了为什么导致因为所以]', kw)
            for p in parts:
                p = p.strip()
                if len(p) >= 2:
                    split_keywords.add(p)
            # 对中文长词按 2-char 滑动窗口拆（"技术问题"→"技术","问题"）
            if _re.search(r'[\u4e00-\u9fff]', kw):
                for i in range(len(kw) - 1):
                    bigram = kw[i:i+2]
                    if _re.match(r'[\u4e00-\u9fff]{2}', bigram):
                        split_keywords.add(bigram)
        scored: Dict[str, Tuple[CausalNode, float]] = {}
        conn = self._get_conn()
        for kw in split_keywords:
            rows = conn.execute(
                "SELECT * FROM nodes WHERE label LIKE ? OR keywords LIKE ?",
                (f"%{kw}%", f"%{kw}%"),
            ).fetchall()
            for r in rows:
                node = CausalNode.from_dict(dict(r))
                if node.id not in scored:
                    scored[node.id] = (node, 0.0)
                scored[node.id] = (node, scored[node.id][1] + 1.0)
        sorted_nodes = sorted(scored.values(), key=lambda x: x[1], reverse=True)
        return sorted_nodes[:top_k]

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        import re
        if not text:
            return []
        keywords = set()
        eng = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text)
        keywords.update(w.lower() for w in eng)
        chn = re.findall(r'[\u4e00-\u9fff]{2,}', text)
        keywords.update(chn)
        return list(keywords)

    # ── 清理 ──

    def clear_all(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM nodes")
        conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __del__(self):
        self.close()
