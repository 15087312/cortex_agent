"""桌宠独立记忆库 + 因果图（按人设隔离）

每个人设拥有独立的一对存储：
  data/pet_memory/persona_<id>/memory.db          （事件记忆）
  data/pet_memory/persona_<id>/causal.db          （因果节点/边）
  data/pet_memory/persona_<id>/events_faiss.index （向量索引）
  data/pet_memory/persona_<id>/events_id_map.json

与主库（data/memory.db + data/causal.db）完全隔离；事件通过
`events.causal_node_ids` 与因果节点关联（物理分库、逻辑绑定）。
"""
import asyncio
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from utils.logger import setup_logger

logger = setup_logger("pet_memory_bank")

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PET_MEMORY_ROOT = Path(
    os.environ.get("PET_MEMORY_DIR") or (_PROJECT_ROOT / "data" / "pet_memory")
)


class PetMemoryBank:
    """按人设隔离的 EventStore + CausalGraph + EventRetrieval 缓存。"""

    _instance: Optional["PetMemoryBank"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._stores: Dict[int, Any] = {}
        self._graphs: Dict[int, Any] = {}
        self._retrievers: Dict[int, Any] = {}
        self._pending: Set[int] = set()
        self._tasks: Set[asyncio.Task] = set()
        self._state_lock = threading.RLock()

    @classmethod
    def get_instance(cls) -> "PetMemoryBank":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── 路径 / 实例 ──

    @staticmethod
    def _dir(pid: int) -> Path:
        return _PET_MEMORY_ROOT / f"persona_{int(pid)}"

    def _paths(self, pid: int) -> Dict[str, str]:
        base = self._dir(pid)
        base.mkdir(parents=True, exist_ok=True)
        return {
            "memory": str(base / "memory.db"),
            "causal": str(base / "causal.db"),
            "faiss": str(base / "events_faiss.index"),
            "id_map": str(base / "events_id_map.json"),
        }

    def store(self, pid: int):
        pid = int(pid)
        with self._state_lock:
            if pid not in self._stores:
                from modules.memory.event_store import EventStore
                p = self._paths(pid)
                self._stores[pid] = EventStore(
                    db_path=p["memory"],
                    faiss_index_path=p["faiss"],
                    id_map_path=p["id_map"],
                )
            return self._stores[pid]

    def graph(self, pid: int):
        pid = int(pid)
        with self._state_lock:
            if pid not in self._graphs:
                from modules.memory.causal_graph import CausalGraph
                self._graphs[pid] = CausalGraph(db_path=self._paths(pid)["causal"])
            return self._graphs[pid]

    def retrieval(self, pid: int):
        pid = int(pid)
        with self._state_lock:
            if pid not in self._retrievers:
                from modules.memory.event_retrieval import EventRetrieval
                self._retrievers[pid] = EventRetrieval(
                    store=self.store(pid),
                    graph=self.graph(pid),
                )
            return self._retrievers[pid]

    # ── 检索 ──

    async def retrieve(self, pid: int, query: str, max_results: int = 2) -> List[Any]:
        """返回该人设记忆库中与 query 最相关的事件（含因果扩散）。"""
        try:
            # 空库先短路：避免每次回复都做一次 embedding / FAISS 查询
            conn = self.store(pid)._get_conn()
            if not conn.execute("SELECT 1 FROM events LIMIT 1").fetchone():
                return []
            return await self.retrieval(pid).retrieve(
                query=query or "用户与桌宠互动",
                max_results=max(1, int(max_results)),
                owner_id=f"pet:{int(pid)}",
            )
        except Exception as e:
            logger.debug(f"[PetMemoryBank] 检索失败 (persona={pid}): {e}")
            return []

    def causal_context(self, pid: int, query: str, limit: int = 4) -> List[str]:
        """返回该人设因果图中与 query 相关的 1 跳关系文本。"""
        try:
            graph = self.graph(pid)
            if not graph.list_nodes(limit=1):
                return []
            anchors = graph.find_anchor_nodes(query or "", top_k=3)
            if not anchors:
                return []
            lines: List[str] = []
            seen: Set[str] = set()
            for node, _score in anchors:
                for n, edge, _hop in graph.get_neighbors(node.id, hops=1):
                    if n.id in seen:
                        continue
                    seen.add(n.id)
                    lines.append(
                        f"{node.label} --{edge.relation}--> {n.label} "
                        f"(置信度 {edge.confidence:.2f})"
                    )
                    if len(lines) >= limit:
                        return lines
            return lines
        except Exception as e:
            logger.debug(f"[PetMemoryBank] 因果上下文失败 (persona={pid}): {e}")
            return []

    # ── 自动提炼（用户对话 → 事件 + 因果图） ──

    def schedule_extract(self, pid: int, user_text: str, assistant_text: str,
                         model_client=None) -> None:
        """异步提炼一轮对话；同一人设同时只跑一个提炼任务。"""
        if not (user_text or "").strip() or not (assistant_text or "").strip():
            return
        if model_client is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pid = int(pid)
        with self._state_lock:
            if pid in self._pending:
                return
            self._pending.add(pid)
        conversation = f"用户：{user_text.strip()}\n桌宠：{assistant_text.strip()}"
        task = loop.create_task(self._extract(pid, conversation, model_client))
        self._tasks.add(task)

        def _done(t: asyncio.Task):
            self._tasks.discard(t)
            with self._state_lock:
                self._pending.discard(pid)

        task.add_done_callback(_done)

    async def _extract(self, pid: int, conversation: str, model_client) -> None:
        try:
            from modules.memory.event_reducer import EventReducer
            reducer = EventReducer(
                model_client=model_client,
                store=self.store(pid),
                graph=self.graph(pid),
            )
            saved = await reducer.reduce(
                session_id=f"pet:{pid}",
                conversation_text=conversation,
                owner_id=f"pet:{pid}",
            )
            if saved:
                logger.info(f"[PetMemoryBank] 人设 {pid} 提炼 {len(saved)} 条记忆")
        except Exception as e:
            logger.debug(f"[PetMemoryBank] 提炼失败 (persona={pid}): {e}")

    # ── 生命周期 ──

    def delete_persona(self, pid: int) -> None:
        """关闭连接并删除该人设的记忆/因果图文件。"""
        pid = int(pid)
        with self._state_lock:
            store = self._stores.pop(pid, None)
            graph = self._graphs.pop(pid, None)
            self._retrievers.pop(pid, None)
            self._pending.discard(pid)
        for obj in (store, graph):
            conn = getattr(obj, "_conn", None)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
        shutil.rmtree(self._dir(pid), ignore_errors=True)

    def stats(self, pid: int) -> Dict[str, int]:
        """返回该人设的记忆事件数 / 因果节点数 / 边数（用于 UI/诊断）。"""
        out = {"memory_events": 0, "causal_nodes": 0, "causal_edges": 0}
        try:
            conn = self.store(pid)._get_conn()
            out["memory_events"] = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        except Exception:
            pass
        try:
            conn = self.graph(pid)._get_conn()
            out["causal_nodes"] = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            out["causal_edges"] = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        except Exception:
            pass
        return out
