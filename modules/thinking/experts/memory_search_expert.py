"""记忆搜索专家 (MemorySearchExpert) — on_demand，响应 memory_search 请求

从 MemoryManagerExpert 拆分而来，只负责记忆检索，不负责归档和维护。
"""

import json
import time
from typing import Dict, Any, List, Optional

from modules.thinking.experts.base import RuntimeExpert, register_runtime_expert
from utils.logger import setup_logger

logger = setup_logger("memory_search_expert")


class MemorySearchExpert(RuntimeExpert):
    """记忆搜索专家 — 按需激活，响应 memory_search 请求"""

    template_key = "expert_memory_searcher"

    def __init__(self, model_instance=None, blackboard=None,
                 session_id="", model_id=""):
        super().__init__(
            model_instance=model_instance,
            blackboard=blackboard,
            session_id=session_id,
            model_id=model_id,
        )
        # per-model MemoryManager 实例
        self._model_memories: Dict[str, Any] = {}
        logger.info(
            f"[MemorySearchExpert] 初始化完成: "
            f"session={session_id[:16] if session_id else '?'}"
        )

    # ------------------------------------------------------------------
    # RuntimeExpert 抽象方法实现
    # ------------------------------------------------------------------

    async def process(
        self,
        request_text: str,
        messages: List[Dict[str, Any]],
        dialog_context: str,
    ) -> str:
        """只处理 memory_search 请求"""
        result = self._handle_search_requests(messages)
        return result or ""

    # ------------------------------------------------------------------
    # Per-model MemoryManager
    # ------------------------------------------------------------------

    def get_or_create_model_memory(
        self,
        model_id: str,
        tier: str = "expert",
    ) -> Any:
        """获取或创建指定模型的 MemoryManager"""
        if model_id in self._model_memories:
            return self._model_memories[model_id]

        from modules.memory.core.memory_manager import MemoryManager
        from modules.memory.core.memory_config import get_default_config

        config = get_default_config(tier, model_id)
        mm = MemoryManager(
            data_dir="data/memory",
            model_id=model_id,
            config=config,
        )

        if self.session_id:
            mm.set_session_id(self.session_id)

        self._model_memories[model_id] = mm
        logger.info(
            f"[MemorySearchExpert] 创建模型记忆: "
            f"model={model_id}, tier={tier}"
        )
        return mm

    def get_model_memory(self, model_id: str) -> Optional[Any]:
        """获取已存在的模型 MemoryManager（不创建）"""
        return self._model_memories.get(model_id)

    # ------------------------------------------------------------------
    # memory_search 请求处理
    # ------------------------------------------------------------------

    def _handle_search_requests(self, messages: List[Dict[str, Any]]) -> str:
        """检查 MessageBus 消息中是否有 memory_search 请求并处理"""
        for msg in messages:
            content = str(msg.get("content", ""))
            msg_type = msg.get("type", "")

            if "【memory_search】" in content or msg_type == "memory_search":
                params = self._parse_search_params(msg)
                if params:
                    result = self.search(
                        query=params.get("query", ""),
                        category=params.get("category"),
                        time_range=params.get("time_range", "7d"),
                        limit=params.get("limit", 10),
                        model_id=params.get("model_id"),
                    )
                    return self._format_search_result(result)
        return ""

    @staticmethod
    def _parse_search_params(msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从消息中解析搜索参数"""
        content = str(msg.get("content", ""))

        params = {
            "query": "",
            "category": None,
            "time_range": "7d",
            "limit": 10,
            "model_id": None,
        }

        metadata = msg.get("metadata", {})
        if isinstance(metadata, dict):
            for key in params:
                if key in metadata:
                    params[key] = metadata[key]

        if "【memory_search】" in content and not params["query"]:
            query_part = content.split("【memory_search】", 1)[1].strip()
            if query_part:
                params["query"] = query_part[:500]

        if not params["query"]:
            return None
        return params

    # ------------------------------------------------------------------
    # 检索 API
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        category: str = None,
        time_range: str = "7d",
        limit: int = 10,
        model_id: str = None,
    ) -> List[Dict[str, Any]]:
        """按分类和时间范围检索记忆"""
        all_results = []

        if model_id:
            mm = self.get_or_create_model_memory(model_id, tier="expert")
            if mm:
                results = mm.search_memories_by_category(
                    query=query, category=category,
                    time_range=time_range, limit=limit,
                )
                for r in results:
                    r["_model_id"] = model_id
                all_results.extend(results)
        else:
            for mid, mm in self._model_memories.items():
                try:
                    results = mm.search_memories_by_category(
                        query=query, category=category,
                        time_range=time_range, limit=limit,
                    )
                    for r in results:
                        r["_model_id"] = mid
                    all_results.extend(results)
                except Exception as e:
                    logger.debug(f"搜索模型 [{mid}] 记忆失败: {e}")

        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return all_results[:limit]

    def _format_search_result(self, results: List[Dict[str, Any]]) -> str:
        """格式化搜索结果"""
        if not results:
            return "【memory_search 结果】未找到匹配的记忆。"

        lines = [f"【memory_search 结果】共 {len(results)} 条匹配:\n"]
        for i, r in enumerate(results, 1):
            content = r.get("content", {})
            if isinstance(content, dict):
                text = content.get("text", content.get("content", str(content)))[:300]
            else:
                text = str(content)[:300]
            model_id = r.get("_model_id", "?")
            mem_type = r.get("type", "unknown")
            score = r.get("score", 0)
            lines.append(f"{i}. [{model_id}/{mem_type}] score={score:.2f} | {text}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        status = super().get_status()
        model_statuses = {}
        for mid, mm in self._model_memories.items():
            try:
                model_statuses[mid] = mm.get_summary()
            except Exception:
                model_statuses[mid] = "error"
        status.update({
            "model_memories": model_statuses,
            "model_count": len(self._model_memories),
        })
        return status


# 注册
register_runtime_expert("memory_searcher", MemorySearchExpert)
