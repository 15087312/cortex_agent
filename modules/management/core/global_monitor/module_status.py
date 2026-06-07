"""
1. 模块运行状态监控

监控各模块是否正常启动、运行、休眠
检测模块卡死、崩溃、无响应、死循环
"""
from typing import Dict, Any
from .base_recorder import BaseRecorder


class ModuleStatusRecorder(BaseRecorder):
    """模块运行状态记录器"""

    def __init__(self):
        super().__init__("module_status", max_records=100)
        # 当前状态（只保留最新）
        self.current_status: Dict[str, Any] = {}

    def record_status(
        self,
        module_name: str,
        status: str,
        details: Dict[str, Any] = None
    ) -> None:
        """
        记录模块状态
        
        Args:
            module_name: 模块名称
            status: 状态 (running, idle, error, crashed)
            details: 详细信息
        """
        self.current_status[module_name] = {
            "status": status,
            "last_update": self.records[-1]["timestamp"] if self.records else None,
            "details": details or {}
        }
        
        super().add_record({
            "module_name": module_name,
            "status": status,
            "details": details or {}
        })

    def get_status(self, module_name: str = None) -> Dict[str, Any]:
        """
        获取模块状态
        
        Args:
            module_name: 模块名称（可选，不传返回所有）
            
        Returns:
            模块状态字典
        """
        if module_name:
            return self.current_status.get(module_name, {})
        return self.current_status.copy()

    def check_health(self) -> Dict[str, Any]:
        """
        检查模块健康度
        
        Returns:
            健康检查结果
        """
        unhealthy_modules = []
        for name, status in self.current_status.items():
            if status.get("status") in ["error", "crashed"]:
                unhealthy_modules.append(name)
        
        return {
            "healthy": len(unhealthy_modules) == 0,
            "total_modules": len(self.current_status),
            "unhealthy_modules": unhealthy_modules
        }
