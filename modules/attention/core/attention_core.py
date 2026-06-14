"""
注意力核心 — 任务重要性分类器 + 记忆过滤器

设计意图：
  这个模块名字叫"注意力"，实际做的是两件事：
  1. 关键词分类：扫描用户输入中的紧急/任务关键词 → 重要性分数
  2. TF-IDF 相关记忆：计算输入与短期记忆的余弦相似度 → 相关记忆

  影响行为的方式：
  - importance_score: 注入模型 prompt 作为提示（仅提示，不强制）
  - attention_level: 控制 MemoryAttentionScorer 的记忆检索阈值
    （任务越重要，阈值越低=召回更多记忆；反之越少=省 token）

  2026-06 裁掉了装饰性代码：
  - _decide_modules / _calculate_priority（输出从未被消费）
  - _check_perception_changes（感知变化由 PerceptionIntegrator 处理）
  - module_keywords 字典（专家关键词映射，从未被路由使用）
  - context_related / reasoning / probe_signals 等字段

  现在的 AttentionDecision 从 13 个字段减到 5 个，
  只保留真正影响行为的部分。
"""
import time
import json
import asyncio
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger
from modules.perception.interface import PerceptionPort, get_perception_port
from config.attention_config import get_attention_config

logger = setup_logger("attention_core")

# 延迟导入重要性评分器（旧版 memory 存根兼容）
try:
    from modules.memory.utils.importance_scorer import ImportanceScorer
except ImportError:
    ImportanceScorer = None

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("scikit-learn 未安装，使用关键词模式")


@dataclass
class AttentionDecision:
    """任务重要性决策

    只有 importance_score 和 attention_level 实际影响运行时行为。
    attention_level 控制 MemoryAttentionScorer 的记忆检索阈值。
    importance_score 注入模型 prompt 作为提示。
    """
    focus: str = ""
    related_memory: List[str] = field(default_factory=list)
    importance_score: float = 0.5
    importance_reasons: List[str] = field(default_factory=list)
    attention_level: float = 0.6
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class AttentionCore:
    """任务重要性核心"""

    def __init__(self, perception: Optional[PerceptionPort] = None):
        self._last_focus = ""
        self._relevance_threshold = 0.5
        self._cfg = get_attention_config()
        try:
            self._importance_scorer = ImportanceScorer() if ImportanceScorer else None
        except Exception:
            self._importance_scorer = None

    def analyze(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        short_term_memory: Optional[List[str]] = None
    ) -> AttentionDecision:
        """分析用户输入的重要性"""
        context = context or []
        short_term_memory = short_term_memory or []

        # 1. 重要性识别
        importance_score, importance_reasons = self._recognize_importance(user_input, context)
        attention_level = self._map_importance_to_attention(importance_score)

        # 2. TF-IDF 相关记忆
        related_memory = self._get_related_memory(user_input, short_term_memory)

        # 3. 焦点（仅用于日志，不影响行为）
        focus = (user_input or "")[:30]
        if related_memory:
            focus += f" [相关:{len(related_memory)}条]"
        self._last_focus = focus

        logger.info(f"重要性分析: score={importance_score:.2f} level={attention_level:.2f}")
        return AttentionDecision(
            focus=focus,
            related_memory=related_memory,
            importance_score=importance_score,
            importance_reasons=importance_reasons,
            attention_level=attention_level,
        )

    def _recognize_importance(self, user_input: str, context: Optional[List[Dict]] = None) -> Tuple[float, List[str]]:
        """关键词匹配 → 重要性分数"""
        if not self._cfg.importance_enabled:
            return 0.5, ["重要性识别已关闭"]

        text = (user_input or "").strip()
        if not text:
            return 0.0, ["输入为空"]

        urgent_keywords = ["紧急", "立刻", "马上", "故障", "报错", "崩溃", "中断", "阻塞"]
        task_keywords = ["实现", "修复", "优化", "设计", "排查", "部署", "上线", "架构"]

        score_context = {
            "source": "user",
            "task_related": any(k in text for k in task_keywords),
            "emotion_intensity": 0.8 if any(k in text for k in urgent_keywords) else 0.3,
        }

        score = 0.5
        try:
            if ImportanceScorer is not None:
                score = ImportanceScorer.score_rule_based(text, score_context)
        except Exception:
            pass
        reasons: List[str] = []
        hit_urgent = [k for k in urgent_keywords if k in text]
        hit_task = [k for k in task_keywords if k in text]
        if hit_urgent:
            reasons.append(f"紧急关键词: {hit_urgent[:3]}")
        if hit_task:
            reasons.append(f"任务关键词: {hit_task[:3]}")
        if "?" in text or "？" in text or "如何" in text or "怎么" in text:
            reasons.append("求解意图")
        if not reasons:
            reasons.append("默认评分")

        return max(0.0, min(1.0, score)), reasons

    def _map_importance_to_attention(self, importance_score: float) -> float:
        """重要性 → 注意力等级（影响记忆检索阈值）"""
        if self._cfg.force_static_level is not None:
            return max(0.0, min(1.0, float(self._cfg.force_static_level)))
        return round(max(0.0, min(1.0, importance_score)), 2)

    def _get_related_memory(self, user_input: str, short_term_memory: List[str]) -> List[str]:
        """TF-IDF 相关记忆"""
        if not short_term_memory or not HAS_SKLEARN:
            return []
        try:
            vectorizer = TfidfVectorizer(max_features=100)
            matrix = vectorizer.fit_transform([user_input] + short_term_memory)
            similarities = cosine_similarity(matrix[0:1], matrix[1:])[0]
            return [
                mem for mem, sim in zip(short_term_memory, similarities)
                if sim > self._relevance_threshold
            ][:3]
        except Exception as e:
            logger.warning(f"相关记忆检索失败: {e}")
            return []