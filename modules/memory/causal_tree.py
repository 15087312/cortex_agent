"""
CausalTree — 因果树：两种推理能力

1. 图推理 (trace_up/trace_down)：沿因果边遍历，回答"为什么/导致什么"
2. 树推理 (expand_node)：展开节点的具体证据，回答"有什么依据"

因果图存抽象概念和关系，因果树提供两种查询视角。
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Set

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """缓存条目（带版本号）"""
    value: any
    version: int
    expires_at: float  # 过期时间戳


@dataclass
class CausalChain:
    """一条因果链（图推理用）"""
    nodes: List[CausalNode] = field(default_factory=list)
    edges: List[CausalEdge] = field(default_factory=list)
    confidence: float = 0.0
    direction: str = "forward"   # forward / backward

    def summary(self, max_nodes: int = 5) -> str:
        labels = [n.label for n in self.nodes[:max_nodes]]
        return " → ".join(labels)


@dataclass
class EvidenceItem:
    """一条证据事件"""
    event_id: str
    fact: str
    thought: str = ""
    lesson: str = ""
    importance: float = 0.5
    event_type: str = "fact"
    keywords: List[str] = field(default_factory=list)
    time: str = ""
    # 与节点的关系
    relation: str = ""  # supports / contradicts / illustrates


@dataclass
class EvidenceTree:
    """一个节点的证据树 — 抽象↔具体的桥梁

    node: 抽象节点（概念）
    evidence: 该节点支撑的所有具体事件
    parent_chain: 从根因到该节点的因果路径
    child_chains: 从该节点到结果的因果路径
    """
    node: CausalNode
    evidence: List[EvidenceItem] = field(default_factory=list)
    parent_chain: List[CausalNode] = field(default_factory=list)
    child_chains: List[List[CausalNode]] = field(default_factory=list)
    confidence: float = 0.0

    def format(self) -> str:
        lines = [f"【{self.node.label}】(置信度 {self.confidence:.0%})"]

        if self.parent_chain:
            labels = [n.label for n in self.parent_chain]
            lines.append(f"  原因链: {' ← '.join(labels)}")

        if self.evidence:
            lines.append(f"  证据 ({len(self.evidence)} 条):")
            for ev in self.evidence[:5]:
                lines.append(f"    · {ev.fact[:60]} (重要性 {ev.importance:.1f})")

        if self.child_chains:
            lines.append("  后果:")
            for chain in self.child_chains:
                labels = [n.label for n in chain]
                lines.append(f"    → {' → '.join(labels)}")

        return "\n".join(lines)


# 兼容旧代码的别名
CausalTreeResult = EvidenceTree


class CausalTree:
    """因果树：抽象节点 → 具体事件的桥梁"""

    def __init__(self, graph: CausalGraph = None):
        self._graph = graph or CausalGraph.get_instance()
        # 缓存：key → CacheEntry(value, version, expires_at)
        self._cache: Dict[str, CacheEntry] = {}
        self._cache_ttl = 300.0  # 5 分钟 TTL

    def _get_cache_key(self, method: str, **kwargs) -> str:
        """生成缓存 key"""
        parts = [method]
        for k, v in sorted(kwargs.items()):
            if isinstance(v, list):
                v = ",".join(str(x) for x in sorted(v))
            parts.append(f"{k}={v}")
        return ":".join(parts)

    def _get_cached(self, key: str, node_version: int) -> Optional[any]:
        """获取缓存（检查版本号和 TTL）"""
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() > entry.expires_at:
            self._cache.pop(key, None)
            return None
        if entry.version < node_version:
            # 节点已更新，缓存失效
            self._cache.pop(key, None)
            return None
        return entry.value

    def _set_cache(self, key: str, value: any, version: int):
        """设置缓存"""
        self._cache[key] = CacheEntry(
            value=value,
            version=version,
            expires_at=time.time() + self._cache_ttl,
        )

    def invalidate_cache(self, node_ids: Optional[Set[str]] = None):
        """使缓存失效
        
        Args:
            node_ids: 可选，指定节点 ID 列表；None 则清空所有缓存
        """
        if node_ids is None:
            self._cache.clear()
            logger.info("[CausalTree] 缓存已清空")
            return
        
        # 删除涉及指定节点的缓存条目
        to_delete = []
        for key in self._cache:
            for nid in node_ids:
                if nid in key:
                    to_delete.append(key)
                    break
        for key in to_delete:
            del self._cache[key]
        
        logger.info(f"[CausalTree] 缓存失效：{len(to_delete)} 条")

    def expand_node(self, node_id: str, max_evidence: int = 10) -> EvidenceTree:
        """展开一个节点：返回该节点的证据树

        核心方法：以节点为中心，收集所有支撑它的具体事件，
        同时构建上下游因果链。
        """
        node = self._graph.get_node(node_id)
        if not node:
            raise ValueError(f"节点 {node_id} 不存在")

        # 检查缓存（带版本校验）
        cache_key = self._get_cache_key("expand_node", node_id=node_id)
        cached = self._get_cached(cache_key, node.version)
        if cached:
            logger.debug(f"[CausalTree] 命中缓存：{node_id}")
            return cached

        # 收集支撑事件
        evidence = self._collect_evidence(node_id, max_evidence)

        # 构建上游因果链（原因方向）
        parent_chain = self._trace_to_root(node_id)

        # 构建下游因果链（结果方向）
        child_chains = self._trace_to_leaves(node_id)

        # 计算证据强度
        confidence = self._compute_evidence_confidence(node, evidence, parent_chain)

        result = EvidenceTree(
            node=node,
            evidence=evidence,
            parent_chain=parent_chain,
            child_chains=child_chains,
            confidence=confidence,
        )

        # 写入缓存
        self._set_cache(cache_key, result, node.version)
        return result

    def _collect_evidence(self, node_id: str, limit: int) -> List[EvidenceItem]:
        """收集节点关联的所有具体事件"""
        from modules.memory.event_store import EventStore
        store = EventStore.get_instance()

        all_events = store.list_events(limit=500)
        evidence = []
        for ev in all_events:
            if node_id in (ev.causal_node_ids or []):
                evidence.append(EvidenceItem(
                    event_id=ev.id,
                    fact=ev.fact,
                    thought=ev.thought,
                    lesson=ev.lesson,
                    importance=ev.importance,
                    event_type=ev.type,
                    keywords=ev.keywords,
                    time=ev.time,
                ))

        # 按重要性排序
        evidence.sort(key=lambda e: e.importance, reverse=True)
        return evidence[:limit]

    def _trace_to_root(self, node_id: str, max_depth: int = 5) -> List[CausalNode]:
        """从节点向上溯源到根因"""
        path = []
        visited = set()
        current = node_id

        for _ in range(max_depth):
            if current in visited:
                break
            visited.add(current)

            predecessors = self._graph.get_predecessors(current)
            if not predecessors:
                break

            # 选置信度最高的前驱
            best = max(predecessors, key=lambda p: p.confidence)
            path.append(best)
            current = best.id

        path.reverse()
        return path

    def _trace_to_leaves(self, node_id: str, max_depth: int = 5) -> List[List[CausalNode]]:
        """从节点向下追踪到叶节点，返回多条因果链"""
        chains = []
        self._dfs_down(node_id, [], chains, 0, max_depth)
        # 去重：相同终点只保留最短路径
        seen = {}
        for chain in chains:
            key = chain[-1].id if chain else None
            if key and (key not in seen or len(chain) < len(seen[key])):
                seen[key] = chain
        return list(seen.values())

    def _dfs_down(self, node_id: str, path: List[CausalNode],
                  results: List[List[CausalNode]], depth: int, max_depth: int):
        """深度优先遍历下游（用 path 做环路检测，不污染兄弟分支）"""
        if depth >= max_depth:
            return

        successors = self._graph.get_successors(node_id)
        if not successors:
            if path:
                results.append(list(path))
            return

        for succ in successors:
            # 环路检测：只看当前路径，不看兄弟分支
            if any(n.id == succ.id for n in path):
                continue
            path.append(succ)
            self._dfs_down(succ.id, path, results, depth + 1, max_depth)
            path.pop()

    def _compute_evidence_confidence(self, node: CausalNode,
                                     evidence: List[EvidenceItem],
                                     parent_chain: List[CausalNode]) -> float:
        """计算证据强度：节点置信度 × 事件数量权重 × 因果链深度"""
        base = node.confidence
        evidence_factor = min(1.0, len(evidence) / 5.0)  # 5条证据=满分
        chain_factor = min(1.0, len(parent_chain) / 3.0)  # 3层深度=满分
        return min(0.99, base * 0.4 + evidence_factor * 0.3 + chain_factor * 0.3)

    # ── 图推理：沿因果边遍历 ──

    def trace_up(
        self,
        node_id: str,
        max_depth: int = 5,
        min_confidence: float = 0.0,
        time_window: Optional[str] = None,
    ) -> List[CausalChain]:
        """图推理溯源：从锚点沿边向根因方向遍历，返回因果链
        
        Args:
            node_id: 锚点节点 ID
            max_depth: 最大深度
            min_confidence: 最小边置信度
            time_window: 可选，时间窗口（如"30d", "7d"）— 只遍历该时间范围内的边
        """
        # 检查缓存
        cache_key = self._get_cache_key("trace_up", node_id=node_id, max_depth=max_depth, time_window=time_window)
        node = self._graph.get_node(node_id)
        if node:
            cached = self._get_cached(cache_key, node.version)
            if cached:
                logger.debug(f"[CausalTree] 命中 trace_up 缓存：{node_id}")
                return cached

        chains = []
        self._dfs_up(node_id, [], [], max_depth, min_confidence, chains, set(), time_window)
        
        # 写入缓存
        if node:
            self._set_cache(cache_key, chains, node.version)
        return chains

    def trace_down(
        self,
        node_id: str,
        max_depth: int = 5,
        min_confidence: float = 0.0,
        time_window: Optional[str] = None,
    ) -> List[CausalChain]:
        """图推理预测：从锚点沿边向结果方向遍历，返回因果链
        
        Args:
            node_id: 锚点节点 ID
            max_depth: 最大深度
            min_confidence: 最小边置信度
            time_window: 可选，时间窗口（如"30d", "7d"）— 只遍历该时间范围内的边
        """
        # 检查缓存
        cache_key = self._get_cache_key("trace_down", node_id=node_id, max_depth=max_depth, time_window=time_window)
        node = self._graph.get_node(node_id)
        if node:
            cached = self._get_cached(cache_key, node.version)
            if cached:
                logger.debug(f"[CausalTree] 命中 trace_down 缓存：{node_id}")
                return cached

        chains = []
        self._dfs_down_legacy(node_id, [], [], max_depth, min_confidence, chains, set(), time_window)
        
        # 写入缓存
        if node:
            self._set_cache(cache_key, chains, node.version)
        return chains

    def _dfs_up(self, node_id, path_nodes, path_edges, max_depth, min_confidence, results, visited, time_window=None):
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
            elif not path_nodes:
                node = self._graph.get_node(node_id)
                if node:
                    results.append(CausalChain(nodes=[node], direction="backward", confidence=node.confidence))
            return
        for pred in predecessors:
            if pred.id in visited:
                continue
            visited.add(pred.id)
            # 时间窗口过滤
            if time_window:
                import re
                match = re.match(r'(\d+)(d|h|m)', time_window)
                if match:
                    val, unit = int(match.group(1)), match.group(2)
                    seconds = val * (3600 * 24 if unit == 'd' else (3600 if unit == 'h' else 60))
                    time_cond = f" AND created_at >= datetime('now', '-{seconds} seconds')"
                else:
                    time_cond = ""
            else:
                time_cond = ""
            
            query = f"SELECT * FROM edges WHERE from_id=? AND to_id=?{time_cond}"
            edges = self._graph._get_conn().execute(query, (pred.id, node_id)).fetchall()
            edge_list = [CausalEdge.from_dict(dict(e)) for e in edges]
            path_nodes.append(pred)
            path_edges.extend(edge_list)
            self._dfs_up(pred.id, path_nodes, path_edges, max_depth, min_confidence, results, visited, time_window)
            path_nodes.pop()
            for _ in edge_list:
                path_edges.pop()
            visited.discard(pred.id)

    def _dfs_down_legacy(self, node_id, path_nodes, path_edges, max_depth, min_confidence, results, visited, time_window=None):
        successors = self._graph.get_successors(node_id, min_confidence)
        if not successors or len(path_nodes) >= max_depth:
            if path_nodes:
                chain = CausalChain(
                    nodes=path_nodes[:], edges=path_edges[:], direction="forward",
                )
                chain.confidence = (
                    sum(e.confidence for e in chain.edges) / max(len(chain.edges), 1)
                ) if chain.edges else 0.0
                results.append(chain)
            elif not path_nodes:
                node = self._graph.get_node(node_id)
                if node:
                    results.append(CausalChain(nodes=[node], direction="forward", confidence=node.confidence))
            return
        for succ in successors:
            if succ.id in visited:
                continue
            visited.add(succ.id)
            # 时间窗口过滤
            if time_window:
                import re
                match = re.match(r'(\d+)(d|h|m)', time_window)
                if match:
                    val, unit = int(match.group(1)), match.group(2)
                    seconds = val * (3600 * 24 if unit == 'd' else (3600 if unit == 'h' else 60))
                    time_cond = f" AND created_at >= datetime('now', '-{seconds} seconds')"
                else:
                    time_cond = ""
            else:
                time_cond = ""
            
            query = f"SELECT * FROM edges WHERE from_id=? AND to_id=?{time_cond}"
            edges = self._graph._get_conn().execute(query, (node_id, succ.id)).fetchall()
            edge_list = [CausalEdge.from_dict(dict(e)) for e in edges]
            path_nodes.append(succ)
            path_edges.extend(edge_list)
            self._dfs_down_legacy(succ.id, path_nodes, path_edges, max_depth, min_confidence, results, visited, time_window)
            path_nodes.pop()
            for _ in edge_list:
                path_edges.pop()
            visited.discard(succ.id)

    def what_if(
        self,
        node_id: str,
        hypothetical_edge: Optional[CausalEdge] = None,
        max_depth: int = 5,
        min_confidence: float = 0.0,
        time_window: Optional[str] = None,
    ) -> List[CausalChain]:
        """反事实推理：假设新边存在时，从锚点向结果方向遍历
        
        Args:
            node_id: 锚点节点 ID
            hypothetical_edge: 假设的新边（None 则只遍历现有边）
            max_depth: 最大深度
            min_confidence: 最小边置信度
            time_window: 可选，时间窗口（如"30d", "7d"）
        
        Returns:
            因果链列表（包含假设边的影响）
        """
        # 检查缓存
        cache_key = self._get_cache_key(
            "what_if",
            node_id=node_id,
            hypothetical_edge=hypothetical_edge.to_dict() if hypothetical_edge else None,
            max_depth=max_depth,
            time_window=time_window,
        )
        node = self._graph.get_node(node_id)
        if node:
            cached = self._get_cached(cache_key, node.version)
            if cached:
                logger.debug(f"[CausalTree] 命中 what_if 缓存：{node_id}")
                return cached

        chains = []
        # 先处理假设边（起点在锚点）
        if hypothetical_edge and hypothetical_edge.from_id == node_id:
            target = self._graph.get_node(hypothetical_edge.to_id)
            if target:
                chains.append(CausalChain(
                    nodes=[node, target],
                    edges=[hypothetical_edge],
                    direction="forward",
                    confidence=hypothetical_edge.confidence,
                ))
                # 从目标节点继续向下遍历
                sub_chains = self.trace_down(
                    hypothetical_edge.to_id,
                    max_depth=max_depth - 1,
                    min_confidence=min_confidence,
                    time_window=time_window,
                )
                for sc in sub_chains:
                    sc.nodes.insert(0, node)
                    sc.edges.insert(0, hypothetical_edge)
                    chains.append(sc)

        # 处理锚点的现有下游边
        existing = self.trace_down(
            node_id,
            max_depth=max_depth,
            min_confidence=min_confidence,
            time_window=time_window,
        )
        chains.extend(existing)

        # 按置信度排序去重
        seen = set()
        unique_chains = []
        for c in chains:
            key = tuple(n.id for n in c.nodes)
            if key not in seen:
                seen.add(key)
                unique_chains.append(c)
        unique_chains.sort(key=lambda c: c.confidence, reverse=True)

        # 写入缓存
        if node:
            self._set_cache(cache_key, chains, node.version)
        return chains

    def _dfs_what_if(
        self,
        node_id,
        path_nodes,
        path_edges,
        max_depth,
        min_confidence,
        results,
        visited,
        time_window=None,
        hypothetical_edge: Optional[CausalEdge] = None,
    ):
        # 首先处理假设边
        if hypothetical_edge and hypothetical_edge.from_id == node_id:
            # 如果假设边的起点是当前节点，则直接添加
            path_nodes.append(self._graph.get_node(hypothetical_edge.to_id))
            path_edges.append(hypothetical_edge)
            self._dfs_down_legacy(
                hypothetical_edge.to_id,
                path_nodes,
                path_edges,
                max_depth - 1,
                min_confidence,
                results,
                visited,
                time_window,
            )
            path_nodes.pop()
            path_edges.pop()

        # 然后处理现有边
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
            elif not path_nodes:
                node = self._graph.get_node(node_id)
                if node:
                    results.append(CausalChain(nodes=[node], direction="forward", confidence=node.confidence))
            return
        for succ in successors:
            if succ.id in visited:
                continue
            visited.add(succ.id)
            # 时间窗口过滤
            if time_window:
                import re
                match = re.match(r'(\d+)(d|h|m)', time_window)
                if match:
                    val, unit = int(match.group(1)), match.group(2)
                    seconds = val * (3600 * 24 if unit == 'd' else (3600 if unit == 'h' else 60))
                    time_cond = f" AND created_at >= datetime('now', '-{seconds} seconds')"
                else:
                    time_cond = ""
            else:
                time_cond = ""
            
            query = f"SELECT * FROM edges WHERE from_id=? AND to_id=?{time_cond}"
            edges = self._graph._get_conn().execute(query, (node_id, succ.id)).fetchall()
            edge_list = [CausalEdge.from_dict(dict(e)) for e in edges]
            path_nodes.append(succ)
            path_edges.extend(edge_list)
            self._dfs_what_if(
                succ.id,
                path_nodes,
                path_edges,
                max_depth,
                min_confidence,
                results,
                visited,
                time_window,
                hypothetical_edge,
            )
            path_nodes.pop()
            for _ in edge_list:
                path_edges.pop()
            visited.discard(succ.id)

    def compare_lateral(self, node_ids: List[str], max_depth: int = 3, min_confidence: float = 0.0) -> List[str]:
        """横向对比：找多条链路的共享因子"""
        all_labels = set()
        for nid in node_ids:
            chains = self.trace_up(nid, max_depth, min_confidence)
            for c in chains:
                for n in c.nodes:
                    all_labels.add(n.label)
        return list(all_labels)
