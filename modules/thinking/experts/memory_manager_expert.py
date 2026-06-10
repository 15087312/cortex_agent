"""
记忆管理员专家 (MemoryManagerExpert) — 常驻 RuntimeExpert

负责:
1. 持续监控 Blackboard，自动将重要对话归档到长期记忆
2. 定期维护：压缩去重、清理过期、重建 FAISS 索引

不负责记忆搜索（该功能由 MemorySearchExpert 提供）
"""
import json
import time
from typing import Dict, Any, List, Optional

from modules.thinking.experts.base import RuntimeExpert, register_runtime_expert
from utils.logger import setup_logger

logger = setup_logger("memory_manager_expert")


class MemoryManagerExpert(RuntimeExpert):
    """记忆管理员 — 常驻专家，管理所有模型的记忆

    继承 RuntimeExpert，startup="persistent"，不自动退出。
    """

    template_key = "expert_memory_manager"

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

        # 上次维护时间
        self._last_maintenance = 0.0

        # 归档阈值：Blackboard 中消息的重要度超过此值自动归档
        self._archive_threshold = 0.5

        logger.info(
            f"[MemoryManagerExpert] 初始化完成: "
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
        """主处理循环:
        1. 自动归档 Blackboard 中的重要消息
        2. 定期维护（每 30 分钟）

        Returns:
            处理结果文本
        """
        results = []

        # 1. 自动归档
        archive_count = self._auto_archive_dialog(dialog_context)
        if archive_count > 0:
            results.append(f"归档了 {archive_count} 条记忆")

        # 2. 定期维护
        maintenance_msg = self._periodic_maintenance()
        if maintenance_msg:
            results.append(maintenance_msg)

        if results:
            return " | ".join(results)
        return ""

    # ------------------------------------------------------------------
    # Per-model MemoryManager
    # ------------------------------------------------------------------

    def get_or_create_model_memory(
        self,
        model_id: str,
        tier: str = "expert",
    ) -> Any:
        """获取或创建指定模型的 MemoryManager

        Args:
            model_id: 模型唯一标识
            tier: 模型层级 (large/supervisor/expert)

        Returns:
            MemoryManager 实例
        """
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
            f"[MemoryManagerExpert] 创建模型记忆: "
            f"model={model_id}, tier={tier}, "
            f"modules={[k for k, v in {
                'short': config.enable_short_term,
                'long': config.enable_long_term,
                'personality': config.enable_personality,
                'blackbox': config.enable_blackbox,
                'notebook': config.enable_notebook,
            }.items() if v]}"
        )
        return mm

    def get_model_memory(self, model_id: str) -> Optional[Any]:
        """获取已存在的模型 MemoryManager（不创建）"""
        return self._model_memories.get(model_id)

    # ------------------------------------------------------------------
    # 自动归档
    # ------------------------------------------------------------------

    def _auto_archive_dialog(self, dialog_context: str) -> int:
        """从 Blackboard 上下文中自动检测重要消息并归档到长期记忆"""
        if not dialog_context or not self._get_dialog():
            return 0

        count = 0
        try:
            entries = self._get_dialog().read_dialog(limit=20)
            for entry in entries:
                entry_id = entry.get("entry_id", "")
                content = entry.get("content", "")
                tier = entry.get("tier", "")
                model_id = entry.get("model_id", "")

                # 跳过大模型自己的消息（主会话已有短期记忆）
                if tier == "large":
                    continue

                # 评估重要度
                importance = self._assess_importance(content, tier)

                if importance >= self._archive_threshold:
                    # 归档到对应模型的长期记忆
                    mm = self.get_or_create_model_memory(
                        model_id or "unknown", tier or "expert"
                    )
                    if mm and mm.long_term:
                        mm.save_long_term(
                            memory_type="thought",
                            content={
                                "text": content[:2000],
                                "model_id": model_id,
                                "tier": tier,
                                "importance": importance,
                                "source": "auto_archive",
                                "timestamp": time.time(),
                            },
                            save_to_blackbox=False,
                        )
                        count += 1

        except Exception as e:
            logger.debug(f"自动归档异常: {e}")

        return count

    @staticmethod
    def _assess_importance(content: str, tier: str) -> float:
        """评估消息的重要度（简单启发式）"""
        if not content:
            return 0.0

        score = 0.0
        content_lower = content.lower()

        # 长度加分
        if len(content) > 200:
            score += 0.3
        if len(content) > 500:
            score += 0.2

        # 关键词加分
        important_keywords = [
            "结论", "决定", "错误", "修复", "方案", "架构", "安全",
            "性能", "优化", "总结", "最终", "重要", "关键", "必须",
            "bug", "fix", "error", "critical", "security",
        ]
        for kw in important_keywords:
            if kw in content_lower:
                score += 0.15

        # 主管的消息视为更重要
        if tier == "supervisor":
            score += 0.2

        return min(score, 1.0)

    # ------------------------------------------------------------------
    # 定期维护
    # ------------------------------------------------------------------

    def _periodic_maintenance(self) -> str:
        """每 30 分钟执行一次维护"""
        now = time.time()
        if now - self._last_maintenance < 1800:
            return ""

        self._last_maintenance = now
        compacted = 0

        for mid, mm in self._model_memories.items():
            try:
                if mm.long_term:
                    # 压缩去重
                    for mem_type in ["dialog", "thought", "summary", "event"]:
                        removed = mm.long_term.compact(mem_type)
                        compacted += removed
            except Exception as e:
                logger.debug(f"维护模型 [{mid}] 记忆失败: {e}")

        if compacted > 0:
            logger.info(f"[MemoryManagerExpert] 维护完成: 压缩 {compacted} 条重复记录")

        return f"维护: 压缩 {compacted} 条" if compacted > 0 else ""

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """获取状态（扩展基类）"""
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
            "last_maintenance": self._last_maintenance,
            "archive_threshold": self._archive_threshold,
        })
        return status


# 注册：让 ModelRunner 根据 role="memory_manager" 自动激活 MemoryManagerExpert
register_runtime_expert("memory_manager", MemoryManagerExpert)
