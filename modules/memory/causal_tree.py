"""
CausalTree — 因果树遍历

根据因果图边关系虚拟构建树结构，支持：
- 上溯（trace_up）：从叶节点→根节点，输出完整原因链
- 下钻（trace_down）：从根节点→叶节点，输出完整结果链
- 横向对比（compare_lateral）：多棵树的同层对比，提取共享因子
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

logger = logging.getLogger(__name__)


@dataclass
class CausalChain:
    """一条完整因果链"""
    nodes: List[CausalNode] = field(default_factory=list)
    edges: List[CausalEdge] = field(default_factory=list)
    confidence: float = 0.0      # 整链平均置信度
    direction: str = "forward"   # forward / backward

    def summary(self, max_nodes: int = 5) -> str:
        labels = [n.label for n in self.nodes[:max_nodes]]
        return " → ".join(labels)


@dataclass
class CausalTreeResult:
    """深度回忆下钻的输出"""
    anchor: CausalNode
    chains: List[CausalChain] = field(default_factory=list)
    confidence: float = 0.0
    query_intent: str = ""       # trace / predict / generalize / counterfactual

    def format(self) -> str:
        lines = [f"【因果分析】锚点: {self.anchor.label}"]
        for i, chain in enumerate(self.chains, 1):
            direction = "后向" if chain.direction == "backward" else "前向"
            lines.append(f"  {direction}链路 {i} (置信度 {chain.confidence:.0%}): {chain.summary()}")
        return "\n".join(lines)


class CausalTree:
    """因果树遍历引擎"""

    def __init__(self, graph: CausalGraph = None):
        self._graph = graph or CausalGraph.get_instance()
        self._cache: Dict[str, List[CausalChain]] = {}

    def invalidate_cache(self, node_id: str = None):
        if node_id:
            self._cache.pop(node_id, None)
        else:
            self._cache.clear()

    def trace_up(
        self, node_id: str, max_depth: int = 5, min_confidence: float = 0.0,
    ) -> List[CausalChain]:
        """上溯：从锚点向上遍历父节点到根，输出完整原因链"""
        cache_key = f"up:{node_id}:{max_depth}:{min_confidence}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        chains: List[CausalChain] = []
        self._dfs_up(node_id, [], [], max_depth, min_confidence, chains, set())
        self._cache[cache_key] = chains
        return chains

    def _dfs_up(
        self, node_id: str,
        path_nodes: List[CausalNode],
        path_edges: List[CausalEdge],
        max_depth: int, min_confidence: float,
        results: List[CausalChain],
        visited: set,
    ):
        predecessors = self._graph.get_predecessors(node_id, min_confidence)
        if not predecessors or len(path_nodes) >= max_depth:
            if path_nodes:
                chain = CausalChain(
                    nodes=list(reversed(path_nodes)),
                    edges=list(reversed(path_edges)),
                    direction="backward",
                )
                chain.confidence = (
                    sum(e.confidence for e in chain.edges) / max(len(chain.edges), 1)
                ) if chain.edges else 0.0
                results.append(chain)
            return
        for pred in predecessors:
            if pred.id in visited:
                continue
            visited.add(pred.id)
            edges = self._graph._get_conn().execute(
                "SELECT * FROM edges WHERE from_id=? AND to_id=?", (pred.id, node_id),
            ).fetchall()
            edge_list = [CausalEdge.from_dict(dict(e)) for e in edges]
            path_nodes.append(pred)
            path_edges.extend(edge_list)
            self._dfs_up(pred.id, path_nodes, path_edges, max_depth, min_confidence, results, visited)
            path_nodes.pop()
            for _ in edge_list:
                path_edges.pop()
            visited.discard(pred.id)

    def trace_down(
        self, node_id: str, max_depth: int = 5, min_confidence: float = 0.0,
    ) -> List[CausalChain]:
        """下钻：从锚点向下遍历子节点到叶节点，输出完整结果链"""
        cache_key = f"down:{node_id}:{max_depth}:{min_confidence}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        chains: List[CausalChain] = []
        self._dfs_down(node_id, [], [], max_depth, min_confidence, chains, set())
        self._cache[cache_key] = chains
        return chains

    def _dfs_down(
        self, node_id: str,
        path_nodes: List[CausalNode],
        path_edges: List[CausalEdge],
        max_depth: int, min_confidence: float,
        results: List[CausalChain],
        visited: set,
    ):
        successors = self._graph.get_successors(node_id, min_confidence)
        if not successors or len(path_nodes) >= max_depth:
            if path_nodes:
                chain = CausalChain(
                    nodes=path_nodes[:],
                    edges=path_edges[:],
                    direction="forward",
                )
                chain.confidence = (
                    sum(e.confidence for e in chain.edges) / max(len(chain.edges), 1)
                ) if chain.edges else 0.0
                results.append(chain)
            return
        for succ in successors:
            if succ.id in visited:
                continue
            visited.add(succ.id)
            edges = self._graph._get_conn().execute(
                "SELECT * FROM edges WHERE from_id=? AND to_id=?", (node_id, succ.id),
            ).fetchall()
            edge_list = [CausalEdge.from_dict(dict(e)) for e in edges]
            path_nodes.append(succ)
            path_edges.extend(edge_list)
            self._dfs_down(succ.id, path_nodes, path_edges, max_depth, min_confidence, results, visited)
            path_nodes.pop()
            for _ in edge_list:
                path_edges.pop()
            visited.discard(succ.id)

    def compare_lateral(
        self, node_ids: List[str], max_depth: int = 3, min_confidence: float = 0.0,
    ) -> List[str]:
        """横向对比多棵树，提取共享因果因子"""
        chains_by_node: Dict[str, List[CausalChain]] = {}
        for nid in node_ids:
            up = self.trace_up(nid, max_depth, min_confidence)
            down = self.trace_down(nid, max_depth, min_confidence)
            chains_by_node[nid] = up + down

        shared_factors: List[str] = []
        if len(node_ids) < 2:
            return shared_factors

        base_node = node_ids[0]
        base_chains = chains_by_node[base_node]
        base_labels = set()
        for chain in base_chains:
            for node in chain.nodes:
                base_labels.add(node.label)

        for other_id in node_ids[1:]:
            other_chains = chains_by_node[other_id]
            other_labels = set()
            for chain in other_chains:
                for node in chain.nodes:
                    other_labels.add(node.label)
            common = base_labels & other_labels
            shared_factors.extend(common - {self._graph.get_node(other_id).label if self._graph.get_node(other_id) else ""})

        return list(set(shared_factors))
