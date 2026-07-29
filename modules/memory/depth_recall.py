"""
DepthRecall — 深度回忆调度模块

三步闭环：
  1. 因果图定位 + 邻域扩散（划清逻辑边界）
  2. 因果树下钻（拆解完整链路）
  3. 事件池召回 + 复合排序（精准捞取实例）

触发条件（满足其一）:
  - 用户查询含逻辑诉求词（为什么/原因/后果/规律/如果当时）
  - 浅层召回置信度低于阈值
  - 当前任务为决策/分析类
"""
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
from modules.memory.causal_tree import CausalTree, CausalChain, CausalTreeResult
from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.event_retrieval import EventRetrieval, SCORE_WEIGHTS

from utils.logger import get_logger
logger = get_logger(__name__)

# ── 触发词 ──
_TRIGGER_PATTERNS = [
    r"为什么", r"原因", r"后果", r"结果", r"规律",
    r"类似情况", r"如果当时", r"假如", r"否则",
    r"how it (happened|came about)",
    r"what (caused|led to|triggered)",
    r"what (if|would happen)",
    r"root cause", r"pattern",
]

# ── 查询意图分类 ──
_INTENT_PATTERNS: Dict[str, List[str]] = {
    "trace":     [r"为什么", r"原因", r"root cause", r"溯源", r"起因", r"导致", r"造成"],
    "predict":   [r"后果", r"会导致", r"结果", r"predict", r"forecast", r"风险", r"影响"],
    "generalize":[r"规律", r"pattern", r"类似情况", r"归纳", r"共同点", r"总结"],
    "counterfactual": [r"如果当时", r"假如", r"what if", r"otherwise"],
    "analyze":   [r"分析", r"根因", r"诊断", r"排查", r"调试", r"debug", r"定位", r"怎么解决"],
    "optimize":  [r"优化", r"改进", r"提升", r"加速", r"性能", r"效率", r"怎么.*好"],
    "evaluate":  [r"评估", r"比较", r"对比", r"优劣", r"哪个更好"],
}

# ── 限流常量（从 settings 读取，支持运行时修改）──
from config.settings import settings as _settings

def _get_max_anchors() -> int:
    return getattr(_settings, "CAUSAL_MAX_ANCHORS", 3)

def _get_max_neighbors() -> int:
    return getattr(_settings, "CAUSAL_MAX_NEIGHBORS_PER_HOP", 10)

def _get_max_tree_depth() -> int:
    return getattr(_settings, "CAUSAL_MAX_TREE_DEPTH", 4)

def _get_max_events_recall() -> int:
    return getattr(_settings, "CAUSAL_MAX_EVENTS_RECALL", 30)

def _get_min_confidence() -> float:
    return getattr(_settings, "CAUSAL_MIN_CONFIDENCE", 0.2)

def _get_hot_cache_ttl() -> float:
    return float(getattr(_settings, "CAUSAL_HOT_CACHE_TTL", 300))

def _get_confidence_boost_delta() -> float:
    return float(getattr(_settings, "CAUSAL_CONFIDENCE_BOOST_DELTA", 0.05))

def _get_confidence_max() -> float:
    return float(getattr(_settings, "CAUSAL_CONFIDENCE_MAX", 0.99))


_intent_cache: Dict[str, Tuple[str, float]] = {}
_intent_cache_ttl: float = 60.0


def classify_intent(query: str) -> str:
    """判断查询意图: trace / predict / generalize / counterfactual / shallow"""
    now = time.time()
    cached = _intent_cache.get(query)
    if cached:
        intent, ts = cached
        if now - ts < _intent_cache_ttl:
            return intent
    q = query.lower()
    for intent, patterns in _INTENT_PATTERNS.items():
        for p in patterns:
            if re.search(p, q):
                _intent_cache[query] = (intent, now)
                return intent
    _intent_cache[query] = ("shallow", now)
    return "shallow"


def should_trigger_deep_recall(
    query: str,
    shallow_confidence: Optional[float] = None,
    task_type: str = "",
) -> Tuple[bool, str]:
    """判断是否触发深度回忆

    Returns:
        (trigger, reason)
    """
    if classify_intent(query) != "shallow":
        return True, "query_contains_logic_words"
    if shallow_confidence is not None and shallow_confidence < 0.3:
        return True, "shallow_recall_low_confidence"
    if task_type in ("decision", "analysis", "planning"):
        return True, "decision_task"
    return False, ""


