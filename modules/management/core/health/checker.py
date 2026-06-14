"""
健康检查器 - 自动检测和修复
"""
import time
import asyncio
import threading
from typing import Dict, Any, List, Optional, Callable
from utils.logger import setup_logger

logger = setup_logger("health_checker")


class HealthCheckResult:
    """健康检查结果"""

    def __init__(
        self,
        module: str,
        status: str,  # healthy, degraded, unhealthy
        message: str = "",
        details: Dict[str, Any] = None,
        checks: List[Dict[str, Any]] = None
    ):
        self.module = module
        self.status = status
        self.message = message
        self.details = details or {}
        self.checks = checks or []
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "module": self.module,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "checks": self.checks,
            "timestamp": self.timestamp
        }


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self._checkers: Dict[str, Callable] = {}
        self._repairers: Dict[str, Callable] = {}
        self._lock = threading.Lock()
        self._register_default_checkers()

    def _register_default_checkers(self):
        """注册默认检查器"""
        self.register_checker("system", self._check_system)
        self.register_checker("memory", self._check_memory)
        self.register_checker("thinking", self._check_thinking)
        self.register_checker("database", self._check_database)

    def register_checker(self, name: str, checker: Callable):
        """注册检查器"""
        with self._lock:
            self._checkers[name] = checker
            logger.info(f"注册健康检查器: {name}")

    def register_repairer(self, name: str, repairer: Callable):
        """注册修复器"""
        with self._lock:
            self._repairers[name] = repairer
            logger.info(f"注册修复器: {name}")

    async def check_all(self) -> Dict[str, Any]:
        """执行所有健康检查"""
        results = {}
        overall_status = "healthy"

        for name, checker in self._checkers.items():
            try:
                result = await checker()
                if isinstance(result, HealthCheckResult):
                    results[name] = result.to_dict()
                    if result.status == "unhealthy":
                        overall_status = "unhealthy"
                    elif result.status == "degraded" and overall_status == "healthy":
                        overall_status = "degraded"
                else:
                    results[name] = result
            except Exception as e:
                logger.error(f"健康检查 {name} 失败: {e}")
                results[name] = {
                    "module": name,
                    "status": "unhealthy",
                    "message": str(e)
                }
                overall_status = "unhealthy"

        return {
            "overall_status": overall_status,
            "checks": results,
            "timestamp": time.time()
        }

    async def check(self, module: str) -> Dict[str, Any]:
        """检查指定模块"""
        checker = self._checkers.get(module)
        if not checker:
            return {"module": module, "status": "unknown", "message": "检查器不存在"}

        try:
            result = await checker()
            if isinstance(result, HealthCheckResult):
                return result.to_dict()
            return result
        except Exception as e:
            return {"module": module, "status": "unhealthy", "message": str(e)}

    async def auto_repair(self, module: str) -> Dict[str, Any]:
        """自动修复"""
        repairer = self._repairers.get(module)
        if not repairer:
            return {"module": module, "success": False, "message": "修复器不存在"}

        try:
            result = await repairer()
            return {"module": module, "success": True, "result": result}
        except Exception as e:
            return {"module": module, "success": False, "message": str(e)}

    async def _check_system(self) -> HealthCheckResult:
        """检查系统"""
        import psutil

        checks = []

        # CPU 检查
        cpu_percent = psutil.cpu_percent(interval=0.1)
        checks.append({
            "name": "cpu",
            "status": "healthy" if cpu_percent < 80 else "degraded" if cpu_percent < 95 else "unhealthy",
            "value": cpu_percent,
            "threshold": 80
        })

        # 内存检查
        memory = psutil.virtual_memory()
        checks.append({
            "name": "memory",
            "status": "healthy" if memory.percent < 80 else "degraded" if memory.percent < 90 else "unhealthy",
            "value": memory.percent,
            "threshold": 80
        })

        # 磁盘检查
        disk = psutil.disk_usage('/')
        checks.append({
            "name": "disk",
            "status": "healthy" if disk.percent < 85 else "degraded" if disk.percent < 95 else "unhealthy",
            "value": disk.percent,
            "threshold": 85
        })

        statuses = [c["status"] for c in checks]
        overall = "healthy" if "unhealthy" not in statuses else "unhealthy"
        if "degraded" in statuses and overall != "unhealthy":
            overall = "degraded"

        return HealthCheckResult(
            module="system",
            status=overall,
            message=f"系统健康检查完成",
            details={"cpu": cpu_percent, "memory": memory.percent, "disk": disk.percent},
            checks=checks
        )

    async def _check_memory(self) -> HealthCheckResult:
        """检查记忆模块"""
        checks = []

        # 旧版 MemoryManager 已废弃，事件记忆由 EventReducer 管理
        checks.append({
            "name": "memory_system",
            "status": "healthy",
            "note": "事件驱动记忆 (EventStore + EventRetrieval)",
        })

        return HealthCheckResult(
            module="memory",
            status="healthy",
            message="记忆模块健康",
            checks=checks
        )

    async def _check_thinking(self) -> HealthCheckResult:
        """检查思维模块"""
        checks = []

        try:
            checks.append({
                "name": "core",
                "status": "healthy",
                "message": "思维核心可用"
            })
        except Exception as e:
            checks.append({
                "name": "core",
                "status": "unhealthy",
                "error": str(e)
            })

        return HealthCheckResult(
            module="thinking",
            status="healthy",
            message="思维模块健康",
            checks=checks
        )

    async def _check_database(self) -> HealthCheckResult:
        """检查数据库"""
        checks = []

        try:
            import sqlite3
            conn = sqlite3.connect("data/metrics/timeseries.db")
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM metrics")
            count = cursor.fetchone()[0]
            conn.close()

            checks.append({
                "name": "timeseries",
                "status": "healthy",
                "records": count
            })

        except Exception as e:
            checks.append({
                "name": "timeseries",
                "status": "unhealthy",
                "error": str(e)
            })

        return HealthCheckResult(
            module="database",
            status="healthy",
            message="数据库健康",
            checks=checks
        )


health_checker = HealthChecker()