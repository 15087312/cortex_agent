"""
2. 内存与硬件资源监控

监控总内存占用、模型内存占用、CPU/GPU 使用率
防止内存溢出、卡死、变慢
"""
import psutil
import os
from typing import Dict, Any, List
from .base_recorder import BaseRecorder


class ResourceMonitor:
    """内存与硬件资源监控器"""

    def __init__(self, max_snapshots: int = 100):
        """
        初始化资源监控器
        
        Args:
            max_snapshots: 最大快照数量
        """
        self.recorder = BaseRecorder("resource", max_records=max_snapshots)
        self.thresholds = {
            "memory_warning": 0.85,      # 85% 内存使用率告警
            "memory_critical": 0.95,     # 95% 内存使用率危险
            "cpu_warning": 0.90          # 90% CPU 使用率告警
        }

    def capture_snapshot(self) -> Dict[str, Any]:
        """
        捕获资源快照
        
        Returns:
            资源快照
        """
        process = psutil.Process(os.getpid())
        memory_info = process.memory_info()
        system_memory = psutil.virtual_memory()
        
        snapshot = {
            "process": {
                "rss_mb": memory_info.rss / 1024 / 1024,  # 物理内存
                "vms_mb": memory_info.vms / 1024 / 1024,  # 虚拟内存
                "cpu_percent": process.cpu_percent(interval=0.1)
            },
            "system": {
                "memory_total_gb": system_memory.total / 1024 / 1024 / 1024,
                "memory_used_gb": system_memory.used / 1024 / 1024 / 1024,
                "memory_percent": system_memory.percent / 100,
                "cpu_percent": psutil.cpu_percent(interval=0.1),
                "cpu_count": psutil.cpu_count()
            }
        }
        
        self.recorder.add_record(snapshot)
        return snapshot

    def get_latest_snapshot(self) -> Dict[str, Any]:
        """获取最新快照"""
        if not self.recorder.records:
            return self.capture_snapshot()
        return self.recorder.get_latest()

    def check_thresholds(self) -> List[Dict[str, Any]]:
        """
        检查资源阈值
        
        Returns:
            告警列表
        """
        alerts = []
        snapshot = self.get_latest_snapshot()
        
        memory_percent = snapshot["system"]["memory_percent"]
        cpu_percent = snapshot["system"]["cpu_percent"] / 100
        
        if memory_percent > self.thresholds["memory_critical"]:
            alerts.append({
                "level": "critical",
                "message": f"内存使用率危险: {memory_percent:.1%}",
                "value": memory_percent
            })
        elif memory_percent > self.thresholds["memory_warning"]:
            alerts.append({
                "level": "warning",
                "message": f"内存使用率告警: {memory_percent:.1%}",
                "value": memory_percent
            })
        
        if cpu_percent > self.thresholds["cpu_warning"]:
            alerts.append({
                "level": "warning",
                "message": f"CPU 使用率告警: {cpu_percent:.1%}",
                "value": cpu_percent
            })
        
        return alerts

    def get_status(self) -> Dict[str, Any]:
        """获取资源状态"""
        snapshot = self.get_latest_snapshot()
        alerts = self.check_thresholds()
        
        return {
            "current": snapshot,
            "alerts": alerts
        }
