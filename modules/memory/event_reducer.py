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

# LLM 提示词：将一段对话提炼为结构化记忆事件
REDUCE_PROMPT_TEMPLATE = """你是一个记忆分析专家。请分析以下对话，提炼出有价值的记忆事件。

每段对话可能包含 1~3 个值得记住的事件。请以 JSON 数组格式输出，每个事件包含：

- fact: 发生了什么（客观描述，20-80 字）
- thought: 你的思考和分析（20-100 字）
- lesson: 学到了什么，可复用的经验教训（10-60 字）
- keywords: 关键词列表（2-6 个，用于检索匹配）
- importance: 重要性评分（0.0-1.0，0.3=普通, 0.5=值得注意, 0.7=重要, 0.9=极其重要）
- type: 事件类型（emotion | thought | fact | strategy）
  - emotion: 情绪感受、用户偏好、痛点
  - thought: 分析推理、反思、见解
  - fact: 客观事实、技术细节、配置信息
  - strategy: 方法论、架构决策、长期经验

判断标准：
- 用户明确表达偏好/痛点的 → importance ≥ 0.7
- 技术决策、架构约定 → importance ≥ 0.6
- 问题解决的方法 → importance ≥ 0.5
- 普通寒暄、临时状态 → 不要生成事件

只返回 JSON 数组，不要多余的文字说明。

对话：
{conversation_text}
"""


class EventReducer:
    """会话 → 事件提炼器"""

    def __init__(self, model_client=None):
        self._model_client = model_client
        self._store: Optional[EventStore] = None
        self._embedder: Optional[EmbeddingEngine] = None

    def set_model(self, client):
        """注入 LLM 客户端"""
        self._model_client = client

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    async def reduce(self, session_id: str, conversation_text: str) -> List[MemoryEvent]:
        """分析对话并生成记忆事件"""
        logger.info(f"[EventReducer] 分析会话 {session_id} ({len(conversation_text)} 字)")

        # 检测是否有值得提炼的内容（少于 50 字跳过）
        if len(conversation_text.strip()) < 50:
            logger.debug("[EventReducer] 对话太短，跳过")
            return []

        # 调用 LLM
        events = await self._call_llm(conversation_text)
        if not events:
            logger.debug("[EventReducer] LLM 未生成事件")
            return []

        # 填充元数据 + 向量化 + 存储
        store = self._get_store()
        embedder = self._get_embedder()

        saved = []
        for ev in events:
            ev.session_id = session_id
            ev_id = store.save_event(ev)

            # 向量化并存入 FAISS
            text_for_embedding = f"{ev.fact} {ev.thought} {ev.lesson} {' '.join(ev.keywords)}"
            embedding = embedder.embed(text_for_embedding)
            if embedding:
                store.add_embedding(ev_id, embedding)
                ev.embedding = embedding

            saved.append(ev)
            logger.info(f"[EventReducer] 保存事件 {ev_id}: {ev.fact[:50]}... (重要性={ev.importance})")

        return saved

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _call_llm(self, conversation_text: str) -> List[MemoryEvent]:
        """调用 LLM 生成事件列表"""
        if not self._model_client:
            logger.warning("[EventReducer] 无模型客户端，回退到基础摘要")
            return self._fallback_summary(conversation_text)

        prompt = REDUCE_PROMPT_TEMPLATE.format(conversation_text=conversation_text)

        try:
            response = await self._model_client.generate(
                prompt,
                max_tokens=2048,
                temperature=0.3,
            )
            return self._parse_events(response)
        except Exception as e:
            logger.warning(f"[EventReducer] LLM 调用失败: {e}")
            return self._fallback_summary(conversation_text)

    def _parse_events(self, text: str) -> List[MemoryEvent]:
        """解析 LLM 返回的 JSON"""
        # 清理可能的 markdown 包裹
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if "```" in text:
                text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                data = [data]
        except json.JSONDecodeError:
            logger.warning(f"[EventReducer] JSON 解析失败，尝试截取 [...]")
            # 尝试从文本中提取 JSON 数组
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    logger.warning("[EventReducer] 仍无法解析")
                    return []
            else:
                return []

        events = []
        for item in data:
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
                importance=min(max(float(item.get("importance", 0.5)), 0.0), 1.0),
                type=t,
            )
            events.append(ev)

        return events

    def _fallback_summary(self, conversation_text: str) -> List[MemoryEvent]:
        """无 LLM 时的降级策略：截取摘要为一个事件"""
        truncated = conversation_text[:200]
        return [
            MemoryEvent(
                fact=f"对话摘要: {truncated}",
                thought="",
                lesson="",
                keywords=["对话"],
                importance=0.3,
            )
        ]

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
