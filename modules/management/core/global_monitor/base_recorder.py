"""
基础记录器 - 所有监控记录器的基类

提供通用的记录管理、数量限制、时间戳等功能。
"""
import time
from typing import Dict, Any, List, Optional
from utils.logger import setup_logger


class BaseRecorder:
    """
    基础记录器
    
    核心功能：
    - 记录管理（添加、查询、限制数量）
    - 时间戳自动添加
    - 日志记录
    """

    def __init__(self, name: str, max_records: int = 500):
        """
        初始化基础记录器
        
        Args:
            name: 记录器名称（用于日志）
            max_records: 最大记录数量（防止内存溢出）
        """
        self.name = name
        self.max_records = max_records
        self.records: List[Dict[str, Any]] = []
        self.logger = setup_logger(f"monitor.{name}")

    def add_record(self, data: Dict[str, Any]) -> None:
        """
        添加记录
        
        Args:
            data: 记录数据
        """
        record = {
            **data,
            "timestamp": time.time()
        }
        self.records.append(record)
        
        # 限制数量
        if len(self.records) > self.max_records:
            self.records = self.records[-self.max_records:]

    def get_records(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        获取记录
        
        Args:
            limit: 返回数量限制
            
        Returns:
            最近的记录列表
        """
        return self.records[-limit:]

    def get_count(self) -> int:
        """获取记录总数"""
        return len(self.records)

    def clear(self) -> None:
        """清空所有记录"""
        self.records.clear()
        self.logger.info(f"{self.name} 记录已清空")

    def get_latest(self) -> Optional[Dict[str, Any]]:
        """获取最新记录"""
        if not self.records:
            return None
        return self.records[-1]
