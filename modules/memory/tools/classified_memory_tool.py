"""
分类记忆工具 - 供AI使用的记忆分类和检索工具
"""
from typing import Dict, List, Any, Optional
from infra.tool_manager.tool_registry import ToolRegistry
from modules.memory.classification_memory import ClassificationMemory
from utils.logger import setup_logger


class ClassifiedMemoryTool:
    """分类记忆工具集合"""

    def __init__(self, memory_system: ClassificationMemory):
        self.memory_system = memory_system
        self.logger = setup_logger("classified_memory_tool")
        self._register_tools()

    def _register_tools(self):
        """注册所有分类记忆相关的工具"""

        @ToolRegistry.register(
            name="save_memory_to_category",
            description="将信息保存到指定的记忆类别中。类别包括：skills(技能)、communication(沟通)、knowledge(知识)、experience(经验)、preferences(偏好)、general(通用)。",
            params={
                "category": "记忆类别: skills, communication, knowledge, experience, preferences, general",
                "content": "要保存的记忆内容",
                "importance": "重要性评分 0.0-1.0，默认为0.5"
            },
            risk_level="LOW",
            category="memory"
        )
        def save_memory_to_category(category: str, content: str, importance: float = 0.5) -> Dict[str, Any]:
            """保存记忆到指定类别"""
            try:
                metadata = {"importance": importance}
                memory_id = self.memory_system.save_memory(category, content, metadata)
                return {
                    "success": True,
                    "message": f"记忆已保存到'{category}'类别",
                    "memory_id": memory_id
                }
            except Exception as e:
                self.logger.error(f"保存记忆失败: {e}")
                return {"success": False, "error": str(e)}

        @ToolRegistry.register(
            name="search_memory_by_category",
            description="根据查询内容和记忆类别搜索相关记忆。memory_age可选：short(30分钟内)、mid(30分钟-7天)、long(7天以上)、all(全部)。",
            params={
                "query": "搜索查询内容",
                "category": "记忆类别（可选）",
                "memory_age": "记忆年龄: short, mid, long, all，默认all",
                "limit": "返回结果数量，默认10"
            },
            risk_level="LOW",
            category="memory"
        )
        def search_memory_by_category(
                query: str,
                category: Optional[str] = None,
                memory_age: str = "all",
                limit: int = 10
        ) -> Dict[str, Any]:
            """按类别搜索记忆"""
            try:
                results = self.memory_system.search_memories_by_category(
                    query=query,
                    category=category,
                    memory_age=memory_age,
                    limit=limit
                )
                return {
                    "success": True,
                    "results_count": len(results),
                    "memories": results
                }
            except Exception as e:
                self.logger.error(f"搜索记忆失败: {e}")
                return {"success": False, "error": str(e)}

        @ToolRegistry.register(
            name="get_memory_categories",
            description="获取系统支持的所有记忆类别列表。",
            params={},
            risk_level="LOW",
            category="query"
        )
        def get_memory_categories() -> Dict[str, Any]:
            """获取所有记忆类别"""
            try:
                categories = self.memory_system.get_all_categories()
                return {"success": True, "categories": categories}
            except Exception as e:
                self.logger.error(f"获取类别失败: {e}")
                return {"success": False, "error": str(e)}

        @ToolRegistry.register(
            name="get_memory_stats",
            description="获取各类别记忆的数量统计。",
            params={},
            risk_level="LOW",
            category="query"
        )
        def get_memory_stats() -> Dict[str, Any]:
            """获取记忆统计"""
            try:
                stats = self.memory_system.get_category_stats()
                return {"success": True, "stats": stats}
            except Exception as e:
                self.logger.error(f"获取统计失败: {e}")
                return {"success": False, "error": str(e)}


# 全局实例
_classification_memory = ClassificationMemory(data_dir="./data/classified_memories")
classified_memory_tool = ClassifiedMemoryTool(_classification_memory)
