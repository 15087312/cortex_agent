"""
良知系统 — 基于因果知识的决策层

从因果图中提取经验知识，生成内心独白。
静态价值观（values.txt）作为底线，因果图提供动态经验指导。

工作流程：
  1. EventRetrieval 检索具体事件 → 通过 causal_node_ids 找到因果节点
  2. CausalTree 展开节点（原因链 + 后果链 + 证据）
  3. LLM 生成内心独白（因果 + 情绪 + 价值观 → 行动建议）
  4. 大模型开始正式工作
  5. 反馈闭环：大模型的回应 → 调整因果图置信度
"""
import re
from dataclasses import dataclass
from typing import Optional, List
from utils.logger import setup_logger

logger = setup_logger("conscience")

CONSCIENCE_PROMPT = """你是总指挥，正在回忆过去的经验。

【你过去的经验（因果知识）】
{causal_knowledge}

【你的核心价值观】
{values}

【最近对话】
{recent_dialog}

【当前用户输入】
{user_input}

现在，请以第一人称写一段内心独白（3-5句话），就像你突然想起了过去的一些经验：
1. 你以前经历过什么、观察到什么规律
2. 基于这些经验，你觉得现在应该怎么考虑
3. 你的直觉判断

要求：
- 用括号包裹，第一人称，像在自言自语
- 身份就是你自己——总指挥——不是别人
- 优先引用因果知识（"我记得X往往导致Y"），其次引用价值观
- 不要说"我是良知系统""根据分析"之类的话，这是你自己的记忆
- 不要输出 JSON，不要用标签格式

直接输出内心独白，不要任何前缀或说明。"""


@dataclass
class ConscienceGuidance:
    """良知引导结果"""
    inner_thoughts: str = ""


