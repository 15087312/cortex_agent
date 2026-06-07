"""
注意力核心 - 改进版

问题修复：
1. 大模型不再被无关记忆干扰
2. 分离：上下文（对话历史）vs 相关记忆（需要检索）
3. 只使用高度相关的记忆（相似度 > 0.5）
4. 独立的注意力决策，不依赖上下文延续
"""
import time
import json
import asyncio
from typing import List, Dict, Tuple, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger
from modules.memory.utils.importance_scorer import ImportanceScorer
from modules.perception.interface import PerceptionPort, get_perception_port
from config.attention_config import get_attention_config

logger = setup_logger("attention_core")

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False
    logger.warning("scikit-learn 未安装，使用关键词模式")


@dataclass
class AttentionDecision:
    """注意力决策"""
    focus: str  # 当前问题焦点
    active_modules: List[str]  # 激活的模块
    sleep_modules: List[str]  # 休眠的模块
    priority_weights: Dict[str, float]  # 优先级
    related_memory: List[str]  # 强相关记忆（相似度 > 0.5）
    context_related: List[Dict]  # 上下文（用于对话连贯）
    probe_signals: List[Dict] = field(default_factory=list)
    perception_changes: List[Dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    reasoning: str = ""  # 决策原因
    importance_score: float = 0.5
    importance_reasons: List[str] = field(default_factory=list)
    attention_level: float = 0.6


class AttentionCore:
    """注意力核心 - 改进版"""

    def __init__(self, perception: Optional[PerceptionPort] = None):
        self.module_keywords = {
            "code_expert": ["代码", "python", "bug", "函数", "编程", "算法", "调试", "写代码", "程序"],
            "chat_expert": ["你好", "在吗", "最近", "天气", "周末", "闲聊", "聊"],
            "memory_expert": ["回忆", "之前", "上次", "记得", "历史", "我们说过", "以前"],
            "system_expert": ["文件", "打开", "保存", "系统", "设置", "配置", "文件夹"]
        }
        self.main_model = "main_model"
        # CONC-2: Don't share TfidfVectorizer across concurrent calls
        # Each call will create its own instance to avoid state corruption
        self._last_focus = ""  # 上一次的焦点
        self._relevance_threshold = 0.5  # 相关性阈值
        self._cfg = get_attention_config()
        self._importance_scorer = ImportanceScorer()
        self._perception = perception or get_perception_port()

    def analyze(
        self,
        user_input: str,
        context: Optional[List[Dict]] = None,
        short_term_memory: Optional[List[str]] = None
    ) -> AttentionDecision:
        """分析用户输入 - 独立决策，不被无关记忆干扰"""

        context = context or []
        short_term_memory = short_term_memory or []

        # 0. 问题重要性识别
        importance_score, importance_reasons = self._recognize_importance(
            user_input=user_input,
            context=context,
        )
        attention_level = self._map_importance_to_attention(importance_score)

        # 1. 检查感知变化
        perception_changes = self._check_perception_changes()

        # 2. 提取强相关记忆（与当前输入高度相关的）
        related_memory = self._get_related_memory(user_input, short_term_memory)

        # 3. 简单上下文（只保留最近2轮，用于对话连贯）
        context_related = context[-4:] if context else []

        # 4. 基于当前输入决定激活模块（不基于历史）
        active_modules, sleep_modules, reasoning = self._decide_modules(user_input)

        # 5. 优先级权重
        priority_weights = self._calculate_priority(active_modules)

        # 6. 提取焦点
        focus = self._extract_focus(user_input, related_memory)

        # 更新上一次焦点
        self._last_focus = focus

        decision = AttentionDecision(
            focus=focus,
            active_modules=active_modules,
            sleep_modules=sleep_modules,
            priority_weights=priority_weights,
            related_memory=related_memory,
            context_related=context_related,
            perception_changes=perception_changes,
            reasoning=reasoning,
            importance_score=importance_score,
            importance_reasons=importance_reasons,
            attention_level=attention_level,
        )

        logger.info(f"注意力决策: {focus} -> {active_modules} | importance={importance_score:.2f} attention={attention_level:.2f}")
        return decision

    def _recognize_importance(self, user_input: str, context: Optional[List[Dict]] = None) -> Tuple[float, List[str]]:
        """识别问题重要性分数与原因"""
        if not self._cfg.importance_enabled:
            return 0.5, ["重要性识别已关闭，使用默认分数"]

        context = context or []
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

        score = ImportanceScorer.score_rule_based(text, score_context)
        reasons: List[str] = []

        hit_urgent = [k for k in urgent_keywords if k in text]
        hit_task = [k for k in task_keywords if k in text]
        if hit_urgent:
            reasons.append(f"命中紧急关键词: {hit_urgent[:3]}")
        if hit_task:
            reasons.append(f"命中任务关键词: {hit_task[:3]}")
        if "?" in text or "？" in text or "如何" in text or "怎么" in text:
            reasons.append("包含明确求解意图")
        if context:
            reasons.append(f"存在上下文延续: {len(context)} 条")
        if not reasons:
            reasons.append("按默认规则评估")

        return max(0.0, min(1.0, score)), reasons

    def _map_importance_to_attention(self, importance_score: float) -> float:
        """将重要性分数映射为注意力等级"""
        if self._cfg.force_static_level is not None:
            return max(0.0, min(1.0, float(self._cfg.force_static_level)))

        attention_level = round(max(0.0, min(1.0, importance_score)), 2)
        return attention_level

    def _check_perception_changes(self) -> List[Dict]:
        """检查感知系统变化"""
        changes = []
        try:
            if self._perception.is_running:
                attention_items = self._perception.get_attention_items()
                for item in attention_items[-3:]:
                    changes.append({
                        "change": item.change.to_prompt() if hasattr(item, 'change') else str(item),
                        "urgency": item.urgency
                    })
        except Exception as e:
            logger.debug("感知系统变化检查失败 (非致命): %s", e)
        return changes

    def _get_related_memory(self, user_input: str, short_term_memory: List[str]) -> List[str]:
        """获取与当前输入高度相关的记忆"""
        if not short_term_memory or not HAS_SKLEARN:
            return []

        try:
            all_texts = [user_input] + short_term_memory
            # CONC-2: Create local instance per call (no shared state)
            vectorizer = TfidfVectorizer(max_features=100)
            matrix = vectorizer.fit_transform(all_texts)
            similarities = cosine_similarity(matrix[0:1], matrix[1:])[0]

            # 只返回相似度 > 阈值的记忆
            related = [
                mem for mem, sim in zip(short_term_memory, similarities)
                if sim > self._relevance_threshold
            ]
            return related[:3]  # 最多3条
        except Exception as e:
            logger.warning(f"注意力相关记忆检索失败: {e}")
            return []

    def _decide_modules(
        self,
        user_input: str
    ) -> Tuple[List[str], List[str], str]:
        """基于当前输入决定激活模块 - 不依赖历史"""
        
        # 始终激活主模型
        active = [self.main_model]
        reasoning = "主模型始终激活"
        
        # 检测当前输入中的关键词
        input_lower = user_input.lower()
        detected = []
        
        for module, kws in self.module_keywords.items():
            if any(kw in input_lower for kw in kws):
                detected.append(module)
        
        # 只有明确匹配才激活对应专家
        if detected:
            active.extend(detected)
            reasoning = f"检测到关键词: {detected}"
        
        # 所有其他模块休眠
        all_modules = list(self.module_keywords.keys())
        sleep = [m for m in all_modules if m not in active]
        
        return active, sleep, reasoning

    def _calculate_priority(self, active: List[str]) -> Dict[str, float]:
        """计算优先级"""
        weights = {}
        for m in active:
            weights[m] = 1.0 if m == self.main_model else 0.8
        return weights

    def _extract_focus(self, user_input: str, related_memory: List[str]) -> str:
        """提取焦点"""
        focus = user_input[:30]
        if related_memory:
            focus += f" [相关:{len(related_memory)}条]"
        return focus


def create_attention_core(perception: Optional[PerceptionPort] = None) -> AttentionCore:
    """创建注意力核心"""
    return AttentionCore(perception=perception)