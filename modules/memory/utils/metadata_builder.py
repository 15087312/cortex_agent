"""
记忆元数据生成工具
"""
from typing import Dict, Any


class MetadataBuilder:
    """记忆元数据构建器"""
    
    @staticmethod
    def build(content: str, source: str = "user") -> Dict[str, Any]:
        """构建记忆元数据"""
        return {
            "source": source,
            "length": len(content),
            "language": "zh-CN"
        }
