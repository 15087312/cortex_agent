"""
5. 输入输出全过程监控

监控用户输入解析是否正确
输出内容是否合规、是否偏离任务
输出延迟、响应速度（单轮 ≤300ms）
"""
from typing import Dict, Any, List, Optional
from .base_recorder import BaseRecorder


class IOMonitorRecorder(BaseRecorder):
    """输入输出全过程监控记录器"""

    def __init__(self):
        super().__init__("io_operation", max_records=300)
        self.max_latency_ms = 300  # 最大延迟 300ms

    def record_operation(
        self,
        operation: str,
        input_data: str,
        output_data: str = None,
        latency_ms: float = None
    ) -> None:
        """
        记录输入输出操作
        
        Args:
            operation: 操作类型 (input, output)
            input_data: 输入数据
            output_data: 输出数据
            latency_ms: 延迟（毫秒）
        """
        super().add_record({
            "operation": operation,
            "input_preview": input_data[:200],  # 只保留前 200 字符
            "output_preview": output_data[:200] if output_data else None,
            "latency_ms": latency_ms
        })

    def check_latency(self) -> List[Dict[str, Any]]:
        """
        检查延迟
        
        Returns:
            延迟告警列表
        """
        alerts = []
        latest = self.get_latest()
        
        if latest and latest.get("latency_ms") and latest["latency_ms"] > self.max_latency_ms:
            alerts.append({
                "level": "warning",
                "message": f"响应延迟过高: {latest['latency_ms']:.0f}ms (目标 ≤{self.max_latency_ms}ms)",
                "latency_ms": latest["latency_ms"]
            })
        
        return alerts

    def get_status(self) -> Dict[str, Any]:
        """获取 IO 状态"""
        return {
            "total_records": self.get_count(),
            "recent_records": self.get_records(limit=5),
            "alerts": self.check_latency()
        }