@dataclass
class DeepRecallResult:
    """深度回忆输出"""

    # ── 基础信息 ──
    anchor: Optional[CausalNode] = None
    intent: str = ""
    confidence: float = 0.0

    # ── 因果结论 ──
    causal_chains: List[CausalChain] = field(default_factory=list)
    causal_conclusion: str = ""
    shared_factors: List[str] = field(default_factory=list)

    # ── 事件 ──
    supporting_events: List[MemoryEvent] = field(default_factory=list)
    counter_examples: List[MemoryEvent] = field(default_factory=list)

    # ── 状态 ──
    success: bool = False
    fallback: bool = False       # 是否回退到浅层检索
    error: str = ""

    def format(self, max_events: int = 5) -> str:
        if self.fallback or not self.success:
            return ""
        lines: List[str] = []

        if self.causal_conclusion:
            lines.append(f"【因果结论】{self.causal_conclusion}")

        if self.shared_factors:
            lines.append(f"【共享因子】{'、'.join(self.shared_factors)}")

        for i, chain in enumerate(self.causal_chains, 1):
            direction = "因果链" if chain.direction == "forward" else "溯源链"
            labels = [n.label for n in chain.nodes]
            lines.append(f"  {direction} {i}: {' → '.join(labels)} (置信度 {chain.confidence:.0%})")

        if self.supporting_events:
            lines.append(f"【佐证事件】")
            for ev in self.supporting_events[:max_events]:
                lines.append(f"  · {ev.fact} (重要性 {ev.importance:.0%})")

        if self.counter_examples:
            lines.append(f"【反例 / 例外】")
            for ev in self.counter_examples[:3]:
                lines.append(f"  · {ev.fact}")

        return "\n".join(lines)


