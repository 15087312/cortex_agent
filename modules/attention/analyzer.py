"""
注意力分析器（V1+V2 合并版）

核心功能：
1. 关键词匹配 → 重要性分数
2. 5维注意力向量（简化版，保留 V2 的信息密度）
3. 生成可直接注入 prompt 的 summary_text

设计原则：
- 无状态，可复用
- 无外部依赖（tf-idf 等装饰性逻辑移除）
- 所有输出都是"提示性"的，不硬控任何系统行为
"""
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from utils.logger import setup_logger

logger = setup_logger("attention_analyzer")

# 紧急/任务关键词表（V1 核心逻辑保留）
_URGENT_KEYWORDS = ["紧急", "立刻", "马上", "故障", "报错", "崩溃", "中断", "阻塞"]
_TASK_KEYWORDS = ["实现", "修复", "优化", "设计", "排查", "部署", "上线", "架构"]
_QUERY_KEYWORDS = ["?", "？", "如何", "怎么", "为什么", "什么"]


@dataclass
class AttentionVector:
    """多维度注意力向量（纯数据容器）

    V2 保留的唯一概念——将标量展开为 5 维，为 prompt 提供更丰富的信息密度。
    不再附带运算方法（magnitude/normalize/dot/cosine/etc），
    这些在单一文本输入下没有任何消费者。
    """
    semantic: float = 0.5      # 语义相关性 (0-1)
    temporal: float = 0.5      # 时间衰减 (0-1)
    task: float = 0.5          # 任务优先级 (0-1)
    emotion: float = 0.0       # 情感强度 (0-1)
    modality: float = 0.5      # 模态权重 (0-1)
    source: str = "text"
    confidence: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "semantic": self.semantic,
            "temporal": self.temporal,
            "task": self.task,
            "emotion": self.emotion,
            "modality": self.modality,
            "source": self.source,
            "confidence": self.confidence,
        }


@dataclass
class AttentionResult:
    """注意力分析结果"""
    importance_score: float = 0.5
    attention_level: float = 0.6
    vector: Optional[AttentionVector] = None
    importance_reasons: List[str] = field(default_factory=list)

    @property
    def summary_text(self) -> str:
        """生成可直接注入 prompt 的上下文文本"""
        parts = [
            "\n\n【注意力状态】",
            f"任务重要性: {self.attention_level:.2f}/1.0",
            "高重要性任务应投入更多思考轮次和工具调用。",
        ]
        if self.vector is not None:
            parts.append(
                f"\n【多维度注意力状态】\n"
                f"语义相关性: {self.vector.semantic:.2f} | "
                f"时间衰减: {self.vector.temporal:.2f} | "
                f"任务优先级: {self.vector.task:.2f} | "
                f"情感强度: {self.vector.emotion:.2f} | "
                f"模态权重: {self.vector.modality:.2f}"
            )
        return "\n".join(parts)

    @property
    def importance_context(self) -> str:
        """生成简版 importance 上下文（供 MultiModelOrchestrator 使用）"""
        return f"\n\n【任务重要性】{self.importance_score:.2f}/1.0\n" \
               f"高重要性任务应投入更多思考轮次和工具调用。"


class AttentionAnalyzer:
    """注意力分析器

    单次分析是无状态的，实例可安全复用。
    """

    def __init__(self):
        self._cfg = self._load_config()

    @staticmethod
    def _load_config():
        from config.settings import settings
        from types import SimpleNamespace
        return SimpleNamespace(
            importance_enabled=settings.ATTENTION_IMPORTANCE_ENABLED,
            force_static_level=settings.ATTENTION_FORCE_STATIC_LEVEL,
        )

    def analyze(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        short_term_memory: Optional[List[str]] = None,
    ) -> AttentionResult:
        """分析用户输入，返回注意力结果

        Args:
            user_input: 用户输入文本
            context: 对话上下文（当前未被使用，保留参数兼容）
            short_term_memory: 短期记忆（当前未被使用，保留参数兼容）

        Returns:
            AttentionResult: 包含标量 + 向量 + 格式化文本
        """
        # 1. 关键词重要性评分
        importance_score, reasons = self._score_importance(user_input)

        # 2. 计算 attention_level
        attention_level = self._compute_attention_level(importance_score)

        # 3. 构建 5 维向量
        vector = self._build_vector(user_input, importance_score)

        logger.info(f"注意力分析: score={importance_score:.2f} level={attention_level:.2f}")

        return AttentionResult(
            importance_score=importance_score,
            attention_level=attention_level,
            vector=vector,
            importance_reasons=reasons,
        )

    def _score_importance(self, text: str) -> tuple[float, list[str]]:
        """关键词匹配 → 重要性分数（V1 核心逻辑）"""
        if not self._cfg.importance_enabled:
            return 0.5, ["重要性识别已关闭"]

        text = (text or "").strip()
        if not text:
            return 0.0, ["输入为空"]

        reasons: List[str] = []
        score = 0.5

        hit_urgent = [k for k in _URGENT_KEYWORDS if k in text]
        hit_task = [k for k in _TASK_KEYWORDS if k in text]
        hit_query = [k for k in _QUERY_KEYWORDS if k in text]

        if hit_urgent:
            score += 0.3
            reasons.append(f"紧急关键词: {hit_urgent[:3]}")
        if hit_task:
            score += 0.15
            reasons.append(f"任务关键词: {hit_task[:3]}")
        if hit_query:
            score += 0.05
            reasons.append("求解意图")
        if not reasons:
            reasons.append("常规输入")

        return max(0.0, min(1.0, score)), reasons

    def _compute_attention_level(self, importance_score: float) -> float:
        """重要性 → 注意力等级"""
        if self._cfg.force_static_level is not None:
            return max(0.0, min(1.0, float(self._cfg.force_static_level)))
        return round(max(0.0, min(1.0, importance_score)), 2)

    def _build_vector(self, text: str, importance_score: float) -> AttentionVector:
        """构建 5 维注意力向量（简化版）"""
        has_urgency = any(k in text for k in _URGENT_KEYWORDS)
        has_task = any(k in text for k in _TASK_KEYWORDS)

        return AttentionVector(
            semantic=importance_score,
            temporal=0.5,  # 固定值——时间衰减只有跨轮才有意义
            task=importance_score,
            emotion=0.8 if has_urgency else (0.3 if has_task else 0.0),
            modality=0.9,  # 纯文本输入
            source="text",
            confidence=0.9 if (has_urgency or has_task) else 0.6,
        )


def create_attention_analyzer() -> AttentionAnalyzer:
    """创建注意力分析器（工厂函数）"""
    return AttentionAnalyzer()
