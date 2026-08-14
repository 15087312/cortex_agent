"""
CausalGraph — 因果图存储与邻域扩散

持久化:
- data/causal.db 中 nodes / edges 两张表
"""
import json
import os
import sqlite3
import threading
import time
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
    label: str = ""              # 节点名称（如"项目延期"）
    node_type: str = "cause"     # root / cause / effect / condition / counterfactual
    description: str = ""        # 详细描述
    keywords: List[str] = field(default_factory=list)
    importance: float = 0.5      # 节点重要性
    confidence: float = 0.5      # 因果置信度 0-1
    event_count: int = 0         # 挂载的事件数
    version: int = 0             # 版本号（用于缓存失效）
    updated_at: str = ""         # 更新时间（ISO）

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
            "version": self.version,
            "updated_at": self.updated_at,
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
            version=d.get("version", 0),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class CausalEdge:
    id: str = ""
    from_id: str = ""
    to_id: str = ""
    relation: str = "causes"     # causes / prevents / requires / alternatives
    edge_type: str = "causal"    # causal / correlation（因果 / 仅共现相关）
    confidence: float = 0.5      # 边置信度
    label: str = ""              # 可选：关系描述
    created_at: str = ""         # 创建时间（ISO），用于时间窗口过滤
    version: int = 0             # 版本号（用于缓存失效）

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "from_id": self.from_id,
            "to_id": self.to_id,
            "relation": self.relation,
            "edge_type": self.edge_type,
            "confidence": self.confidence,
            "label": self.label,
            "created_at": self.created_at,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CausalEdge":
        return cls(
            id=d["id"],
            from_id=d["from_id"],
            to_id=d["to_id"],
            relation=d.get("relation", "causes"),
            edge_type=d.get("edge_type", "causal"),
            confidence=d.get("confidence", 0.5),
            label=d.get("label", ""),
            created_at=d.get("created_at", ""),
            version=d.get("version", 0),
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
        self._write_lock = threading.Lock()  # 写操作互斥锁
        self._metrics = {
            "node_count": 0,
            "edge_count": 0,
            "query_count": 0,
            "last_query_time": 0.0,
            "avg_query_time": 0.0,
            "query_time_samples": [],
        }

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
                event_count INTEGER DEFAULT 0,
                version INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS edges (
                id TEXT PRIMARY KEY,
                from_id TEXT NOT NULL REFERENCES nodes(id),
                to_id TEXT NOT NULL REFERENCES nodes(id),
                relation TEXT DEFAULT 'causes',
                edge_type TEXT DEFAULT 'causal',
                confidence REAL DEFAULT 0.5,
                label TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                version INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
            CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
            CREATE INDEX IF NOT EXISTS idx_edges_from_to ON edges(from_id, to_id);
            CREATE INDEX IF NOT EXISTS idx_nodes_label ON nodes(label);
            CREATE INDEX IF NOT EXISTS idx_edges_created_at ON edges(created_at);
        """)
        conn.commit()
        self._migrate_schema(conn)

    def _update_metrics(self):
        """更新监控指标"""
        try:
            conn = self._get_conn()
            row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
            self._metrics["node_count"] = row[0] if row else 0
            row = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
            self._metrics["edge_count"] = row[0] if row else 0
        except Exception:
            pass

    def get_metrics(self) -> Dict[str, Any]:
        """获取 Prometheus 风格的监控指标

        Returns:
            字典格式的指标，包含：
            - causal_graph_nodes_total: 节点总数
            - causal_graph_edges_total: 边总数
            - causal_graph_query_count: 查询次数
            - causal_graph_avg_query_time_seconds: 平均查询时间
            - causal_graph_last_query_time_seconds: 最后一次查询时间
        """
        return {
            "causal_graph_nodes_total": self._metrics["node_count"],
            "causal_graph_edges_total": self._metrics["edge_count"],
            "causal_graph_query_count": self._metrics["query_count"],
            "causal_graph_avg_query_time_seconds": self._metrics["avg_query_time"],
            "causal_graph_last_query_time_seconds": self._metrics["last_query_time"],
        }

    def record_query_time(self, query_time: float):
        """记录查询时间"""
        self._metrics["query_count"] += 1
        self._metrics["last_query_time"] = query_time
        self._metrics["query_time_samples"].append(query_time)
        if len(self._metrics["query_time_samples"]) > 100:
            self._metrics["query_time_samples"].pop(0)
        if self._metrics["query_time_samples"]:
            self._metrics["avg_query_time"] = sum(self._metrics["query_time_samples"]) / len(self._metrics["query_time_samples"])

    def get_metrics_prometheus(self) -> str:
        """获取 Prometheus 格式的指标输出"""
        metrics = self.get_metrics()
        return "\n".join(
            f"{k} {v}" for k, v in metrics.items()
        )

    def _migrate_schema(self, conn):
        """增量迁移：为旧表添加新字段"""
        existing = {row[1] for row in conn.execute("PRAGMA table_info(edges)").fetchall()}
        if "edge_type" not in existing:
            conn.execute("ALTER TABLE edges ADD COLUMN edge_type TEXT DEFAULT 'causal'")
        if "created_at" not in existing:
            conn.execute("ALTER TABLE edges ADD COLUMN created_at TEXT DEFAULT ''")  # pragma: no cover — created_at 缺失时 _init_db 的 idx_edges_created_at 索引先失败，该分支不可达
        if "version" not in existing:
            conn.execute("ALTER TABLE edges ADD COLUMN version INTEGER DEFAULT 0")
        
        # 迁移 nodes 表
        existing_nodes = {row[1] for row in conn.execute("PRAGMA table_info(nodes)").fetchall()}
        if "version" not in existing_nodes:
            conn.execute("ALTER TABLE nodes ADD COLUMN version INTEGER DEFAULT 0")
        if "updated_at" not in existing_nodes:
            conn.execute("ALTER TABLE nodes ADD COLUMN updated_at TEXT DEFAULT ''")
        conn.commit()

    # ── DAG 环路检测 ──

    def _has_cycle(self, from_id: str, to_id: str) -> bool:
        """检查添加 from_id→to_id 边后是否形成环路（DFS）"""
        if from_id == to_id:
            return True  # 自环
        visited = set()
        stack = [to_id]
        while stack:
            node = stack.pop()
            if node == from_id:
                return True  # 发现环路
            if node in visited:
                continue
            visited.add(node)
            # 只沿 from_id 方向遍历（检查 to_id 是否能到达 from_id）
            rows = self._get_conn().execute(
                "SELECT to_id FROM edges WHERE from_id=?", (node,)
            ).fetchall()
            for r in rows:
                stack.append(r["to_id"])
        return False

    # ── Node CRUD ──

    def save_node(self, node: CausalNode) -> str:
        with self._write_lock:
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

    def save_edge(self, edge: CausalEdge) -> Optional[str]:
        """保存因果边（DAG 校验：形成环路则拒绝写入）"""
        with self._write_lock:
            if not edge.id:
                edge.id = f"ce_{uuid.uuid4().hex[:12]}"

            # 自动填充创建时间
            if not edge.created_at:
                from datetime import datetime, timezone
                edge.created_at = datetime.now(timezone.utc).isoformat()

            # DAG 校验：新边不能形成环路
            if self._has_cycle(edge.from_id, edge.to_id):
                logger.debug(f"[因果图] 环路检测: {edge.from_id}→{edge.to_id} 会形成环，拒绝写入")
                return None

            conn = self._get_conn()
            conn.execute(
            """INSERT OR REPLACE INTO edges
               (id, from_id, to_id, relation, edge_type, confidence, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (edge.id, edge.from_id, edge.to_id, edge.relation,
             edge.edge_type, edge.confidence, edge.label, edge.created_at),
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
        _start = time.time()
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
                query = query.replace("WHERE e", "WHERE e.relation = ? AND e")
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

        self.record_query_time(time.time() - _start)
        return results

    def get_predecessors(self, node_id: str, min_confidence: float = 0.0) -> List[CausalNode]:
        """获取前驱因节点（溯源用）"""
        _start = time.time()
        rows = self._get_conn().execute(
            """SELECT n.* FROM nodes n
               JOIN edges e ON e.from_id = n.id
               WHERE e.to_id = ? AND e.confidence >= ?
               ORDER BY e.confidence DESC""",
            (node_id, min_confidence),
        ).fetchall()
        self.record_query_time(time.time() - _start)
        return [CausalNode.from_dict(dict(r)) for r in rows]

    def get_successors(self, node_id: str, min_confidence: float = 0.0) -> List[CausalNode]:
        """获取后继果节点（预测用）"""
        _start = time.time()
        rows = self._get_conn().execute(
            """SELECT n.* FROM nodes n
               JOIN edges e ON e.to_id = n.id
               WHERE e.from_id = ? AND e.confidence >= ?
               ORDER BY e.confidence DESC""",
            (node_id, min_confidence),
        ).fetchall()
        self.record_query_time(time.time() - _start)
        return [CausalNode.from_dict(dict(r)) for r in rows]

    def find_anchor_nodes(self, query: str, top_k: int = 5) -> List[Tuple[CausalNode, float]]:
        """根据关键词和语义相似度找到锚点节点（用于深度回忆入口定位）

        融合策略：
        - 关键词匹配得分（精确匹配）
        - 语义相似度得分（向量余弦相似度）
        - 加权融合：0.6 * 关键词 + 0.4 * 语义
        """
        keywords = self._extract_keywords(query)
        if not keywords:
            return []

        # ── 1. 关键词匹配（原有逻辑）──
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
            # 对中文长词按 2-char 滑动窗口拆
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

        # ── 2. 语义相似度（新增）──
        semantic_scores: Dict[str, float] = {}
        try:
            from modules.memory.embedding import EmbeddingEngine
            embedder = EmbeddingEngine.get_instance()

            # 获取所有节点（用于批量向量化）
            all_rows = conn.execute("SELECT * FROM nodes").fetchall()
            if all_rows:
                # 构建节点文本列表
                node_texts = []
                for r in all_rows:
                    node = CausalNode.from_dict(dict(r))
                    node_texts.append(f"{node.label} {' '.join(node.keywords)} {node.description}".strip())

                # 批量向量化
                query_vec = embedder.embed(query)
                node_vecs = embedder.embed_batch(node_texts)

                if query_vec and node_vecs:
                    import numpy as np
                    query_arr = np.array(query_vec, dtype=np.float32)
                    for i, vec in enumerate(node_vecs):
                        if vec is None:
                            continue
                        node = CausalNode.from_dict(dict(all_rows[i]))
                        node_arr = np.array(vec, dtype=np.float32)
                        # 余弦相似度
                        sim = float(np.dot(query_arr, node_arr))
                        semantic_scores[node.id] = sim
                        # 合并到 scored
                        if node.id not in scored:
                            scored[node.id] = (node, 0.0)
        except Exception as e:
            logger.debug(f"[CausalGraph] 语义相似度计算失败，降级到关键词匹配: {e}")

        # ── 3. 加权融合 ──
        # 归一化关键词得分
        max_kw_score = max((s for _, s in scored.values()), default=1.0) or 1.0
        # 归一化语义得分
        max_sem_score = max(semantic_scores.values(), default=1.0) or 1.0

        final_scores: Dict[str, float] = {}
        for node_id, (node, kw_score) in scored.items():
            # 归一化
            norm_kw = kw_score / max_kw_score
            norm_sem = semantic_scores.get(node_id, 0.0) / max_sem_score if max_sem_score > 0 else 0.0
            # 加权融合：关键词 60% + 语义 40%
            final_score = 0.6 * norm_kw + 0.4 * norm_sem
            final_scores[node_id] = (node, final_score)

        # 按最终得分排序
        sorted_nodes = sorted(final_scores.values(), key=lambda x: x[1], reverse=True)
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

    # ── 共现统计（自动发现因果关系）──

    def update_cooccurrence(self, event_ids: List[str] = None, min_cooccur: int = 2, store=None):
        """从事件的 causal_node_ids 中统计共现，用 P(B|A)-P(B|¬A) 区分因果/相关

        Args:
            event_ids: 指定事件 ID 列表（增量更新）；None 则扫描全量
            min_cooccur: 最少共现次数才建边
            store: EventStore 实例（测试用）；None 则用全局单例
        """
        if store is None:
            from modules.memory.event_store import EventStore
            store = EventStore.get_instance()

        if event_ids:
            events = [store.get_event(eid) for eid in event_ids]
            events = [e for e in events if e]
        else:
            events = store.list_events(limit=1000)

        N = len(events)
        if N < 2:
            return 0

        # 统计每个节点出现次数 & 节点对共现次数
        node_counts = {}  # node_id -> count
        pair_counts = {}  # (A, B) -> count (A 出现时 B 也出现)
        for ev in events:
            node_ids = [nid for nid in (ev.causal_node_ids or []) if nid]
            for nid in node_ids:
                node_counts[nid] = node_counts.get(nid, 0) + 1
            for i in range(len(node_ids)):
                for j in range(len(node_ids)):
                    if i != j:
                        key = (node_ids[i], node_ids[j])
                        pair_counts[key] = pair_counts.get(key, 0) + 1

        # 用 P(B|A) - P(B|¬A) 计算因果强度
        edges_created = 0
        edges_boosted = 0
        for (a_id, b_id), cooccur in pair_counts.items():
            if cooccur < min_cooccur:
                continue

            count_a = node_counts.get(a_id, 0)
            count_b = node_counts.get(b_id, 0)

            # P(B|A) = cooccur(A,B) / count(A)
            p_b_given_a = cooccur / max(count_a, 1)
            # P(B|¬A) = (count(B) - cooccur) / (N - count(A))
            p_b_given_not_a = max(0, count_b - cooccur) / max(N - count_a, 1)

            # 因果强度 = P(B|A) - P(B|¬A)，clamp 到 [0, 1]
            causal_strength = max(0.0, min(1.0, p_b_given_a - p_b_given_not_a))

            # 判断类型：strength > 0.1 → causal，否则 → correlation
            edge_type = "causal" if causal_strength > 0.1 else "correlation"

            # 检查边是否已存在
            existing = self._get_conn().execute(
                "SELECT * FROM edges WHERE from_id=? AND to_id=?",
                (a_id, b_id),
            ).fetchall()

            if existing:
                edge = CausalEdge.from_dict(dict(existing[0]))
                # 升级：correlation → causal（如果新计算是 causal）
                if edge_type == "causal" and edge.edge_type == "correlation":
                    edge.edge_type = "causal"
                boost = min(0.1, causal_strength * 0.1)
                edge.confidence = min(0.99, edge.confidence + boost)
                self.save_edge(edge)
                edges_boosted += 1
            else:
                confidence = min(0.9, 0.2 + causal_strength * 0.7)
                edge = CausalEdge(
                    from_id=a_id, to_id=b_id,
                    relation="causes", edge_type=edge_type,
                    confidence=confidence,
                )
                result = self.save_edge(edge)
                if result:
                    edges_created += 1

        # 更新监控指标
        self._metrics["query_count"] += 1
        self._metrics["edge_count"] = self._get_conn().execute("SELECT COUNT(*) FROM edges").fetchone()[0]

        logger.info(f"[因果图] 共现统计: {edges_created} 条新边, {edges_boosted} 条增强")
        return edges_created + edges_boosted

    def get_related_events(self, node_id: str, limit: int = 20, store=None) -> list:
        """获取关联到指定节点的事件（反向查询）"""
        if store is None:
            from modules.memory.event_store import EventStore
            store = EventStore.get_instance()
        events = store.list_events(limit=500)
        return [e for e in events if node_id in (e.causal_node_ids or [])][:limit]

    def list_all_edges(self, node_ids: Optional[List[str]] = None, time_window: Optional[str] = None) -> List[CausalEdge]:
        """高效获取边列表（O(1) 索引查询，非 O(N²)）
        
        Args:
            node_ids: 可选，只返回涉及指定节点的边
            time_window: 可选，时间窗口（如"30d", "7d", "24h"）
        
        Returns:
            CausalEdge 列表
        """
        conn = self._get_conn()
        
        # 构建时间窗口条件
        time_cond = ""
        time_params = []
        if time_window:
            import re
            match = re.match(r'(\d+)(d|h|m)', time_window)
            if match:
                val, unit = int(match.group(1)), match.group(2)
                seconds = val * (3600 * 24 if unit == 'd' else (3600 if unit == 'h' else 60))
                time_cond = " AND created_at >= datetime('now', ?)"
                time_params = [f"-{seconds} seconds"]
        
        # 构建节点过滤条件
        node_cond = ""
        node_params = []
        if node_ids:
            node_cond = " WHERE from_id IN ({}) OR to_id IN ({})".format(
                ",".join("?" for _ in node_ids),
                ",".join("?" for _ in node_ids)
            )
            node_params = node_ids + node_ids
        
        # 单次查询获取所有边（O(1) 索引查询）
        query = f"SELECT * FROM edges{node_cond}{time_cond} ORDER BY created_at DESC"
        params = node_params + time_params
        
        rows = conn.execute(query, params).fetchall()
        return [CausalEdge.from_dict(dict(r)) for r in rows]

    def get_edge_stats(self) -> Dict[str, Any]:
        """获取边统计信息（O(1) 聚合查询）"""
        conn = self._get_conn()
        row = conn.execute("""
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT from_id) as from_nodes,
                COUNT(DISTINCT to_id) as to_nodes,
                AVG(confidence) as avg_confidence,
                MIN(created_at) as oldest,
                MAX(created_at) as newest
            FROM edges
        """).fetchone()
        
        return {
            "total_edges": row["total"],
            "from_nodes": row["from_nodes"],
            "to_nodes": row["to_nodes"],
            "avg_confidence": row["avg_confidence"] or 0.5,
            "oldest_edge": row["oldest"],
            "newest_edge": row["newest"],
        }

    # ── 节点合并（粒度归一化）──

    def merge_similar_nodes(self, similarity_threshold: float = 0.9):
        """合并语义相似的节点（标签包含关系 + 关键词重合）

        合并策略：保留标签更长的节点，将被合并节点的边和事件转移过来。
        """
        nodes = self.list_nodes(limit=500)
        merged = set()
        merge_count = 0

        for i, n1 in enumerate(nodes):
            if n1.id in merged:
                continue
            for j in range(i + 1, len(nodes)):
                n2 = nodes[j]
                if n2.id in merged:
                    continue

                # 判断是否应合并：标签包含关系 或 关键词高度重合
                should_merge = False
                if n1.label in n2.label or n2.label in n1.label:
                    should_merge = True
                elif n1.keywords and n2.keywords:
                    common = set(k.lower() for k in n1.keywords) & set(k.lower() for k in n2.keywords)
                    total = set(k.lower() for k in n1.keywords) | set(k.lower() for k in n2.keywords)
                    if total and len(common) / len(total) >= similarity_threshold:
                        should_merge = True

                if should_merge:
                    # 保留标签更长的
                    keep, remove = (n1, n2) if len(n1.label) >= len(n2.label) else (n2, n1)
                    # 转移边
                    self._转移边(remove.id, keep.id)
                    # 转移事件关联
                    self._转移事件关联(remove.id, keep.id)
                    # 合并关键词
                    keep.keywords = list(set(keep.keywords + remove.keywords))
                    keep.event_count += remove.event_count
                    self.save_node(keep)
                    # 删除被合并节点
                    self.delete_node(remove.id)
                    merged.add(remove.id)
                    merge_count += 1

        if merge_count:
            logger.info(f"[因果图] 节点合并: {merge_count} 对")
        return merge_count

    def _转移边(self, old_id: str, new_id: str):
        """将指向 old_id 的边重定向到 new_id"""
        conn = self._get_conn()
        # 更新 from_id
        conn.execute("UPDATE edges SET from_id=? WHERE from_id=?", (new_id, old_id))
        # 更新 to_id
        conn.execute("UPDATE edges SET to_id=? WHERE to_id=?", (new_id, old_id))
        # 删除自环
        conn.execute("DELETE FROM edges WHERE from_id=to_id")
        conn.commit()

    def _转移事件关联(self, old_id: str, new_id: str):
        """将事件的 causal_node_ids 中的 old_id 替换为 new_id"""
        from modules.memory.event_store import EventStore
        store = EventStore.get_instance()
        for ev in store.list_events(limit=1000):
            ids = ev.causal_node_ids or []
            if old_id in ids:
                ids = [new_id if x == old_id else x for x in ids]
                ids = list(dict.fromkeys(ids))  # 去重保序
                ev.causal_node_ids = ids
                store.save_event(ev)

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
