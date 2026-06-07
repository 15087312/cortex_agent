"""
告警管理器 - 统一管理所有监控领域的告警

提供告警的添加、查询、过滤、限制功能。
"""
import time
from typing import Dict, Any, List, Optional
from .base_recorder import BaseRecorder


class AlertManager(BaseRecorder):
    """告警管理器"""

    def __init__(self):
        super().__init__("alert", max_records=100)

    def add_alert(
        self,
        level: str,
        message: str,
        details: Dict[str, Any] = None
    ) -> None:
        """
        添加告警
        
        Args:
            level: 告警级别 (critical, warning)
            message: 告警消息
            details: 详细信息
        """
        super().add_record({
            "level": level,
            "message": message,
            "details": details or {}
        })

    def get_alerts(self, level: str = None) -> List[Dict[str, Any]]:
        """
        获取告警
        
        Args:
            level: 告警级别过滤（可选）
            
        Returns:
            告警列表
        """
        alerts = self.get_records(limit=100)
        
        if level:
            alerts = [a for a in alerts if a.get("level") == level]
        
        return alerts

    def get_summary(self) -> Dict[str, int]:
        """
        获取告警摘要
        
        Returns:
            告警统计
        """
        alerts = self.get_records(limit=100)
        
        return {
            "total": len(alerts),
            "critical": sum(1 for a in alerts if a.get("level") == "critical"),
            "warning": sum(1 for a in alerts if a.get("level") == "warning")
        }
