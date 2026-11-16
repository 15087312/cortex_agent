"""工作流拓扑与执行状态流式投影 (Workflow State Projection)

将工作流图的当前节点、执行状态与节点耗时投影至 session_projections，
使前端能通过 session_projection 帧或刷新恢复实时渲染工作流拓扑图。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger(__name__)

PROJECTION_KEY_WORKFLOW = "workflow"


class WorkflowTopologyProjector:
    """工作流拓扑与状态投影器"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._node_states: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Dict[str, str]] = []
        self._active_node: Optional[str] = None

    def set_topology(self, nodes: List[str], edges: List[Dict[str, str]]) -> None:
        """注册静态图拓扑"""
        self._edges = edges
        for nid in nodes:
            if nid not in self._node_states:
                self._node_states[nid] = {
                    "node_id": nid,
                    "status": "idle",
                    "duration_ms": 0,
                    "updated_at": time.time(),
                }

    def on_node_start(self, node_id: str) -> Optional[Dict[str, Any]]:
        """节点开始执行"""
        self._active_node = node_id
        if node_id not in self._node_states:
            self._node_states[node_id] = {"node_id": node_id}
        self._node_states[node_id].update({
            "status": "running",
            "start_time": time.time(),
            "updated_at": time.time(),
        })
        return self._emit()

    def on_node_end(self, node_id: str, duration_ms: float = 0) -> Optional[Dict[str, Any]]:
        """节点正常完成"""
        if node_id in self._node_states:
            self._node_states[node_id].update({
                "status": "completed",
                "duration_ms": duration_ms,
                "updated_at": time.time(),
            })
        if self._active_node == node_id:
            self._active_node = None
        return self._emit()

    def on_node_suspended(self, node_id: str, delegation_id: str = "") -> Optional[Dict[str, Any]]:
        """节点因跨模型委托挂起"""
        if node_id in self._node_states:
            self._node_states[node_id].update({
                "status": "suspended",
                "delegation_id": delegation_id,
                "updated_at": time.time(),
            })
        return self._emit()

    def get_snapshot(self) -> Dict[str, Any]:
        """获取当前工作流状态快照"""
        return {
            "active_node": self._active_node,
            "nodes": self._node_states,
            "edges": self._edges,
            "timestamp": time.time(),
        }

    def _emit(self) -> Optional[Dict[str, Any]]:
        """触发 session_projection 发射"""
        try:
            from modules.thinking.projections import emit_projection
            snapshot = self.get_snapshot()
            return emit_projection(self.session_id, PROJECTION_KEY_WORKFLOW, snapshot)
        except Exception as e:
            logger.debug(f"[WorkflowProjection] 投影发射异常 (非致命): {e}")
            return None
