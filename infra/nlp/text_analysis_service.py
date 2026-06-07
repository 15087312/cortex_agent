"""
文本分析服务 - 封装模型调用的核心实现

提供情感分析、命名实体识别、摘要生成等 NLP 能力。
内部管理大小模型客户端，提供完善的降级机制。
"""
from typing import Dict, Any, List, Optional
import asyncio
import json
import re

from config.model_config import get_large_model_config, get_small_model_config
from infra.model.small_model_client import SmallModelClient
from infra.model.large_model_client import LargeModelClient
from infra.nlp.base_nlp_service import BaseNLPService
from utils.logger import setup_logger


class TextAnalysisService(BaseNLPService):
    """
    文本分析服务
    
    封装大小模型调用，提供高层次的文本分析 API。
    支持降级方案，确保服务可用性。
    """
    
    # 情感分析提示词模板
    SENTIMENT_PROMPT_TEMPLATE = """请分析以下文本的情感倾向，只返回以下三个选项之一：positive（积极）、negative（消极）、neutral（中性）。

文本："{text}"

情感倾向："""
    
    # NER 提示词模板（更简单的格式，适配小模型）
    NER_PROMPT_TEMPLATE = """提取文本中的人名和地点。每行一个，格式：名字|类型

文本：{text}

结果："""
    
    # 摘要生成提示词模板（小模型版本）
    SUMMARY_PROMPT_TEMPLATE = """请为以下文本生成简洁的摘要，保留关键信息，长度控制在50字以内。

文本：
{text}

摘要："""
    
    def __init__(self):
        """
        初始化文本分析服务
        """
        self.small_model_client: Optional[SmallModelClient] = None
        self.large_model_client: Optional[LargeModelClient] = None
        self.logger = setup_logger("text_analysis_service")
        self._initialized = False
    
    async def initialize(self) -> None:
        """
        初始化模型客户端
            
        加载大小模型配置并初始化对应的客户端。
        如果环境变量未配置，会记录警告但允许继续运行（使用降级方案）。
        """
        if self._initialized:
            self.logger.debug("TextAnalysisService 已初始化，跳过重复初始化")
            return
            
        try:
            # 初始化小模型客户端
            self.small_model_client = SmallModelClient.from_config()
            self.logger.info("SmallModelClient 初始化成功")
        except Exception as e:
            self.logger.error("SmallModelClient 初始化失败：%s", e)
            self.small_model_client = None
            
        try:
            # 初始化大模型客户端
            self.large_model_client = LargeModelClient.from_config()
            self.logger.info("LargeModelClient 初始化成功")
        except Exception as e:
            self.logger.error("LargeModelClient 初始化失败：%s", e)
            self.large_model_client = None
            
        self._initialized = True
        self.logger.info("TextAnalysisService 初始化完成")
    
    async def analyze_sentiment(self, text: str) -> str:
        """
        情感分析
        
        使用大模型 API 进行情感分析。
        
        Args:
            text: 待分析文本
            
        Returns:
            "positive" | "negative" | "neutral"
        """
        if not self._initialized:
            await self.initialize()
        
        # 使用大模型
        if self.large_model_client:
            try:
                prompt = self.SENTIMENT_PROMPT_TEMPLATE.format(text=text[:500])
                response = await self.large_model_client.generate(
                    prompt,
                    max_tokens=10,
                    temperature=0.1
                )
                
                response_lower = response.lower().strip()
                
                if "positive" in response_lower or "积极" in response:
                    return "positive"
                elif "negative" in response_lower or "消极" in response:
                    return "negative"
                elif "neutral" in response_lower or "中性" in response:
                    return "neutral"
                    
            except Exception as e:
                self.logger.warning("大模型情感分析失败: %s，使用降级方案", e)
        
        return self._sentiment_by_keywords(text)
    
    def _sentiment_by_keywords(self, text: str) -> str:
        """
        基于关键词的情感分析（降级方案）
        
        通过统计积极/消极词汇数量判断情感倾向。
        """
        positive_words = [
            "好", "优秀", "喜欢", "爱", "棒", "赞", "开心", "快乐",
            "满意", "完美", "精彩", "成功", "幸福", "美好"
        ]
        negative_words = [
            "坏", "讨厌", "不喜欢", "差", "糟", "难过", "失望",
            "失败", "痛苦", "糟糕", "恶心", "愤怒", "悲伤", "差劲"
        ]
        
        score = sum(1 for word in positive_words if word in text)
        score -= sum(1 for word in negative_words if word in text)
        
        if score > 0:
            return "positive"
        elif score < 0:
            return "negative"
        else:
            return "neutral"
    
    async def extract_entities(self, text: str) -> List[Dict[str, str]]:
        """
        提取命名实体
        
        使用 jieba 进行词性标注，识别人名和地名。
        
        Args:
            text: 待分析文本
            
        Returns:
            实体列表，每个实体包含 text 和 type 字段
        """
        try:
            import jieba
            import jieba.posseg as pseg
            
            entities = []
            words = pseg.cut(text)
            
            for word, flag in words:
                if len(word) < 2:
                    continue
                if flag == 'nr':  # 人名
                    entities.append({"text": word, "type": "PERSON"})
                elif flag == 'ns':  # 地名
                    entities.append({"text": word, "type": "LOCATION"})
                elif flag == 'nt':  # 机构名
                    entities.append({"text": word, "type": "ORGANIZATION"})
            
            self.logger.debug("提取到 %d 个命名实体", len(entities))
            return entities
            
        except ImportError:
            self.logger.warning("jieba 未安装，无法提取实体")
            return []
        except Exception as e:
            self.logger.warning("实体提取失败: %s", e)
            return []
    
    def _parse_ner_response(self, response: str) -> List[Dict[str, str]]:
        """解析 NER 响应，支持多种格式"""
        entities = []
        
        # 尝试解析 "实体名|类型" 格式（每行一个）
        for line in response.strip().split('\n'):
            line = line.strip()
            if not line or line.count('|') < 1:
                continue
            
            parts = line.split('|', 1)
            if len(parts) >= 2:
                entity_text = parts[0].strip()
                entity_type = parts[1].strip().upper()
                
                if not entity_text or not entity_type:
                    continue
                
                # 标准化类型
                type_mapping = {
                    'PERSON': 'PERSON', '人名': 'PERSON', '人': 'PERSON',
                    'LOCATION': 'LOCATION', '地点': 'LOCATION', '地': 'LOCATION',
                    'ORGANIZATION': 'ORGANIZATION', '组织': 'ORGANIZATION', '机构': 'ORGANIZATION',
                    'TIME': 'TIME', '时间': 'TIME',
                    'MISC': 'MISC', '其他': 'MISC'
                }
                
                if entity_type in type_mapping:
                    entities.append({
                        "text": entity_text,
                        "type": type_mapping[entity_type]
                    })
        
        # 如果上面没解析到，尝试 JSON 格式
        if not entities:
            json_match = re.search(r'\[.*\]', response, re.DOTALL)
            if json_match:
                try:
                    json_str = json_match.group()
                    parsed_entities = json.loads(json_str)
                    
                    for entity in parsed_entities:
                        if isinstance(entity, dict) and "text" in entity and "type" in entity:
                            entities.append({
                                "text": str(entity["text"]),
                                "type": str(entity["type"])
                            })
                except Exception as e:
                    self.logger.debug(f"JSON 实体解析失败，跳过: {e}")
        
        return entities
    
    async def generate_summary(self, text: str, use_large: bool = False) -> str:
        """
        生成文本摘要
        
        使用本地抽取式摘要（小模型效果差）。
        
        Args:
            text: 待分析文本
            use_large: 是否强制使用大模型
            
        Returns:
            摘要文本
        """
        if not self._initialized:
            await self.initialize()
        
        if len(text) <= 100:
            return text
        
        # 直接使用本地抽取式摘要
        return self._truncate_summary(text)
    
    async def _generate_with_small(self, text: str) -> str:
        """使用小模型生成摘要"""
        try:
            truncated_text = text[:500] if len(text) > 500 else text
            prompt = self.SUMMARY_PROMPT_TEMPLATE.format(text=truncated_text)
            
            summary = await self.small_model_client.generate(
                prompt,
                max_tokens=80,
                temperature=0.3
            )
            
            summary = summary.strip()
            if summary:
                self.logger.debug("小模型生成摘要成功: %s...", summary[:30])
                return summary
                
        except Exception as e:
            self.logger.warning("小模型摘要生成失败: %s，尝试大模型", e)
        
        if self.large_model_client:
            return await self._generate_with_large(text)
        
        return self._truncate_summary(text)
    
    async def _generate_with_large(self, text: str) -> str:
        """使用大模型生成摘要"""
        try:
            truncated_text = text[:2000] if len(text) > 2000 else text
            prompt = self.SUMMARY_PROMPT_TEMPLATE.format(text=truncated_text)
            
            summary = await self.large_model_client.generate(
                prompt,
                max_tokens=100,
                temperature=0.5
            )
            
            summary = summary.strip()
            if summary:
                self.logger.debug("大模型生成摘要成功: %s...", summary[:30])
                return summary
                
        except Exception as e:
            self.logger.warning("大模型摘要生成失败: %s，使用降级方案", e)
        
        return self._truncate_summary(text)
    
    def _truncate_summary(self, text: str, max_length: int = 100) -> str:
        """
        截断生成摘要（降级方案）
        
        在句子边界处截断文本，避免切断词语。
        """
        if len(text) <= max_length:
            return text
        
        # 尝试在句子边界截断
        truncated = text[:max_length]
        
        # 查找最后一个句子结束符
        for sep in [".", "。", "!", "！", "?", "？", ";", "；"]:
            last_sep = truncated.rfind(sep)
            if last_sep > max_length * 0.5:  # 至少保留一半内容
                return truncated[:last_sep + 1]
        
        # 如果没有合适的句子边界，直接截断并添加省略号
        return truncated + "..."
    
    async def close(self) -> None:
        """
        关闭模型客户端资源
        
        正确关闭所有模型客户端，释放连接资源。
        """
        self.logger.info("正在关闭 TextAnalysisService 资源...")
        
        close_tasks = []
        
        if self.small_model_client:
            close_tasks.append(self._safe_close_client(self.small_model_client, "SmallModelClient"))
        
        if self.large_model_client:
            close_tasks.append(self._safe_close_client(self.large_model_client, "LargeModelClient"))
        
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        
        self._initialized = False
        self.small_model_client = None
        self.large_model_client = None
        self.logger.info("TextAnalysisService 资源已关闭")
    
    async def _safe_close_client(self, client, client_name: str) -> None:
        """
        安全关闭客户端
        
        包装关闭操作，确保异常不会传播。
        """
        try:
            await client.close()
            self.logger.debug("%s 已关闭", client_name)
        except Exception as e:
            self.logger.warning("关闭 %s 时发生错误: %s", client_name, e)
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.initialize()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