class Conscience:
    """良知系统 — 基于因果图的经验决策"""

    def __init__(self, model_client=None):
        self._model_client = model_client
        self._last_dialog_buffer: list = []
        # 记录本轮参与分析的节点 ID，用于反馈闭环
        self._last_analyzed_node_ids: List[str] = []

    def add_to_dialog(self, role: str, text: str):
        if role in ("user", "assistant") and text:
            self._last_dialog_buffer.append(f"{'用户' if role == 'user' else '助手'}: {text[:300]}")
            if len(self._last_dialog_buffer) > 20:
                self._last_dialog_buffer = self._last_dialog_buffer[-20:]

    def _get_causal_knowledge(self, user_input: str, owner_id: str = "large_primary") -> str:
        """从因果图中提取与当前输入相关的经验知识

        改进：先通过 EventStore 检索相关事件，从事件的 causal_node_ids 找到因果节点，
        再展开因果树分析。比纯关键词匹配准得多。
        """
        try:
            from modules.memory.causal_graph import CausalGraph
            from modules.memory.causal_tree import CausalTree

            graph = CausalGraph.get_instance()
            tree = CausalTree(graph)

            # Step 1: 检索相关事件（按 owner_id 隔离），提取 causal_node_ids
            node_ids = self._get_node_ids_from_events(user_input, owner_id=owner_id)

            # Step 2: 用节点 ID 做因果树分析
            parts = []
            seen_labels = set()

            for nid in node_ids:
                try:
                    et = tree.expand_node(nid)
                except ValueError:
                    continue
                if et.node.label in seen_labels:
                    continue
                seen_labels.add(et.node.label)

                self._last_analyzed_node_ids.append(nid)

                parts.append(f"【{et.node.label}】(置信度 {et.confidence:.0%})")
                if et.parent_chain:
                    chain = " ← ".join(n.label for n in et.parent_chain)
                    parts.append(f"  原因: {chain}")
                if et.child_chains:
                    for c in et.child_chains:
                        parts.append(f"  后果: {' → '.join(n.label for n in c)}")
                if et.evidence:
                    for ev in et.evidence[:2]:
                        parts.append(f"  事实: {ev.fact[:60]}")

            # Step 3: 如果事件关联不到节点，回退到关键词锚点匹配
            if not parts:
                anchors = graph.find_anchor_nodes(user_input, top_k=3)
                for node, score in anchors[:2]:
                    self._last_analyzed_node_ids.append(node.id)
                    et = tree.expand_node(node.id)
                    parts.append(f"【{et.node.label}】(置信度 {et.confidence:.0%})")
                    if et.parent_chain:
                        chain = " ← ".join(n.label for n in et.parent_chain)
                        parts.append(f"  原因: {chain}")
                    if et.child_chains:
                        for c in et.child_chains:
                            parts.append(f"  后果: {' → '.join(n.label for n in c)}")
                    if et.evidence:
                        for ev in et.evidence[:2]:
                            parts.append(f"  事实: {ev.fact[:60]}")

            return "\n".join(parts) if parts else "（暂无相关因果经验）"

        except Exception as e:
            logger.debug(f"[Conscience] 因果知识提取失败: {e}")
            return "（暂无相关因果经验）"

    def _get_node_ids_from_events(self, user_input: str, owner_id: str = "large_primary") -> List[str]:
        """从 EventRetrieval 检索事件（按 owner_id 隔离），提取 causal_node_ids

        使用完整的 RAG 评分管道（语义+重要性+时效性+效用+频率），
        替代原先的纯关键词/LIKE 搜索。
        """
        try:
            import asyncio
            from modules.memory.event_retrieval import EventRetrieval

            retrieval = EventRetrieval.get_instance()

            # 使用 EventRetrieval 的完整评分管道
            loop = asyncio.get_event_loop()
            if loop.is_running():
                events = asyncio.run_coroutine_threadsafe(
                    retrieval.retrieve(user_input, max_results=10, threshold=0.0, owner_id=owner_id),
                    loop,
                ).result(timeout=10)
            else:
                events = loop.run_until_complete(
                    retrieval.retrieve(user_input, max_results=10, threshold=0.0, owner_id=owner_id)
                )

            if not events:
                return []

            # 从事件中提取 causal_node_ids，按出现频率排序
            node_id_counts = {}
            for ev in events:
                for nid in (ev.causal_node_ids or []):
                    node_id_counts[nid] = node_id_counts.get(nid, 0) + 1

            # 按频率降序，取 top 5
            sorted_ids = sorted(node_id_counts, key=node_id_counts.get, reverse=True)
            return sorted_ids[:5]

        except Exception as e:
            logger.debug(f"[Conscience] 事件检索失败: {e}")
            return []

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """提取中英文关键词"""
        if not text:
            return []
        keywords = set()
        eng = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{1,}', text)
        keywords.update(w.lower() for w in eng if len(w) >= 2)
        chn = re.findall(r'[\u4e00-\u9fff]{2,}', text)
        keywords.update(chn)
        # 对中文词取关键部分（4字及以上拆 bigram，让"财务问题"→"财务"）
        for kw in list(chn):
            if len(kw) >= 4:
                for i in range(len(kw) - 1):
                    bigram = kw[i:i+2]
                    if bigram not in keywords:
                        keywords.add(bigram)
        return list(keywords)[:10]

    async def analyze_feedback(self, user_input: str, model_response: str):
        """反馈闭环：复用 EventReducer 的模型分析回应，确认/修正因果关系

        在模型回应后异步执行，不阻塞主流程。
        """
        if not self._last_analyzed_node_ids:
            return

        try:
            from modules.memory.event_reducer import get_reducer
            from modules.memory.causal_graph import CausalGraph

            reducer = get_reducer()
            if not reducer._model_client:
                return

            # 获取节点标签供 LLM 理解
            graph = CausalGraph.get_instance()
            known_nodes = {}
            for nid in self._last_analyzed_node_ids:
                node = graph.get_node(nid)
                if node:
                    known_nodes[nid] = node.label
            if not known_nodes:
                return

            prompt = (
                "分析以下对话，判断助手是否确认或否定了某些因果关系。\n\n"
                "【已知的因果概念】\n"
                + "\n".join(f"- {label} ({nid})" for nid, label in known_nodes.items()) + "\n\n"
                f"对话：\n"
                f"用户: {user_input}\n"
                f"助手: {model_response}\n\n"
                f"请输出 JSON，格式如下：\n"
                f'{{"confirmed": ["node_id1", "node_id2"], "contradicted": []}}\n'
                f"confirmed: 助手的回应中确认/支持/引用了哪些概念\n"
                f"contradicted: 助手的回应中否定/反驳/质疑了哪些概念\n"
                f"只返回 JSON，不要多余文字。"
            )

            try:
                text = await reducer._model_client.generate(prompt, max_tokens=500, temperature=0.1)
            except Exception:
                return

            # 解析 JSON
            import json
            text = text.strip()
            if "```" in text:
                text = text.split("```json", 1)[-1] if "```json" in text else text.split("```", 1)[-1]
                if "```" in text:
                    text = text.rsplit("```", 1)[0]
            text = text.strip()
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                return
            try:
                data = json.loads(text[start:end+1])
            except json.JSONDecodeError:
                return

            confirmed = data.get("confirmed", [])
            contradicted = data.get("contradicted", [])
            adjustments = 0

            for nid in confirmed:
                node = graph.get_node(nid)
                if node and nid in self._last_analyzed_node_ids:
                    node.confidence = min(0.99, node.confidence + 0.05)
                    graph.save_node(node)
                    for pred in graph.get_predecessors(nid):
                        pred.confidence = min(0.99, pred.confidence + 0.02)
                        graph.save_node(pred)
                    for succ in graph.get_successors(nid):
                        succ.confidence = min(0.99, succ.confidence + 0.02)
                        graph.save_node(succ)
                    adjustments += 1

            for nid in contradicted:
                node = graph.get_node(nid)
                if node and nid in self._last_analyzed_node_ids:
                    node.confidence = max(0.1, node.confidence - 0.1)
                    graph.save_node(node)
                    adjustments += 1

            if adjustments:
                logger.info(f"[Conscience] 反馈闭环: {adjustments} 个节点置信度已调整 "
                           f"(+{len(confirmed)}, -{len(contradicted)})")
        except Exception as e:
            logger.debug(f"[Conscience] 反馈闭环失败: {e}")
        finally:
            self._last_analyzed_node_ids = []

    async def think(self, user_input: str, owner_id: str = "large_primary") -> str:
        """生成良知内心独白

        流程：
        1. 从因果图提取相关知识（按 owner_id 隔离）
        2. 读取静态价值观
        3. 调用 LLM 生成内心独白
        """
        try:

            # 1. 从因果图提取相关知识
            causal_knowledge = self._get_causal_knowledge(user_input, owner_id=owner_id)
            
            # 2. 读取静态价值观
            values_text = ""
            try:
                import os
                values_path = os.path.join(os.path.dirname(__file__), "values.txt")
                if os.path.exists(values_path):
                    with open(values_path, "r", encoding="utf-8") as f:
                        values_text = f.read().strip()
                if not values_text:
                    values_text = "诚实、负责、安全、有益"
            except Exception:
                values_text = "诚实、负责、安全、有益"
            
            # 3. 获取最近对话
            recent_dialog = "\n".join(self._last_dialog_buffer[-6:]) if self._last_dialog_buffer else "（无）"
            
            # 4. 调用 LLM 生成内心独白
            if not self._model_client:
                logger.debug("[Conscience] 无模型客户端，跳过内心独白生成")
                return ""
            
            prompt = CONSCIENCE_PROMPT.format(
                causal_knowledge=causal_knowledge,
                values=values_text,
                recent_dialog=recent_dialog,
                user_input=user_input,
            )
            
            try:
                inner_thoughts = await self._model_client.generate(prompt, max_tokens=500, temperature=0.7)
                inner_thoughts = inner_thoughts.strip()
                if inner_thoughts:
                    logger.info(f"[Conscience] 生成内心独白：{inner_thoughts[:100]}...")
                    # 添加到对话历史（用于下一轮）
                    self.add_to_dialog("assistant", f"[良知]{inner_thoughts}")
                return inner_thoughts
            except Exception as e:
                logger.debug(f"[Conscience] LLM 生成失败：{e}")
                return ""
                
        except Exception as e:
            logger.debug(f"[Conscience] think 失败：{e}")
            return ""


# 单例
_conscience_instance: Optional[Conscience] = None


def get_conscience() -> Conscience:
    global _conscience_instance
    if _conscience_instance is None:
        _conscience_instance = Conscience()
    return _conscience_instance