class DepthRecallScheduler:
    """深度回忆调度器"""

    def __init__(
        self,
        graph: CausalGraph = None,
        tree: CausalTree = None,
        store: EventStore = None,
        retrieval: EventRetrieval = None,
    ):
        self._graph = graph or CausalGraph.get_instance()
        self._tree = tree or CausalTree(self._graph)
        self._store = store or EventStore.get_instance()
        self._retrieval = retrieval or EventRetrieval.get_instance()

        # 缓存热门因果树结果
        self._hot_cache: Dict[str, Tuple[DeepRecallResult, float]] = {}
        self._hot_cache_ttl = _get_hot_cache_ttl()

        # 增量更新统计（供日志）
        self._update_stats: Dict[str, int] = {"linked": 0, "boosted": 0}

        # 边置信度提升的衰减因子：每次 recall 后每条边最多 + δ
        self._confidence_boost_delta = _get_confidence_boost_delta()
        self._confidence_max = _get_confidence_max()

    # ── 主入口 ──

    async def deep_recall(
        self,
        query: str,
        max_results: int = 10,
        depth_level: int = 1,      # 1=标准, 2=深度
        min_confidence: float = 0.2,
        task_type: str = "",
    ) -> DeepRecallResult:
        """执行深度回忆（完整的三步闭环）"""
        logger.info(f"[DepthRecall] query='{query}' depth={depth_level}")

        # 热缓存
        cache_key = f"{query}:{depth_level}"
        if cache_key in self._hot_cache:
            result, ts = self._hot_cache[cache_key]
            if time.time() - ts < self._hot_cache_ttl:
                logger.debug(f"[DepthRecall] 命中热缓存")
                return result

        result = DeepRecallResult(intent=classify_intent(query))
        hops = 2 if depth_level >= 2 else 1

        # Step 1: 因果图定位（限流：最多 _get_max_anchors() 个锚点）
        anchors = self._graph.find_anchor_nodes(query, top_k=_get_max_anchors())
        if not anchors:
            logger.info("[DepthRecall] 未找到锚点节点，回退到浅层检索")
            return self._fallback(query, max_results, "no_anchor_nodes")

        result.anchor = anchors[0][0]
        result.confidence = anchors[0][1]
        logger.info(f"[DepthRecall] 锚点: {result.anchor.label} (置信度 {result.confidence})")

        # 按意图定向扩散（限流：每跳最多 _get_max_neighbors()）
        intent = result.intent
        neighbor_nodes: List[CausalNode] = []
        if intent == "trace":
            neighbor_nodes = self._graph.get_predecessors(
                result.anchor.id, min_confidence,
            )[:_get_max_neighbors()]
        elif intent == "predict":
            neighbor_nodes = self._graph.get_successors(
                result.anchor.id, min_confidence,
            )[:_get_max_neighbors()]
        else:
            neighbors = self._graph.get_neighbors(
                result.anchor.id, hops=hops, min_confidence=min_confidence,
            )
            neighbor_nodes = [n for n, _, _ in neighbors][:_get_max_neighbors()]

        all_anchor_ids = [result.anchor.id] + [n.id for n in neighbor_nodes]

        # Step 2: 因果树下钻（限流：最大深度 _get_max_tree_depth()）
        chains: List[CausalChain] = []
        if intent == "trace":
            for nid in all_anchor_ids:
                chains.extend(self._tree.trace_up(nid, max_depth=_get_max_tree_depth(), min_confidence=min_confidence))
        elif intent == "predict":
            for nid in all_anchor_ids:
                chains.extend(self._tree.trace_down(nid, max_depth=_get_max_tree_depth(), min_confidence=min_confidence))
        elif intent == "generalize":
            for nid in all_anchor_ids:
                chains.extend(self._tree.trace_up(nid, max_depth=_get_max_tree_depth(), min_confidence=min_confidence))
                chains.extend(self._tree.trace_down(nid, max_depth=_get_max_tree_depth(), min_confidence=min_confidence))
            result.shared_factors = self._tree.compare_lateral(
                all_anchor_ids, max_depth=_get_max_tree_depth(), min_confidence=min_confidence,
            )
        else:
            for nid in all_anchor_ids:
                chains.extend(self._tree.trace_up(nid, max_depth=_get_max_tree_depth(), min_confidence=min_confidence))
                chains.extend(self._tree.trace_down(nid, max_depth=5, min_confidence=min_confidence))

        chains.sort(key=lambda c: c.confidence, reverse=True)
        result.causal_chains = chains[:6]

        if not chains:
            logger.info("[DepthRecall] 未找到因果链，回退")
            return self._fallback(query, max_results, "no_causal_chains")

        # Step 3: 事件池召回（限流：最多 _get_max_events_recall() 条）
        actual_max = min(max_results, _get_max_events_recall())
        supporting, counter = await self._recall_events(
            query, chains, neighbor_nodes, actual_max, intent,
        )
        result.supporting_events = supporting
        result.counter_examples = counter

        result.causal_conclusion = self._build_conclusion(chains, result.shared_factors)
        result.success = True

        # 增量更新：关联事件→节点、上调边置信度
        involved_node_ids = {result.anchor.id} | {n.id for n in neighbor_nodes}
        for chain in chains:
            for node in chain.nodes:
                involved_node_ids.add(node.id)
        self._incremental_update(result, involved_node_ids)

        # 缓存
        self._hot_cache[cache_key] = (result, time.time())

        logger.info(
            f"[DepthRecall] 完成: {len(chains)} 因果链, {len(supporting)} 佐证事件"
            f" (+{self._update_stats.get('linked', 0)} 事件关联, "
            f"+{self._update_stats.get('boosted', 0)} 边置信度提升)"
        )
        return result

    # ── 事件召回 ──

    # ── 动态权重模板（按意图调整）──
    _WEIGHT_TEMPLATES = {
        "trace":            {"semantic": 0.20, "causal": 0.45, "importance": 0.20, "time": 0.15},
        "predict":          {"semantic": 0.20, "causal": 0.45, "importance": 0.20, "time": 0.15},
        "generalize":       {"semantic": 0.30, "causal": 0.30, "importance": 0.25, "time": 0.15},
        "counterfactual":   {"semantic": 0.25, "causal": 0.40, "importance": 0.20, "time": 0.15},
        "default":          {"semantic": 0.30, "causal": 0.35, "importance": 0.20, "time": 0.15},
    }

    async def _recall_events(
        self,
        query: str,
        chains: List[CausalChain],
        neighbor_nodes: List[CausalNode],
        max_results: int,
        intent: str = "default",
    ) -> Tuple[List[MemoryEvent], List[MemoryEvent]]:
        """用因果链约束召回事件，区分佐证与反例"""
        # 收集因果节点 ID 集合
        causal_node_ids: set = set()
        for chain in chains:
            for node in chain.nodes:
                causal_node_ids.add(node.id)
        for node in neighbor_nodes:
            causal_node_ids.add(node.id)

        if not causal_node_ids:
            return [], []

        # 通过 EventStore 按标签/关键词搜索关联事件
        node_labels = []
        for nid in causal_node_ids:
            node = self._graph.get_node(nid)
            if node:
                node_labels.extend(node.keywords)
                node_labels.append(node.label)

        store = self._store
        keyword_events = store.search_by_keywords(list(set(node_labels)), limit=max_results * 3)

        semantic_events: List[MemoryEvent] = []
        try:
            semantic_events = await self._retrieval.retrieve(
                query, max_results=max_results * 3, threshold=0.0,
            )
        except Exception as e:
            logger.warning(f"[DepthRecall] 语义检索失败: {e}")

        merged: Dict[str, Tuple[MemoryEvent, float, bool]] = {}
        for ev in keyword_events:
            merged[ev.id] = (ev, self._causal_relevance(ev, causal_node_ids), False)
        for ev in semantic_events:
            causal_score = self._causal_relevance(ev, causal_node_ids)
            if ev.id in merged:
                old_causal, old_is_counter = merged[ev.id][1], merged[ev.id][2]
                merged[ev.id] = (ev, max(old_causal, causal_score), old_is_counter)
            else:
                merged[ev.id] = (ev, causal_score, False)

        scored: List[Tuple[MemoryEvent, float, bool]] = []
        for ev_id, (ev, causal_rel, is_counter) in merged.items():
            semantic = 0.0
            for se in semantic_events:
                if se.id == ev_id:
                    semantic = 0.5
                    break
            # 动态权重打分（按意图调整各维度权重）
            w = self._WEIGHT_TEMPLATES.get(intent, self._WEIGHT_TEMPLATES["default"])
            final_score = (
                w["semantic"]   * semantic +
                w["causal"]     * causal_rel +
                w["importance"] * ev.importance +
                w["time"]       * self._time_decay(ev.time)
            )
            # 因果关联低且语义低则视为反例候选
            is_counter = causal_rel < 0.2 and semantic < 0.2 and ev.importance < 0.4
            scored.append((ev, final_score, is_counter))

        scored.sort(key=lambda x: x[1], reverse=True)
        supporting = [ev for ev, _, is_c in scored if not is_c][:max_results]
        counter = [ev for ev, _, is_c in scored if is_c][:3]

        return supporting, counter

    def _causal_relevance(self, event: MemoryEvent, causal_node_ids: set) -> float:
        """计算事件与因果节点集合的关联度

        基于:
        - 事件已关联的 causal_node_ids 直接命中率（最高 1.0）
        - 事件向量与节点向量的余弦相似度（向量匹配）
        - 事件文本关键词与节点 label/keywords 的匹配率（兜底）
        """
        # 直接命中：事件已显式关联到这些因果节点
        if causal_node_ids and event.causal_node_ids:
            direct_hits = len(set(event.causal_node_ids) & causal_node_ids)
            if direct_hits > 0:
                return min(1.0, 0.4 + 0.6 * direct_hits / max(len(causal_node_ids), 1))

        # 向量匹配：计算事件向量与节点向量的余弦相似度
        try:
            from modules.memory.embedding import EmbeddingEngine
            eng = EmbeddingEngine.get_instance()
            if eng._loaded:
                event_text = f"{event.fact} {event.thought} {event.lesson}"
                event_vec = eng.embed(event_text)
                if event_vec:
                    max_sim = 0.0
                    for nid in causal_node_ids:
                        node = CausalGraph.get_instance().get_node(nid)
                        if node:
                            node_text = f"{node.label} {' '.join(node.keywords)}"
                            node_vec = eng.embed(node_text)
                            if node_vec:
                                # 余弦相似度（向量已归一化，直接点积）
                                sim = sum(a * b for a, b in zip(event_vec, node_vec))
                                max_sim = max(max_sim, sim)
                    if max_sim > 0:
                        return min(1.0, max_sim)
        except Exception:
            pass

        # 文本关键词匹配（兜底）
        text = f"{event.fact} {event.thought} {event.lesson} {' '.join(event.keywords)}".lower()
        if not text:
            return 0.0

        all_labels = set()
        for nid in causal_node_ids:
            node = CausalGraph.get_instance().get_node(nid)
            if node:
                all_labels.add(node.label.lower())
                all_labels.update(k.lower() for k in node.keywords)

        if not all_labels:
            return 0.0

        hits = sum(1 for label in all_labels if label in text)
        return min(1.0, hits / max(len(all_labels), 1) * 2.0)

    @staticmethod
    def _time_decay(iso_time: str) -> float:
        import math
        from datetime import datetime, timezone
        try:
            if not iso_time:
                return 0.5
            t = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
            days = max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 86400)
            return math.exp(-0.01 * days)
        except (ValueError, TypeError):
            return 0.5

    # ── 结论构建 ──

    @staticmethod
    def _build_conclusion(chains: List[CausalChain], shared_factors: List[str]) -> str:
        if not chains:
            return ""
        top = chains[0]
        labels = [n.label for n in top.nodes]
        arrow = " → " if top.direction == "forward" else " ← "
        conclusion = f"发现 {len(chains)} 条因果链，核心链路: {arrow.join(labels)}"
        if shared_factors:
            conclusion += f"，共享因子: {'、'.join(shared_factors)}"
        return conclusion

    # ── 增量更新闭环 ──

    def _incremental_update(self, result: DeepRecallResult, node_ids: set):
        """每次深度回忆成功后执行增量更新

        1. 将佐证事件关联到因果节点（写 causal_node_ids）
        2. 提升召回链路上各边的置信度
        """
        self._update_stats = {"linked": 0, "boosted": 0}

        # 1. 把佐证事件挂载到因果节点
        for ev in result.supporting_events:
            current_ids = set(ev.causal_node_ids or [])
            new_ids = current_ids | node_ids
            if new_ids != current_ids:
                ev.causal_node_ids = list(new_ids)
                self._store.save_event(ev)
                self._update_stats["linked"] += 1

                # 同步更新节点上的 event_count
                for nid in node_ids:
                    node = self._graph.get_node(nid)
                    if node:
                        has_event_already = nid in current_ids
                        if not has_event_already:
                            node.event_count += 1
                        node.confidence = min(0.99, node.confidence + 0.02)
                        self._graph.save_node(node)

        # 2. 提升召回链路上各边的置信度
        seen_edges: set = set()
        for chain in result.causal_chains:
            for edge in chain.edges:
                if edge.id in seen_edges:
                    continue
                seen_edges.add(edge.id)
                existing = self._graph.get_edge(edge.id)
                if existing:
                    new_conf = min(
                        self._confidence_max,
                        existing.confidence + self._confidence_boost_delta,
                    )
                    if new_conf > existing.confidence:
                        existing.confidence = new_conf
                        self._graph.save_edge(existing)
                        self._update_stats["boosted"] += 1

        # 3. 提升锚点节点自身的置信度
        if result.anchor:
            node = self._graph.get_node(result.anchor.id)
            if node:
                node.confidence = min(0.99, node.confidence + 0.01)
                self._graph.save_node(node)

        # 4. 如果有共享因子，尝试补全新节点（高阶：检查是否已有节点，没有则创建）
        for factor in result.shared_factors:
            existing = self._graph.find_nodes_by_label(factor)
            if not existing:
                new_node = CausalNode(
                    label=factor,
                    node_type="condition",
                    description=f"从深度回忆中发现的共享因果因子",
                    keywords=[factor],
                    importance=0.5,
                    confidence=0.3,
                )
                self._graph.save_node(new_node)
                logger.info(f"[DepthRecall] 自动创建新因果节点: {factor} ({new_node.id})")

    # ── 回退 ──

    def _fallback(self, query: str, max_results: int, reason: str) -> DeepRecallResult:
        result = DeepRecallResult(
            success=False, fallback=True, error=reason, intent="shallow",
        )
        logger.info(f"[DepthRecall] 回退到浅层检索: {reason}")
        return result

    # ── 工具 ──

    def invalidate_cache(self, query: str = None):
        if query:
            self._hot_cache.pop(f"{query}:1", None)
            self._hot_cache.pop(f"{query}:2", None)
        else:
            self._hot_cache.clear()
        self._tree.invalidate_cache()
