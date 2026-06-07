"""
健康检查工具
"""
from typing import Dict, List, Optional, Any
import asyncio
from datetime import datetime


class HealthChecker:
    """健康检查器"""
    
    def __init__(self, timeout: int = 5):
        self.timeout = timeout
        self.check_results: Dict[str, Dict] = {}
    
    async def check_service(
        self,
        service_name: str,
        check_func: callable,
        critical: bool = True
    ) -> bool:
        """检查服务健康状态"""
        start_time = datetime.now()
        
        try:
            result = await asyncio.wait_for(check_func(), timeout=self.timeout)
            elapsed = (datetime.now() - start_time).total_seconds()
            
            self.check_results[service_name] = {
                "status": "healthy" if result else "unhealthy",
                "response_time": elapsed,
                "last_check": datetime.now().isoformat(),
                "critical": critical
            }
            
            return result
        except asyncio.TimeoutError:
            self.check_results[service_name] = {
                "status": "timeout",
                "response_time": self.timeout,
                "last_check": datetime.now().isoformat(),
                "critical": critical
            }
            return False
        except Exception as e:
            self.check_results[service_name] = {
                "status": "error",
                "error": str(e),
                "last_check": datetime.now().isoformat(),
                "critical": critical
            }
            return False
    
    async def check_all(
        self,
        services: Dict[str, callable]
    ) -> Dict[str, bool]:
        """检查所有服务"""
        tasks = [
            self.check_service(name, func)
            for name, func in services.items()
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        health_status = {}
        for i, (name, result) in enumerate(zip(services.keys(), results)):
            if isinstance(result, Exception):
                health_status[name] = False
            else:
                health_status[name] = result
        
        return health_status
    
    def get_overall_status(self) -> str:
        """获取整体健康状态"""
        if not self.check_results:
            return "unknown"
        
        critical_services = [
            name for name, data in self.check_results.items()
            if data.get("critical", False)
        ]
        
        unhealthy_critical = [
            name for name in critical_services
            if self.check_results[name]["status"] != "healthy"
        ]
        
        if unhealthy_critical:
            return "critical"
        
        all_healthy = all(
            data["status"] == "healthy"
            for data in self.check_results.values()
        )
        
        return "healthy" if all_healthy else "degraded"
    
    def get_health_report(self) -> Dict[str, Any]:
        """获取健康报告"""
        return {
            "overall_status": self.get_overall_status(),
            "services": self.check_results,
            "timestamp": datetime.now().isoformat()
        }
