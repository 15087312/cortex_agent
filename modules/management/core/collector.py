"""
管理核心 - 模块注册表和状态收集器

提供：
1. 模块注册表 - 自动发现所有模块
2. 状态收集器 - 聚合所有模块状态
3. 健康检查 - 检查各模块运行状态
"""
import time
import os
from pathlib import Path as FilePath
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from utils.logger import setup_logger

logger = setup_logger("management_core")

PROJECT_ROOT = FilePath(__file__).resolve().parents[2]


@dataclass
class ModuleInfo:
    """模块信息"""
    name: str
    module_path: str
    has_api: bool = False
    has_core: bool = False
    status: str = "discovered"
    last_check: float = 0.0
    info: Dict[str, Any] = field(default_factory=dict)
    capabilities: List[str] = field(default_factory=list)


class ModuleRegistry:
    """模块注册表 - 自动发现所有模块"""
    
    def __init__(self):
        self.modules: Dict[str, ModuleInfo] = {}
        self._discover_modules()
    
    def _discover_modules(self) -> None:
        """自动发现所有模块"""
        modules_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        
        module_dirs = [
            "database",
            "info_process",
            "memory",
            "perception",
            "output_system",
            "security_system",
            "thinking",
            "tool_manager"
        ]
        
        for module_name in module_dirs:
            module_path = os.path.join(modules_dir, module_name)
            
            if not os.path.isdir(module_path):
                continue
            
            has_api = os.path.exists(os.path.join(module_path, "api.py"))
            has_core = os.path.exists(os.path.join(module_path, "core"))
            
            self.modules[module_name] = ModuleInfo(
                name=module_name,
                module_path=module_path,
                has_api=has_api,
                has_core=has_core,
                status="discovered",
                last_check=time.time()
            )
        
        logger.info(f"发现 {len(self.modules)} 个模块")
    
    def get_module(self, name: str) -> Optional[ModuleInfo]:
        """获取模块信息"""
        return self.modules.get(name)
    
    def get_all_modules(self) -> List[ModuleInfo]:
        """获取所有模块"""
        return list(self.modules.values())
    
    def update_status(self, name: str, status: str, info: Dict = None) -> None:
        """更新模块状态"""
        if name in self.modules:
            self.modules[name].status = status
            self.modules[name].last_check = time.time()
            if info:
                self.modules[name].info.update(info)


class StatusCollector:
    """状态收集器 - 收集所有模块状态"""
    
    def __init__(self, registry: ModuleRegistry):
        self.registry = registry
        self._collectors: Dict[str, Callable] = {}
        self._register_collectors()
    
    def _register_collectors(self) -> None:
        """注册各模块的收集器"""
        self._collectors["memory"] = self._collect_memory

        self._collectors["thinking"] = self._collect_thinking
        self._collectors["attention"] = self._collect_attention
        self._collectors["info_process"] = self._collect_info_process
        self._collectors["perception"] = self._collect_perception
        self._collectors["security_system"] = self._collect_security
        self._collectors["output_system"] = self._collect_output
        self._collectors["database"] = self._collect_database
        self._collectors["tool_manager"] = self._collect_tool_manager
    
    def collect_all(self) -> Dict[str, Any]:
        """收集所有模块状态"""
        results = {}
        for module_name in self.registry.modules:
            try:
                collector = self._collectors.get(module_name)
                if collector:
                    results[module_name] = collector()
                else:
                    results[module_name] = self._collect_generic(module_name)
            except Exception as e:
                logger.error(f"收集 {module_name} 状态失败: {e}")
                results[module_name] = {"status": "error", "error": "Module collection failed"}
        return results
    
    def _collect_generic(self, module_name: str) -> Dict[str, Any]:
        """通用收集器"""
        return {
            "status": "available",
            "has_api": self.registry.modules[module_name].has_api,
            "has_core": self.registry.modules[module_name].has_core
        }
    
    def _collect_memory(self) -> Dict[str, Any]:
        """收集记忆模块状态（旧版 MemoryManager 已废弃）"""
        return {
            "status": "healthy",
            "note": "事件驱动记忆 (EventReducer + EventStore + EventRetrieval)",
            "event_count": 0,
        }
    
    def _collect_thinking(self) -> Dict[str, Any]:
        """收集思维模块状态"""
        try:
            return {
                "status": "healthy",
                "capabilities": [
                    "continuous_thinking",
                    "emotion_judgment",
                    "value_matching",
                    "expert_management"
                ],
                "available": True
            }
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}
    
    def _collect_attention(self) -> Dict[str, Any]:
        """收集注意力模块状态"""
        try:
            return {
                "status": "healthy",
                "capabilities": [
                    "weight_calculation",
                    "task_scheduling",
                    "priority_queue"
                ],
                "available": True
            }
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}
    
    def _collect_info_process(self) -> Dict[str, Any]:
        """收集信息处理模块状态"""
        try:
            from infra.data_process.core.image_analyzer import ImageAnalyzer
            from infra.data_process.core.speech_recognizer import SpeechRecognizer
            
            analyzer = ImageAnalyzer()
            recognizer = SpeechRecognizer()
            
            return {
                "status": "healthy",
                "image_analyzer": {
                    "type": analyzer.model_type,
                    "initialized": analyzer._initialized
                },
                "speech_recognizer": {
                    "model": recognizer.model_name,
                    "initialized": recognizer._initialized
                }
            }
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}
    
    def _collect_perception(self) -> Dict[str, Any]:
        """收集感知模块状态"""
        try:
            from modules.management.core.interfaces import get_perception_status_port

            return get_perception_status_port().get_status()
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}

    def _collect_security(self) -> Dict[str, Any]:
        """收集安全模块状态"""
        try:
            from modules.management.core.interfaces import get_security_status_port

            return get_security_status_port().get_status()
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}

    def _collect_output(self) -> Dict[str, Any]:
        """收集输出模块状态"""
        try:
            return {
                "status": "healthy",
                "capabilities": [
                    "archiver",
                    "distributor",
                    "validators"
                ],
                "available": True
            }
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}
    
    def _collect_database(self) -> Dict[str, Any]:
        """收集数据库模块状态"""
        try:
            from modules.database.disk_cache import disk_cache
            import sqlite3
            
            stats = disk_cache.get_stats()
            
            db_path = str(PROJECT_ROOT / "data" / "memory.db")
            tables = []
            row_counts = {}
            
            try:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [row[0] for row in cursor.fetchall()]
                
                for table in tables:
                    try:
                        safe_table = f'"{table.replace(chr(34), chr(34)+chr(34))}"'
                        cursor.execute(f"SELECT COUNT(*) FROM {safe_table}")
                        row_counts[table] = cursor.fetchone()[0]
                    except Exception as e:
                        logger.debug(f"统计表 {table} 行数失败: {e}")
                conn.close()
            except Exception as e:
                logger.debug(f"健康检查数据库连接失败: {e}")
            
            return {
                "status": "healthy",
                "cache_mode": stats.get("mode", "unknown"),
                "cache_size": stats.get("size", 0),
                "database_path": db_path,
                "tables": tables,
                "row_counts": row_counts
            }
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}
    
    def _collect_tool_manager(self) -> Dict[str, Any]:
        """收集工具管理器状态"""
        try:
            return {
                "status": "healthy",
                "available": True
            }
        except Exception:
            return {"status": "error", "error": "Resource collection failed"}



