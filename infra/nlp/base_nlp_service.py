"""
NLP 服务抽象基类 - 定义文本分析服务接口
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseNLPService(ABC):
    """
    NLP 服务抽象基类
    
    定义文本分析相关的高层接口，具体实现由子类完成。
    """
    
    @abstractmethod
    async def analyze_sentiment(self, text: str) -> str:
        """
        情感分析
        
        Args:
            text: 待分析文本
            
        Returns:
            "positive" | "negative" | "neutral"
        """
        pass
    
    @abstractmethod
    async def extract_entities(self, text: str) -> List[Dict[str, str]]:
        """
        命名实体识别
        
        Args:
            text: 待分析文本
            
        Returns:
            实体列表，每个实体包含 text 和 type 字段
        """
        pass
    
    @abstractmethod
    async def generate_summary(self, text: str) -> str:
        """
        生成文本摘要
        
        Args:
            text: 待分析文本
            
        Returns:
            摘要文本
        """
        pass
    
    @abstractmethod
    async def close(self) -> None:
        """
        关闭服务，释放资源
        """
        pass
    
    async def __aenter__(self):
        """异步上下文管理器入口"""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()
