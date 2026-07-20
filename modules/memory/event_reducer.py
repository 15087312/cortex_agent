"""
EventReducer — 会话结束 → LLM 总结 → 生成 MemoryEvent

职责：
1. 接收完整对话文本
2. 调用 LLM 分析并生成 1~3 个 MemoryEvent（fact/thought/lesson/keywords/importance）
3. 自动向量化并存入 EventStore
"""
import json
import threading
from typing import List, Optional

from modules.memory.event_store import EventStore, MemoryEvent
from modules.memory.embedding import EmbeddingEngine
from utils.logger import setup_logger

logger = setup_logger("event_reducer")

# ── 离散重要性等级（LLM 分类比回归可靠）──
_IMPORTANCE_MAP = {
    "critical": 1.0,
    "high":    0.70,
    "medium":  0.40,
    "low":     0.15,
    "trivial": 0.03,
}


def _parse_importance(value) -> float:
    """解析 importance 字段：兼容离散等级和旧浮点数"""
    if isinstance(value, str):
        return _IMPORTANCE_MAP.get(value.strip().lower(), 0.40)
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return 0.40


# LLM 提示词：将一段对话提炼为结构化记忆事件 + 因果关系
REDUCE_PROMPT_TEMPLATE = """你是一个记忆分析专家。请分析以下对话，提炼出记忆事件和因果关系。

请以 JSON 对象格式输出，包含两个字段：

1. events: 记忆事件数组（1-3 个），每个事件包含：
   - fact: 发生了什么（客观描述，20-80 字）
   - thought: 你的思考和分析（20-100 字）
   - lesson: 学到了什么，可复用的经验教训（10-60 字）
   - keywords: 关键词列表（2-6 个，用于检索匹配）
   - importance: 重要性（critical / high / medium / low / trivial）
   - type: 事件类型（emotion | thought | fact | strategy）

2. causal_nodes: 因果节点数组（从对话中识别的概念/事件/原因/结果）
   - label: 节点名称（简短，2-10 字，如"性能问题""需求变更"）
   - node_type: 类型（root / cause / effect / condition）
   - keywords: 关键词（用于匹配）

3. causal_edges: 因果边数组（节点之间的因果关系）
   - from_label: 起始节点 label（原因方）
   - to_label: 目标节点 label（结果方）
   - relation: 关系类型（causes / prevents / requires）

规则：
- 如果对话中没有明确的因果关系，causal_nodes 和 causal_edges 可以为空数组
- 节点 label 要简洁，边要体现"因为 A 所以 B"的关系
- 只返回 JSON 对象，不要多余文字

对话：
{conversation_text}
"""


