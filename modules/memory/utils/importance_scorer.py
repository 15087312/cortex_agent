"""
记忆重要性打分工具

使用轻量模型（1.5B）评估记忆的重要性分数。
考虑因素：
- 情感强度
- 信息密度
- 时效性
- 用户意图
"""
from typing import Dict, Any, Optional
import asyncio
from utils.logger import setup_logger
from modules.management.core.error_bus import error_bus, ErrorContext


class ImportanceScorer:
    """记忆重要性评分器"""
    
    def __init__(self, lite_model=None):
        """
        初始化评分器
        
        Args:
            lite_model: BaseModelClient 实例（可选）
        """
        self.logger = setup_logger("importance_scorer")
        self.lite_model = lite_model
        self._initialized = False
    
    async def initialize(self, lite_model=None):
        """
        初始化模型
        
        Args:
            lite_model: BaseModelClient 实例（如果构造函数未提供）
        """
        if lite_model:
            self.lite_model = lite_model
        
        if not self.lite_model:
            try:
                from modules.thinking.core.model_manager import model_manager
                self.lite_model = model_manager.lite_model
                if not self.lite_model:
                    self.logger.warning("轻量模型未在 ModelManager 中初始化")
                else:
                    self.logger.info("轻量模型已从 ModelManager 加载用于重要性评分")
            except Exception as e:
                self.logger.warning(f"轻量模型加载失败，将使用规则评分: {e}")
                error_bus.report_error(
                    e,
                    ErrorContext(
                        module="importance_scorer",
                        function="initialize",
                        extra={"action": "load_lite_model"}
                    )
                )
                self.lite_model = None
        
        self._initialized = True
    
    @staticmethod
    def score_rule_based(content: str, context: Dict[str, Any] = None) -> float:
        """
        基于规则的重要性评分（降级方案）
        
        Args:
            content: 记忆内容
            context: 上下文信息
            
        Returns:
            0-1 之间的重要性分数
        """
        score = 0.5  # 基础分
        
        if not content:
            return 0.0
        
        content_lower = content.lower()
        
        # 1. 情感强度检测
        emotion_words = {
            "高": ["非常", "极其", "特别", "太", "超级", "爱", "恨", "愤怒", "兴奋"],
            "中": ["有点", "有些", "稍微", "喜欢", "讨厌", "担心"],
            "低": ["一般", "普通", "还行", "凑合"]
        }
        
        for word in emotion_words["高"]:
            if word in content_lower:
                score += 0.2
                break
        
        for word in emotion_words["中"]:
            if word in content_lower:
                score += 0.1
                break
        
        # 2. 关键信息检测
        important_keywords = [
            "密码", "账号", "重要", "记住", "必须", "关键",
            "生日", "地址", "电话", "邮箱", "工作", "学校",
            "计划", "目标", "决定", "承诺", "约定"
        ]
        
        keyword_count = sum(1 for kw in important_keywords if kw in content_lower)
        score += min(keyword_count * 0.1, 0.3)
        
        # 3. 问题类型检测
        if any(q in content for q in ["?", "？", "怎么", "如何", "为什么"]):
            score += 0.1  # 问题通常更重要
        
        # 4. 长度惩罚（过短可能不重要）
        if len(content) < 10:
            score -= 0.2
        elif len(content) > 200:
            score += 0.1  # 长内容可能包含更多信息
        
        # 5. 上下文增强
        if context:
            # 如果是用户主动提供的信息
            if context.get("source") == "user":
                score += 0.1
            
            # 如果与当前任务相关
            if context.get("task_related"):
                score += 0.15
            
            # 如果是情绪表达
            if context.get("emotion_intensity", 0) > 0.7:
                score += 0.15
        
        return max(0.0, min(1.0, score))
    
    async def score_with_model(
        self,
        content: str,
        context: Dict[str, Any] = None
    ) -> float:
        """
        使用轻量模型评分
        
        Args:
            content: 记忆内容
            context: 上下文信息
            
        Returns:
            0-1 之间的重要性分数
        """
        if not self._initialized:
            await self.initialize()
        
        if not self.lite_model:
            self.logger.debug("轻量模型不可用，使用规则评分")
            return self.score_rule_based(content, context)
        
        try:
            # 构建 Prompt
            context_info = ""
            if context:
                emotion = context.get("emotion", "")
                source = context.get("source", "unknown")
                context_info = f"\n上下文：来源={source}, 情绪={emotion}"
            
            prompt = f"""请评估以下记忆的重要性（0.0-1.0），只返回一个数字：

记忆内容：{content[:200]}{context_info}

评分标准：
- 0.0-0.3：日常闲聊、无意义内容
- 0.3-0.6：普通信息、一般对话
- 0.6-0.8：重要事实、情感表达、个人偏好
- 0.8-1.0：关键信息、强烈情感、重要承诺

只输出数字（如 0.7），不要有其他文字。"""
            
            # 调用轻量模型
            result = await self.lite_model.generate(
                prompt,
                max_tokens=10,
                temperature=0.1
            )
            
            # 解析结果
            result = result.strip()
            # 提取数字
            import re
            match = re.search(r'\d+\.?\d*', result)
            if match:
                score = float(match.group())
                score = max(0.0, min(1.0, score))  # 限制在 0-1
                self.logger.debug(f"模型评分: {score:.2f} (原始输出: {result})")
                return score
            else:
                self.logger.warning(f"模型输出无法解析为数字: {result}，使用规则评分")
                return self.score_rule_based(content, context)
        
        except Exception as e:
            self.logger.error(f"模型评分失败: {e}，降级到规则评分")
            error_bus.report_error(
                e,
                ErrorContext(
                    module="importance_scorer",
                    function="score_with_model",
                    extra={"content_length": len(content) if content else 0}
                )
            )
            return self.score_rule_based(content, context)
    
    async def score(
        self,
        content: str,
        context: Dict[str, Any] = None,
        use_model: bool = True
    ) -> float:
        """
        计算记忆重要性分数（主入口）
        
        Args:
            content: 记忆内容
            context: 上下文信息
            use_model: 是否使用模型（False 则直接用规则）
            
        Returns:
            0-1 之间的重要性分数
        """
        if use_model:
            return await self.score_with_model(content, context)
        else:
            return self.score_rule_based(content, context)
    
    async def close(self):
        """关闭模型 — 只释放引用，不关闭共享的 lite_model 单例"""
        self.lite_model = None
        self._initialized = False
        self.logger.info("重要性评分器已关闭")
