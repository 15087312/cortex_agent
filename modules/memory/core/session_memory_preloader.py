"""
会话记忆预加载器 — 在用户输入任何内容之前，把全局记忆和当前项目记忆全部加载好

参照 Claude 的会话启动预加载设计:
- 4路并行读取，200ms 内完成
- 预加载结果存入 GCM session_context，供后续管线阶段使用
- 所有操作 fire-and-forget，不阻塞会话创建
"""
import asyncio
import time
from typing import Dict, Any, Optional
from utils.logger import setup_logger

logger = setup_logger("session_memory_preloader")


class SessionMemoryPreloader:
    """会话启动时预加载核心记忆 — 让模型"一上来就认识用户" """

    def __init__(self, memory_manager=None, gcm_pool=None):
        self.memory = memory_manager
        self.gcm_pool = gcm_pool

    async def preload(self, session_id: str) -> Dict[str, Any]:
        """并行加载 4 类核心记忆

        Returns:
            {
                "preferences": {...},       # 用户偏好 + 价值观
                "recent_tasks": {...},      # 最近任务 + 记事本
                "project_state": {...},     # 项目全局状态
                "global_lessons": {...},    # 全局经验教训
                "preloaded_at": float,      # 时间戳
            }
        """
        start = time.time()
        results = await asyncio.gather(
            self._load_user_preferences(),
            self._load_recent_tasks(),
            self._load_project_state(),
            self._load_global_lessons(),
            return_exceptions=True,
        )

        prefs, tasks, proj_state, lessons = results
        preloaded = {
            "preferences": prefs if not isinstance(prefs, Exception) else {},
            "recent_tasks": tasks if not isinstance(tasks, Exception) else {},
            "project_state": proj_state if not isinstance(proj_state, Exception) else {},
            "global_lessons": lessons if not isinstance(lessons, Exception) else {},
            "preloaded_at": time.time(),
        }

        elapsed_ms = (time.time() - start) * 1000
        logger.info(
            f"[T1] 会话 {session_id[:8]} 记忆预加载完成 "
            f"({elapsed_ms:.0f}ms): "
            f"prefs={bool(preloaded['preferences'])} "
            f"tasks={bool(preloaded['recent_tasks'])} "
            f"state={bool(preloaded['project_state'])} "
            f"lessons={bool(preloaded['global_lessons'])}"
        )

        # GCM 已移除，不再写入会话上下文
        if self.gcm_pool:
            pass

        return preloaded

    # ── 4路并行读取 ──

    async def _load_user_preferences(self) -> Dict[str, Any]:
        """读取用户偏好: personality traits + values + speaking style"""
        if not self.memory:
            return {}
        result = {}
        try:
            result["personality"] = self.memory.get_personality()
        except Exception as e:
            logger.debug("预加载用户性格失败 (非致命): %s", e)
        try:
            result["values"] = self.memory.get_values()
        except Exception as e:
            logger.debug("预加载用户价值观失败 (非致命): %s", e)
        return result

    async def _load_recent_tasks(self) -> Dict[str, Any]:
        """读取最近任务: notebook + 常用工具"""
        if not self.memory:
            return {}
        result = {}
        try:
            result["notebook"] = self.memory.notebook_build_context(max_lines=20)
        except Exception as e:
            logger.debug("预加载记事本失败 (非致命): %s", e)
        try:
            result["top_tools"] = self.memory.get_top_tools(limit=3)
        except Exception as e:
            logger.debug("预加载常用工具失败 (非致命): %s", e)
        return result

    async def _load_project_state(self) -> Dict[str, Any]:
        """读取项目全局状态 (从 GCM)"""
        if not self.gcm_pool:
            return {}
        try:
            state = self.gcm_pool.get_state()
            return {"project_state": state.__dict__ if state and hasattr(state, '__dict__') else {}}
        except Exception as e:
            logger.debug("预加载项目状态失败 (非致命): %s", e)
            return {}

    async def _load_global_lessons(self) -> Dict[str, Any]:
        """读取全局经验教训: long_term summaries + evolution"""
        if not self.memory:
            return {}
        result = {}
        try:
            result["summaries"] = self.memory.load_long_term("summary", limit=10)
        except Exception as e:
            logger.debug("预加载长期摘要失败 (非致命): %s", e)
        try:
            result["evolution"] = self.memory.load_long_term("evolution", limit=5)
        except Exception as e:
            logger.debug("预加载经验教训失败 (非致命): %s", e)
        return result


def format_preloaded_context(preloaded: Dict[str, Any]) -> str:
    """将预加载的记忆格式化为可注入 prompt 的文本"""
    parts = []

    preferences = preloaded.get("preferences", {})
    if preferences:
        personality = preferences.get("personality", {})
        values = preferences.get("values", {})
        if personality:
            traits = personality if isinstance(personality, dict) else {}
            trait_str = ", ".join(
                f"{k}={v}" for k, v in traits.items()
                if not k.startswith("_")
            )
            if trait_str:
                parts.append(f"用户画像: {trait_str}")
        if values:
            vals_str = ", ".join(
                f"{k}={v}" for k, v in values.items() if not k.startswith("_")
            )
            if vals_str:
                parts.append(f"用户价值观: {vals_str}")

    recent_tasks = preloaded.get("recent_tasks", {})
    if recent_tasks:
        notebook = recent_tasks.get("notebook", "")
        if notebook and notebook.strip():
            parts.append(f"当前任务记事本:\n{notebook}")
        top_tools = recent_tasks.get("top_tools", [])
        if top_tools:
            tool_names = [t.get("name", "?") for t in top_tools[:3]]
            parts.append(f"常用工具: {', '.join(tool_names)}")

    global_lessons = preloaded.get("global_lessons", {})
    if global_lessons:
        summaries = global_lessons.get("summaries", [])
        if summaries:
            summary_texts = [
                s.get("content", "")[:200]
                for s in summaries[:3]
                if isinstance(s, dict)
            ]
            parts.append(f"全局经验: {'; '.join(summary_texts)}")

    return "\n".join(parts) if parts else ""