class EventReducer:
    """会话 → 事件提炼器"""

    def __init__(self, model_client=None, store: EventStore = None, embedder: EmbeddingEngine = None):
        """
        Args:
            model_client: LLM 客户端（依赖注入）
            store: EventStore 实例（依赖注入，测试用）
            embedder: EmbeddingEngine 实例（依赖注入，测试用）
        """
        self._model_client = model_client
        self._store = store
        self._embedder = embedder

    def set_model(self, client):
        """注入 LLM 客户端（兼容旧 API）"""
        self._model_client = client

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    async def reduce(self, session_id: str, conversation_text: str, owner_id: str = None) -> List[MemoryEvent]:
        """分析对话并生成记忆事件 + 因果图

        Args:
            session_id: 会话 ID
            conversation_text: 对话文本
            owner_id: 记忆所属模型 ID（如 "large::large_primary"），默认 "large::large_primary"
        """
        owner_id = owner_id or "large::large_primary"
        logger.info(f"[EventReducer] 分析会话 {session_id} ({len(conversation_text)} 字)")

        # 检测是否有值得提炼的内容（少于 50 字跳过）
        if len(conversation_text.strip()) < 50:
            logger.debug("[EventReducer] 对话太短，跳过")
            return []

        # 调用 LLM（返回事件 + 因果关系）
        result = await self._call_llm(conversation_text)
        events = result.get("events", [])
        causal_nodes = result.get("causal_nodes", [])
        causal_edges = result.get("causal_edges", [])

        if not events and not causal_nodes:
            logger.debug("[EventReducer] LLM 未生成任何内容")
            return []

        # 保存因果图
        if causal_nodes or causal_edges:
            self._save_causal_graph(causal_nodes, causal_edges)

        if not events:
            return []

        # 填充元数据 + 去重 + 存储
        store = self._get_store()

        # 去重：检查已有事件，跳过 fact 相似的
        existing_facts = set()
        try:
            existing = store.list_events(limit=500)
            existing_facts = {e.fact[:60] for e in existing}
        except Exception as e:
            logger.warning(f"[EventReducer] 去重查询失败: {e}")

        saved = []
        for ev in events:
            ev.session_id = session_id
            ev.owner_id = owner_id  # 标记所属模型

            # 跳过重复事件
            fact_key = ev.fact[:60]
            if fact_key in existing_facts:
                logger.debug(f"[EventReducer] 跳过重复事件: {fact_key}")
                continue
            existing_facts.add(fact_key)

            # save_event 内部会自动向量化 + 写 FAISS
            ev_id = store.save_event(ev)

            saved.append(ev)
            logger.info(f"[EventReducer] 保存事件 {ev_id}: {ev.fact[:50]}... (重要性={ev.importance})")

        # 共现统计：自动发现因果边
        if saved:
            try:
                from modules.memory.causal_graph import CausalGraph
                graph = CausalGraph.get_instance()
                saved_ids = [ev.id for ev in saved]
                graph.update_cooccurrence(event_ids=saved_ids, min_cooccur=2)
            except Exception as e:
                logger.debug(f"[EventReducer] 共现统计失败 (非致命): {e}")

        return saved

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _call_llm(self, conversation_text: str) -> dict:
        """调用 LLM 生成事件 + 因果关系"""
        if not self._model_client:
            logger.warning("[EventReducer] 无模型客户端，跳过记忆提取")
            return {"events": [], "causal_nodes": [], "causal_edges": []}

        prompt = REDUCE_PROMPT_TEMPLATE.format(conversation_text=conversation_text)

        try:
            response = await self._model_client.generate(
                prompt,
                max_tokens=2048,
                temperature=0.3,
            )
            return self._parse_response(response)
        except Exception as e:
            logger.warning(f"[EventReducer] LLM 调用失败: {e}")
            return {"events": [], "causal_nodes": [], "causal_edges": []}

    def _parse_response(self, text: str) -> dict:
        """解析 LLM 返回的 JSON（包含 events + causal_nodes + causal_edges）"""
        # 清理可能的 markdown 包裹
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if "```" in text:
                text = text.rsplit("```", 1)[0]
        text = text.strip()

        # 尝试解析 JSON 对象
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 对象
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    logger.warning("[EventReducer] JSON 解析失败")
                    return {"events": [], "causal_nodes": [], "causal_edges": []}
            else:
                return {"events": [], "causal_nodes": [], "causal_edges": []}

        # 兼容旧格式（纯数组）
        if isinstance(data, list):
            return {"events": self._parse_events_list(data),
                    "causal_nodes": [], "causal_edges": []}

        events = self._parse_events_list(data.get("events", []))
        causal_nodes = data.get("causal_nodes", [])
        causal_edges = data.get("causal_edges", [])

        return {"events": events, "causal_nodes": causal_nodes, "causal_edges": causal_edges}

    def _parse_events_list(self, items: list) -> List[MemoryEvent]:
        """解析事件列表"""
        events = []
        for item in items:
            if not isinstance(item, dict) or not item.get("fact"):
                continue
            t = str(item.get("type", "fact")).strip().lower()
            if t not in ("emotion", "thought", "fact", "strategy"):
                t = "fact"
            ev = MemoryEvent(
                fact=str(item["fact"])[:500],
                thought=str(item.get("thought", ""))[:500],
                lesson=str(item.get("lesson", ""))[:300],
                keywords=item.get("keywords", [])[:10],
                importance=_parse_importance(item.get("importance", "medium")),
                type=t,
            )
            events.append(ev)
        return events

    def _save_causal_graph(self, nodes_data: list, edges_data: list):
        """保存因果节点和边到 CausalGraph"""
        if not nodes_data and not edges_data:
            return

        try:
            from modules.memory.causal_graph import CausalGraph, CausalNode, CausalEdge
            graph = CausalGraph.get_instance()

            # 保存节点（按 label 去重，已存在的更新 confidence）
            label_to_id = {}
            for nd in nodes_data:
                label = nd.get("label", "").strip()
                if not label or len(label) < 2:
                    continue
                node_type = nd.get("node_type", "cause")
                if node_type not in ("root", "cause", "effect", "condition"):
                    node_type = "cause"
                keywords = nd.get("keywords", [])
                if isinstance(keywords, str):
                    keywords = [keywords]

                # 查找已有节点
                existing = graph.find_nodes_by_label(label)
                if existing:
                    node = existing[0]
                    node.confidence = min(0.99, node.confidence + 0.02)
                    node.event_count += 1
                    graph.save_node(node)
                else:
                    node = CausalNode(
                        label=label,
                        node_type=node_type,
                        keywords=keywords,
                        importance=0.5,
                        confidence=0.5,
                        event_count=1,
                    )
                    graph.save_node(node)

                label_to_id[label] = node.id

            # 保存边
            for ed in edges_data:
                from_label = ed.get("from_label", "").strip()
                to_label = ed.get("to_label", "").strip()
                relation = ed.get("relation", "causes")
                if relation not in ("causes", "prevents", "requires", "alternatives"):
                    relation = "causes"

                from_id = label_to_id.get(from_label)
                to_id = label_to_id.get(to_label)
                if not from_id or not to_id or from_id == to_id:
                    continue

                # 检查边是否已存在
                existing_edges = graph._get_conn().execute(
                    "SELECT * FROM edges WHERE from_id=? AND to_id=? AND relation=?",
                    (from_id, to_id, relation),
                ).fetchall()
                if existing_edges:
                    # 已存在，提升置信度
                    edge = CausalEdge.from_dict(dict(existing_edges[0]))
                    edge.confidence = min(0.99, edge.confidence + 0.03)
                    graph.save_edge(edge)
                else:
                    edge = CausalEdge(
                        from_id=from_id,
                        to_id=to_id,
                        relation=relation,
                        edge_type="causal",
                        confidence=0.5,
                    )
                    graph.save_edge(edge)

            logger.info(f"[EventReducer] 因果图更新: {len(nodes_data)} 节点, {len(edges_data)} 边")

        except Exception as e:
            logger.warning(f"[EventReducer] 因果图保存失败 (非致命): {e}")

    def _fallback_summary(self, conversation_text: str) -> List[MemoryEvent]:
        """无 LLM 时的降级策略：规则引擎提取结构化事件"""
        events = []

        # 提取含因果/动作关键词的句子
        causal_patterns = [
            r"(.{5,60}(?:导致|造成|引起|引发).{5,60})",
            r"(.{5,60}(?:解决|修复|优化|改进|提升).{5,60})",
            r"(.{5,60}(?:问题|故障|错误|bug|崩溃).{5,60})",
        ]
        import re
        sentences = re.split(r'[。！？\n]', conversation_text)
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 5:
                continue
            for pattern in causal_patterns:
                m = re.search(pattern, sent)
                if m:
                    fact = m.group(1)[:200]
                    # 判断类型
                    if any(w in fact for w in ["修复", "优化", "改进", "提升", "解决"]):
                        etype = "strategy"
                        imp = 0.7
                    elif any(w in fact for w in ["问题", "故障", "错误", "崩溃"]):
                        etype = "fact"
                        imp = 0.6
                    else:
                        etype = "fact"
                        imp = 0.5
                    # 提取关键词
                    kws = re.findall(r'[\u4e00-\u9fff]{2,}', fact)[:5]
                    kws += re.findall(r'[a-zA-Z_]{3,}', fact.lower())[:3]
                    events.append(MemoryEvent(
                        fact=fact, type=etype, importance=imp,
                        keywords=kws[:6],
                    ))
                    break

        if not events:
            # 没有找到结构化句子，用截断摘要
            truncated = conversation_text[:200]
            events.append(MemoryEvent(
                fact=f"对话摘要: {truncated}",
                keywords=["对话"], importance=0.3,
            ))

        return events

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _get_store(self) -> EventStore:
        if self._store is None:
            self._store = EventStore.get_instance()
        return self._store

    def _get_embedder(self) -> EmbeddingEngine:
        if self._embedder is None:
            self._embedder = EmbeddingEngine.get_instance()
        return self._embedder


# 模块级快捷函数
_reducer_instance: Optional[EventReducer] = None
_reducer_lock = threading.Lock()


def get_reducer() -> EventReducer:
    global _reducer_instance
    if _reducer_instance is None:
        with _reducer_lock:
            if _reducer_instance is None:
                _reducer_instance = EventReducer()
    return _reducer_instance
