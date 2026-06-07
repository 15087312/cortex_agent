"""
感知集成器

将感知模块与思考模块深度集成 — 基于 difference_detector 的统一差异源
"""
import json
import re
from typing import List, Optional, TYPE_CHECKING

from utils.logger import setup_logger

if TYPE_CHECKING:
    from modules.difference_detector.models import Difference


class PerceptionThinkIntegrator:
    """
    感知-思考集成器

    从 difference_detector 获取活跃差异，决定是否注入当前思考上下文。
    不再维护独立的变化评估逻辑，所有强度计算由 difference_detector 统一完成。
    """

    def __init__(
        self,
        model_manager=None,
        min_intensity: float = 40.0,
    ):
        self.logger = setup_logger("perception_think_integrator")
        self.model_manager = model_manager
        self.min_intensity = min_intensity
        self.context_history: List[str] = []
        self._max_history = 10

    def get_relevant_differences(self, current_task: str = "") -> List["Difference"]:
        """
        从 difference_detector 查询活跃差异，可选 LLM 相关性过滤。

        Args:
            current_task: 当前任务描述，有值时触发 LLM 相关性过滤

        Returns:
            过滤后的 Difference 列表，按强度降序
        """
        try:
            from modules.difference_detector import get_detector
            differences = get_detector().get_active_differences(min_intensity=self.min_intensity)
        except Exception as e:
            self.logger.debug(f"获取活跃差异失败: {e}")
            return []

        if not differences:
            return []

        if current_task and self.model_manager:
            try:
                return self._llm_relevance_filter(differences, current_task)
            except Exception as e:
                self.logger.debug(f"LLM相关性过滤失败，使用全量差异: {e}")

        return differences

    def _llm_relevance_filter(self, differences: List["Difference"], current_task: str) -> List["Difference"]:
        """使用小模型过滤与当前任务相关的差异"""
        summary_lines = [
            f"{i + 1}. [{d.source_type}/{d.category}] 强度={d.intensity:.0f} {d.payload}"
            for i, d in enumerate(differences)
        ]
        prompt = (
            f"当前任务：{current_task}\n\n"
            f"活跃差异列表（编号从1开始）：\n"
            + "\n".join(summary_lines)
            + "\n\n请返回与当前任务相关的差异编号列表（JSON数组），例如 [1, 3]。\n"
            "若全部相关返回 \"all\"，若全不相关返回 []。"
        )

        response = self.model_manager.call(prompt=prompt, model_size="lite")

        try:
            if "all" in response.lower():
                return differences
            match = re.search(r"\[.*?\]", response, re.DOTALL)
            if match:
                indices = json.loads(match.group())
                return [differences[i - 1] for i in indices if 1 <= i <= len(differences)]
        except Exception as e:
            self.logger.debug(f"LLM 相关性过滤结果解析失败，使用全量差异: {e}")

        return differences

    def build_change_context(self, differences: List["Difference"]) -> str:
        """从 Difference 列表构建上下文字符串"""
        if not differences:
            return ""

        parts = ["\n## 重要环境变化\n"]
        for d in differences:
            desc = d.payload.get("description", d.category)
            parts.append(f"- [{d.source_type}] 强度={d.intensity:.0f} — {desc}")

        parts.append("\n请结合以上差异调整思考策略。")
        return "\n".join(parts)

    def inject_into_prompt(self, base_prompt: str, current_task: str = "") -> str:
        """
        将活跃差异注入提示词。

        Args:
            base_prompt: 基础提示词
            current_task: 当前任务描述（用于相关性过滤）

        Returns:
            注入差异上下文后的提示词
        """
        differences = self.get_relevant_differences(current_task)
        context = self.build_change_context(differences)

        if context:
            self.logger.info(f"注入 {len(differences)} 条差异上下文")
            self.context_history.append(context)
            if len(self.context_history) > self._max_history:
                self.context_history.pop(0)
            return base_prompt + "\n\n" + context

        return base_prompt

    def get_recent_context(self) -> List[str]:
        """获取最近的上下文历史"""
        return self.context_history.copy()

    def clear_history(self) -> None:
        """清空上下文历史"""
        self.context_history.clear()


class AttentionAwarePerception:
    """
    注意力感知集成

    结合注意力模块，在 difference_detector 的差异中筛选关注领域相关项
    """

    def __init__(self, integrator: PerceptionThinkIntegrator):
        self.integrator = integrator
        self.logger = setup_logger("attention_aware_perception")
        self._focus_areas: List[str] = []

    def set_focus_areas(self, areas: List[str]) -> None:
        """设置关注领域"""
        self._focus_areas = areas
        self.logger.info(f"设置关注领域: {areas}")

    def filter_relevant_differences(
        self,
        differences: List["Difference"],
        current_task: str = ""
    ) -> List["Difference"]:
        """按关注领域过滤差异（匹配 source_type、category 和 payload）"""
        if not self._focus_areas:
            return differences

        relevant = []
        for d in differences:
            combined = " ".join([d.source_type, d.category] + [str(v) for v in d.payload.values()]).lower()
            if any(area.lower() in combined for area in self._focus_areas):
                relevant.append(d)

        self.logger.debug(f"注意力过滤: {len(relevant)}/{len(differences)}")
        return relevant

    def process_with_attention(self, current_task: str = "") -> List["Difference"]:
        """
        带注意力过滤的差异获取。

        Returns:
            过滤后的 Difference 列表
        """
        differences = self.integrator.get_relevant_differences(current_task)
        return self.filter_relevant_differences(differences, current_task)
