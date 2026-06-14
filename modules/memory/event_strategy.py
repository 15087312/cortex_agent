"""
EventStrategy — 根据检索到的历史事件制定本次回复策略

流程：
1. 收到用户问题后，先检索相关事件
2. 将事件作为"历史经验"输入 LLM
3. LLM 输出策略（本次对话的目标、要避免的坑、要遵循的经验）
4. 策略注入到 prompt 中

策略格式：
{
  "goal": "...",
  "pitfalls": ["...", "..."],
  "lessons_to_follow": ["...", "..."],
  "focus_points": ["...", "..."]
}
"""
import json
import threading
from typing import Any, Dict, List, Optional

from modules.memory.event_store import MemoryEvent
from utils.logger import setup_logger

logger = setup_logger("event_strategy")

STRATEGY_PROMPT_TEMPLATE = """你是一个策略规划专家。请根据以下历史记忆事件和当前用户问题，制定本次回复的策略。

历史记忆事件（按相关性排列）：
{events_text}

当前用户问题：
{user_input}

请以 JSON 格式输出策略，包含：
- goal: 本次回复的目标（一句话，20-60 字）
- pitfalls: 需要避免的问题列表（基于历史教训，2-4 条）
- lessons_to_follow: 需要遵循的经验列表（基于历史成功经验，2-4 条）
- focus_points: 本次回复的重点关注领域（1-3 条）

只返回 JSON，不要多余文字。
"""


class EventStrategy:
    """事件策略生成器"""

    _instance: "EventStrategy" = None
    _lock = threading.Lock()

    def __init__(self, model_client=None):
        self._model_client = model_client

    @classmethod
    def get_instance(cls, model_client=None) -> "EventStrategy":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(model_client=model_client)
        return cls._instance

    def set_model(self, client):
        self._model_client = client

    # ------------------------------------------------------------------
    # 核心方法
    # ------------------------------------------------------------------

    async def generate_strategy(
        self,
        user_input: str,
        events: List[MemoryEvent],
    ) -> Dict[str, Any]:
        """生成回复策略"""
        if not events:
            return self._default_strategy()

        # 格式化事件文本
        events_text = self._format_events(events)

        if not self._model_client:
            logger.debug("[EventStrategy] 无 LLM 客户端，返回默认策略")
            return self._default_strategy(events)

        prompt = STRATEGY_PROMPT_TEMPLATE.format(
            events_text=events_text,
            user_input=user_input,
        )

        try:
            response = await self._model_client.generate(
                prompt,
                max_tokens=1024,
                temperature=0.3,
            )
            strategy = self._parse_strategy(response)
            if strategy:
                return strategy
        except Exception as e:
            logger.warning(f"[EventStrategy] LLM 调用失败: {e}")

        return self._default_strategy(events)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _format_events(self, events: List[MemoryEvent]) -> str:
        lines = []
        for i, ev in enumerate(events, 1):
            lines.append(f"[事件 {i}] (重要性={ev.importance:.1f})")
            lines.append(f"  事实: {ev.fact}")
            if ev.thought:
                lines.append(f"  分析: {ev.thought}")
            if ev.lesson:
                lines.append(f"  经验: {ev.lesson}")
            if ev.keywords:
                lines.append(f"  标签: {', '.join(ev.keywords)}")
            lines.append("")
        return "\n".join(lines)

    def _parse_strategy(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if "```" in text:
                text = text.rsplit("```", 1)[0]
        text = text.strip()

        try:
            data = json.loads(text)
            if isinstance(data, dict):
                return {
                    "goal": data.get("goal", ""),
                    "pitfalls": data.get("pitfalls", []),
                    "lessons_to_follow": data.get("lessons_to_follow", []),
                    "focus_points": data.get("focus_points", []),
                }
        except json.JSONDecodeError:
            logger.warning("[EventStrategy] JSON 解析失败")
        return None

    def _default_strategy(self, events: Optional[List[MemoryEvent]] = None) -> Dict[str, Any]:
        """降级默认策略"""
        pitfalls = []
        lessons = []
        if events:
            for ev in events:
                if ev.lesson:
                    lessons.append(ev.lesson)

        return {
            "goal": "基于历史经验回复用户",
            "pitfalls": pitfalls or ["避免重复已知错误"],
            "lessons_to_follow": lessons or ["参考历史经验"],
            "focus_points": [],
        }


def format_strategy_for_prompt(strategy: Dict[str, Any]) -> str:
    """将策略格式化为 prompt 片段"""
    parts = ["【本次回复策略】"]

    if strategy.get("goal"):
        parts.append(f"目标: {strategy['goal']}")

    pitfalls = strategy.get("pitfalls", [])
    if pitfalls:
        parts.append("需避免:")
        for p in pitfalls:
            parts.append(f"  - {p}")

    lessons = strategy.get("lessons_to_follow", [])
    if lessons:
        parts.append("需遵循:")
        for l in lessons:
            parts.append(f"  - {l}")

    focus = strategy.get("focus_points", [])
    if focus:
        parts.append("重点:")
        for f in focus:
            parts.append(f"  - {f}")

    return "\n".join(parts)
