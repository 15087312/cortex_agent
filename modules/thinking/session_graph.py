"""会话执行图谱 — 记录多 Agent 会话中"谁呼唤谁 / 谁回复谁"

数据源：api_stream 收到的 model_comm broadcast 事件。
每个 Agent 发言（sender X，回复给 Y）：
  - 呼唤边 Y → X（Y 呼唤 X 执行）
  - 回复边 X → Y（X 回复 Y）
无 return_to 的发言视为回复用户（总指挥直出 / 用户呼唤总指挥）。

图谱按会话内存缓存 + 持久化到会话 metadata（session_graph），重启后保留。
"""
import threading
from typing import Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("session_graph")

_MAX_EDGES = 200      # 每会话最多保留的边数
_MAX_NODES = 40       # 每会话最多节点数


class SessionGraphStore:
    def __init__(self):
        self._graphs: Dict[str, dict] = {}
        self._lock = threading.RLock()

    # ── 记录 ──────────────────────────────────────────────────────────

    def record(self, session_id: str, model_id: str, identity_name: str,
               tier: str, return_to_model_id: str, entry_type: str,
               content: str = "", ts: float = 0.0) -> None:
        if not session_id or not model_id:
            return
        # 用户输入消息不生成图谱边（用户→Agent 的"呼唤"由 Agent 回复用户时产生）
        if model_id == "user" or model_id == "__user__" or model_id.startswith("user:"):
            return
        with self._lock:
            g = self._graphs.setdefault(session_id, {"nodes": {}, "edges": {}})
            nodes = g["nodes"]
            edges = g["edges"]

            # 发言者节点
            node = nodes.setdefault(model_id, {
                "id": model_id, "label": identity_name or tier or model_id[:12],
                "tier": tier, "count": 0, "last_content": "", "last_ts": 0,
            })
            node["count"] += 1
            if content:
                node["last_content"] = str(content)[:120]
            if ts:
                node["last_ts"] = ts

            if return_to_model_id:
                # 有回复对象：return_to 呼唤 model_id，model_id 回复 return_to
                # 按发言者层级推断上级层级（专家→主管→总指挥→用户）
                parent_tier = {"expert": "supervisor", "supervisor": "large", "large": "user"}.get(tier, "")
                parent = nodes.setdefault(return_to_model_id, {
                    "id": return_to_model_id, "label": return_to_model_id[:12],
                    "tier": parent_tier, "count": 0, "last_content": "", "last_ts": 0,
                })
                parent["count"] += 1
                self._touch_edge(edges, return_to_model_id, model_id, "呼唤", ts, content)
                self._touch_edge(edges, model_id, return_to_model_id, "回复", ts, content)
            else:
                # 无回复对象：视为回复用户（用户呼唤 → 该 Agent 干活并输出给用户）
                user_node = nodes.setdefault("__user__", {
                    "id": "__user__", "label": "用户", "tier": "user",
                    "count": 0, "last_content": "", "last_ts": 0,
                })
                user_node["count"] += 1
                self._touch_edge(edges, "__user__", model_id, "呼唤", ts, "")
                self._touch_edge(edges, model_id, "__user__", "回复", ts, content)

    @staticmethod
    def _touch_edge(edges: dict, frm: str, to: str, etype: str, ts: float, content: str) -> None:
        key = f"{frm}|{to}|{etype}"
        e = edges.setdefault(key, {
            "from": frm, "to": to, "type": etype, "count": 0,
            "last_content": "", "last_ts": 0,
        })
        e["count"] += 1
        if content:
            e["last_content"] = str(content)[:100]
        if ts:
            e["last_ts"] = ts

    # ── 查询 ──────────────────────────────────────────────────────────

    def get_graph(self, session_id: str) -> dict:
        """返回会话图谱 {nodes: [...], edges: [...]}（按 tier 排序节点）"""
        with self._lock:
            g = self._graphs.get(session_id)
            if not g:
                return {"nodes": [], "edges": []}
            tier_rank = {"user": 0, "large": 1, "supervisor": 2, "expert": 3}
            nodes = sorted(g["nodes"].values(), key=lambda n: (tier_rank.get(n["tier"], 9), n["label"]))
            edges = sorted(g["edges"].values(), key=lambda e: (e["from"], e["to"], e["type"]))
            return {"nodes": nodes, "edges": edges}

    def reset(self, session_id: str) -> None:
        with self._lock:
            self._graphs.pop(session_id, None)

    # ── 持久化到会话 metadata ─────────────────────────────────────────

    def snapshot(self, session_id: str) -> dict:
        """返回可持久化的图谱快照（含节点/边）"""
        return self.get_graph(session_id)

    def restore(self, session_id: str, data: dict) -> None:
        """从 metadata 快照恢复图谱"""
        if not data or not isinstance(data, dict):
            return
        with self._lock:
            g: dict = {"nodes": {}, "edges": {}}
            for n in data.get("nodes", []):
                if isinstance(n, dict) and n.get("id"):
                    g["nodes"][n["id"]] = n
            for e in data.get("edges", []):
                if isinstance(e, dict) and e.get("from") and e.get("to"):
                    key = f"{e['from']}|{e['to']}|{e.get('type', '')}"
                    g["edges"][key] = e
            self._graphs[session_id] = g


_store: Optional[SessionGraphStore] = None
_store_lock = threading.Lock()


def get_session_graph_store() -> SessionGraphStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SessionGraphStore()
    return _store
