"""
记忆管理器 - 聚合所有记忆组件

AI 的大脑海马体 + 阅历 + 性格底色 + 黑匣子 + 记事本 + 分类记忆
分七层：短期、上下文、长期、人格、黑匣子、记事本、分类记忆

支持 per-model 隔离：每个模型有独立的记忆目录和模块配置。
通过 MemoryConfig 控制各模块开关，通过 model_id 隔离存储。
"""
import os
import time
from typing import Dict, Any, List, Optional
from utils.logger import setup_logger

from .core import MemoryCore
from .short_term import ShortTermMemory
from .long_term import LongTermMemory
from .personality import PersonalityMemory
from .blackbox import BlackboxMemory
from .notebook import AINotebook
from .memory_config import MemoryConfig, get_default_config


class MemoryManager:
    """
    记忆管理器 - 聚合所有记忆组件

    核心职责：
    - 统一管理七层记忆（新增分类记忆）
    - 提供统一的读写接口
    - 协调各层记忆的数据流
    - 支持自进化
    - 支持 per-model 隔离
    """

    # 实例缓存：避免每次请求重新初始化所有子模块
    _instances: Dict[str, "MemoryManager"] = {}
    _instances_lock = __import__("threading").Lock()

    @classmethod
    def get_instance(cls, model_id: str = "", **kwargs) -> "MemoryManager":
        """获取或创建缓存的 MemoryManager 实例（按 model_id 隔离）"""
        cache_key = model_id or "__default__"
        with cls._instances_lock:
            if cache_key not in cls._instances:
                cls._instances[cache_key] = cls(model_id=model_id, **kwargs)
            return cls._instances[cache_key]

    @classmethod
    def clear_cache(cls) -> None:
        """清空实例缓存（用于测试或配置变更）"""
        with cls._instances_lock:
            cls._instances.clear()

    def __init__(
            self,
            data_dir: str = "data/memory",
            max_context_turns: int = 20,
            max_working_items: int = 50,
            model_id: str = "",
            config: Optional[MemoryConfig] = None,
    ):
        """
        初始化记忆管理器

        Args:
            data_dir: 数据存储目录
            max_context_turns: 最大对话轮次（仅用于兼容，实际由数据库控制）
            max_working_items: 最大工作记忆项（仅用于兼容，实际由数据库控制）
            model_id: 模型唯一标识，用于记忆隔离
            config: 记忆模块配置（None 则使用默认全开配置）
        """
        self.logger = setup_logger("memory_manager")
        self.model_id = model_id
        self.session_id = ""
        self.owner = model_id or ""
        self.config = config or MemoryConfig(model_id=model_id)

        if model_id and not self.config.model_id:
            self.config.model_id = model_id

        # 核心工具
        self.core = MemoryCore(data_dir=data_dir)

        # 六层记忆 - 使用基础设施层
        from modules.database.interface import get_database_port
        self._database = get_database_port()
        self.short_term_repo = self._database.short_term_repo  # SQLite + diskcache

        # 长期记忆直接使用 JSONL 实现（不经过 repository）
        self.long_term_repo = None

        # —— 按 config 选择性加载模块 ——
        self.short_term = self._ShortTermAdapter(self.short_term_repo, self._database)
        if model_id:
            self.short_term.set_owner(model_id)

        # 长期记忆：per-model 目录隔离
        if self.config.enable_long_term:
            long_term_dir = self._get_long_term_dir(data_dir)
            self.long_term = LongTermMemory(
                data_dir=str(long_term_dir),
                enable_rag=self.config.enable_semantic_search,
            )
        else:
            self.long_term = None

        # 人格记忆：仅 large 默认开启
        if self.config.enable_personality:
            self.personality = PersonalityMemory(config_file=f"{data_dir}/personality.json")
        else:
            self.personality = None

        # 黑匣子：可选
        if self.config.enable_blackbox:
            self.blackbox = BlackboxMemory(data_dir=f"{data_dir}/blackbox")
        else:
            self.blackbox = None

        # 记事本：可选
        if self.config.enable_notebook:
            self.notebook = AINotebook(data_dir=f"{data_dir}/notebook")
        else:
            self.notebook = None

        # 分类记忆：可选（新增）。RAG 保持延迟初始化，避免服务启动阶段阻塞在外部模型下载/网络探测。
        if self.config.enable_classified_memory:
            from modules.memory.classification_memory import ClassificationMemory
            classified_dir = f"{data_dir}/classified/{model_id}" if model_id else f"{data_dir}/classified"
            self.classified_memory = ClassificationMemory(
                data_dir=classified_dir,
                enable_rag=False,
            )
        else:
            self.classified_memory = None

        # 工具熟练度（惯性）
        self.tool_skills_file = f"{data_dir}/tool_skills.json"
        self.tool_skills = self._load_tool_skills()

        enabled_modules = [
            k for k, v in {
                "short_term": self.config.enable_short_term,
                "long_term": self.config.enable_long_term,
                "classified_memory": self.config.enable_classified_memory,
                "personality": self.config.enable_personality,
                "blackbox": self.config.enable_blackbox,
                "notebook": self.config.enable_notebook,
                "semantic_search": self.config.enable_semantic_search,
            }.items() if v
        ]
        self.logger.info(
            "记忆管理器初始化完成 (model=%s, data_dir=%s, modules=%s)",
            model_id or "default", data_dir, enabled_modules,
        )

    def _get_long_term_dir(self, data_dir: str) -> str:
        """获取 per-model 长期记忆目录"""
        if self.config.memory_dir:
            return self.config.memory_dir
        if self.model_id:
            return f"{data_dir}/long_term/expert_memories/{self.model_id}"
        return f"{data_dir}/long_term"

    class _ShortTermAdapter:
        """
        短期记忆适配器 - 将 Repository 接口适配为 ShortTermMemory 接口

        保持向后兼容，让上层代码无需修改
        """

        def __init__(self, repo, database):
            self.repo = repo
            self._database = database
            self._session_id: str = ""
            self._owner: str = ""

        def set_session_id(self, session_id: str):
            self._session_id = session_id

        def set_owner(self, owner: str):
            self._owner = owner

        def _cache_key(self, key: str) -> str:
            """生成带 session_id 前缀的缓存 key，防止多轮污染"""
            if self._session_id:
                return f"{self._session_id}:{key}"
            return key

        def add_dialog(self, role: str, text: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
            """添加对话到短期记忆（SQLite + diskcache）"""

            owner = self._owner
            if not owner:
                owner = metadata.get("owner", "") if metadata else ""
            if not owner:
                owner = "user" if role == "user" else "assistant"

            memory_id = self.repo.add(
                content=text,
                memory_type="dialog",
                importance=metadata.get("importance", 0.5) if metadata else 0.5,
                emotion=metadata.get("emotion", "") if metadata else "",
                source=role,
                owner=owner,
                session_id=self._session_id,
                tags=metadata.get("tags", []) if metadata else [],
                metadata=metadata
            )
            return {
                "id": memory_id,
                "role": role,
                "text": text,
                "timestamp": time.time(),
                "metadata": metadata or {}
            }

        def get_context(self, limit: int = None, max_age_seconds: int = None,
                        owner: str = None, session_id: str = None) -> List[Dict[str, Any]]:
            """获取对话上下文（按 session_id + owner 过滤）"""
            start_time = time.time()
            effective_owner = owner or self._owner or None
            if session_id is None:
                effective_session_id = self._session_id if self._session_id else None
            elif session_id == "":
                effective_session_id = None
            else:
                effective_session_id = session_id
            query = self._database.create_memory_query(
                memory_type="dialog",
                limit=limit or 20,
                max_age_seconds=max_age_seconds,
                session_id=effective_session_id,
                owner=effective_owner,
            )
            results = self.repo.query(query)
            duration_ms = (time.time() - start_time) * 1000

            return [
                {
                    "role": r.get("source", "unknown"),
                    "text": r.get("content", ""),
                    "timestamp": r.get("created_at", 0),
                    "metadata": r.get("extra_data", {}),
                    "owner": r.get("owner", ""),
                }
                for r in results
            ]

        def set_working_memory(self, key: str, value: Any, ttl: float = None) -> None:
            """设置工作记忆（使用 diskcache）"""
            self.repo.cache.set(f"working:{key}", value, prefix="cache", ttl=int(ttl) if ttl else 3600)

        def get_working_memory(self, key: str) -> Optional[Any]:
            """获取工作记忆"""
            return self.repo.cache.get(f"working:{key}", prefix="cache")

        def set_current_emotion(self, emotion: Dict[str, Any]) -> None:
            """设置当前情绪"""
            self.repo.cache.set(self._cache_key("current_emotion"), emotion, prefix="cache", ttl=3600)

        def get_current_emotion(self) -> Optional[Dict[str, Any]]:
            """获取当前情绪"""
            return self.repo.cache.get(self._cache_key("current_emotion"), prefix="cache")

        def set_current_task(self, task: Dict[str, Any]) -> None:
            """设置当前任务"""
            self.repo.cache.set(self._cache_key("current_task"), task, prefix="cache", ttl=7200)

        def get_current_task(self) -> Optional[Dict[str, Any]]:
            """获取当前任务"""
            return self.repo.cache.get(self._cache_key("current_task"), prefix="cache")

        def clear_all(self) -> None:
            """清空所有短期记忆"""
            owner = self._owner or None
            session_id = self._session_id or None
            self._database.deactivate_short_term(owner=owner, session_id=session_id)
            self.repo.cache.flush_prefix("short_term")
            self.repo.cache.flush_prefix("working")
            self.repo.cache.delete(self._cache_key("current_emotion"))
            self.repo.cache.delete(self._cache_key("current_task"))

        def get_status(self) -> Dict[str, Any]:
            """获取短期记忆状态"""
            try:
                total = self._database.count_active_short_term()

                context = self.get_context(limit=100)
                emotion = self.get_current_emotion()
                task = self.get_current_task()

                return {
                    "context_turns": len(context),
                    "working_items": 0,
                    "cached_thoughts": 0,
                    "current_emotion": emotion,
                    "current_task": task,
                    "total_memories": total
                }
            except Exception as e:
                self.logger.debug("获取短期记忆状态失败，返回默认值: %s", e)
                return {
                    "context_turns": 0,
                    "working_items": 0,
                    "cached_thoughts": 0,
                    "current_emotion": None,
                    "current_task": None,
                    "total_memories": 0
                }

        def get_all_working_memory(self) -> Dict[str, Any]:
            """获取所有工作记忆（兼容接口）"""
            return {}

    # ========== 工具熟练度 API ==========

    def _load_tool_skills(self) -> Dict[str, Any]:
        """加载工具熟练度"""
        import json
        try:
            if os.path.exists(self.tool_skills_file):
                with open(self.tool_skills_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            self.logger.debug("加载工具熟练度文件失败: %s", e)
        return {}

    def _save_tool_skills(self) -> None:
        """保存工具熟练度"""
        import json
        os.makedirs(os.path.dirname(self.tool_skills_file), exist_ok=True)
        try:
            with open(self.tool_skills_file, 'w', encoding='utf-8') as f:
                json.dump(self.tool_skills, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"保存工具熟练度失败: {e}")

    def _calculate_time_bonus(self, last_used: str) -> float:
        """计算时间奖励（最近使用给加分）"""
        from datetime import datetime, timedelta
        try:
            last_time = datetime.fromisoformat(last_used)
            days_ago = (datetime.now() - last_time).days
            if days_ago == 0:
                return 2.0
            elif days_ago == 1:
                return 1.5
            elif days_ago <= 7:
                return 1.0
            elif days_ago <= 30:
                return 0.3
            else:
                return 0.0
        except Exception as e:
            self.logger.debug("计算时间奖励失败，默认返回 0: %s", e)
            return 0.0

    def _calculate_prefer_score(self, tool_name: str) -> float:
        """计算工具偏好分数"""
        skill = self.tool_skills.get(tool_name, {})
        success_count = skill.get("success_count", 0)
        fail_count = skill.get("fail_count", 0)
        last_used = skill.get("last_used", "")

        score = success_count * 1.2 - fail_count * 0.5 + self._calculate_time_bonus(last_used)
        return max(0.0, score)

    def record_tool_success(self, tool_name: str) -> Dict[str, Any]:
        """记录工具使用成功"""
        from datetime import datetime

        if tool_name not in self.tool_skills:
            self.tool_skills[tool_name] = {
                "success_count": 0,
                "fail_count": 0,
                "last_used": "",
                "prefer_score": 0.0,
                "total_uses": 0
            }

        self.tool_skills[tool_name]["success_count"] += 1
        self.tool_skills[tool_name]["total_uses"] += 1
        self.tool_skills[tool_name]["last_used"] = datetime.now().isoformat()
        self.tool_skills[tool_name]["prefer_score"] = self._calculate_prefer_score(tool_name)

        self._save_tool_skills()

        return {
            "tool": tool_name,
            "prefer_score": self.tool_skills[tool_name]["prefer_score"],
            "success_count": self.tool_skills[tool_name]["success_count"]
        }

    def record_tool_failure(self, tool_name: str) -> Dict[str, Any]:
        """记录工具使用失败"""
        from datetime import datetime

        if tool_name not in self.tool_skills:
            self.tool_skills[tool_name] = {
                "success_count": 0,
                "fail_count": 0,
                "last_used": "",
                "prefer_score": 0.0,
                "total_uses": 0
            }

        self.tool_skills[tool_name]["fail_count"] += 1
        self.tool_skills[tool_name]["total_uses"] += 1
        self.tool_skills[tool_name]["last_used"] = datetime.now().isoformat()
        self.tool_skills[tool_name]["prefer_score"] = self._calculate_prefer_score(tool_name)

        self._save_tool_skills()

        return {
            "tool": tool_name,
            "prefer_score": self.tool_skills[tool_name]["prefer_score"],
            "fail_count": self.tool_skills[tool_name]["fail_count"]
        }

    def get_tool_skills(self) -> Dict[str, Dict[str, Any]]:
        """获取所有工具熟练度"""
        for tool_name in self.tool_skills:
            self.tool_skills[tool_name]["prefer_score"] = self._calculate_prefer_score(tool_name)
        return self.tool_skills

    def get_top_tools(self, limit: int = 5) -> List[Dict[str, Any]]:
        """获取最常用的工具（按熟练度排序）"""
        tools = []
        for tool_name in self.tool_skills:
            score = self._calculate_prefer_score(tool_name)
            skill = self.tool_skills[tool_name]
            tools.append({
                "name": tool_name,
                "prefer_score": score,
                "success_count": skill.get("success_count", 0),
                "fail_count": skill.get("fail_count", 0),
                "total_uses": skill.get("total_uses", 0),
                "last_used": skill.get("last_used", "")
            })

        tools.sort(key=lambda x: x["prefer_score"], reverse=True)
        return tools[:limit]

    def get_tool_preference_prompt(self) -> str:
        """获取工具偏好提示词"""
        top_tools = self.get_top_tools(limit=5)

        if not top_tools:
            return ""

        lines = ["【工具使用偏好】你有以下工具使用经验，优先选择熟练度高的工具："]

        for i, tool in enumerate(top_tools, 1):
            score = tool["prefer_score"]
            success = tool["success_count"]
            fail = tool["fail_count"]
            uses = tool["total_uses"]

            if score >= 10:
                level = "非常熟练"
            elif score >= 5:
                level = "比较熟练"
            elif score >= 2:
                level = "基本会用"
            else:
                level = "刚接触"

            lines.append(f"{i}. {tool['name']}（熟练度：{score:.1f}，成功{success}次/失败{fail}次）→ {level}")

        lines.append("请优先使用你最熟练的工具。")

        return "\n".join(lines)

    # ========== 短期记忆 API ==========

    def set_session_id(self, session_id: str):
        """设置当前会话 ID，用于记忆隔离"""
        self.session_id = session_id or ""
        self.short_term.set_session_id(self.session_id)

    def set_owner(self, owner: str):
        """设置当前记忆归属者 (model_id)，用于角色隔离"""
        self.owner = owner or ""
        self.short_term.set_owner(self.owner)

    def _build_memory_metadata(
        self,
        metadata: Optional[Dict[str, Any]] = None,
        scope: str = "shared",
        owner: Optional[str] = None,
        visible_to: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """构建统一记忆元数据，支持 shared/global/private 作用域。"""
        result = dict(metadata or {})
        effective_owner = owner or result.get("owner") or self.owner or self.model_id or "shared"
        result.setdefault("scope", scope)
        result.setdefault("owner", effective_owner)
        result.setdefault("session_id", self.session_id)
        if visible_to is not None:
            result["visible_to"] = visible_to
        elif "visible_to" not in result:
            result["visible_to"] = [effective_owner] if result.get("scope") == "private" else []
        if self.model_id:
            result.setdefault("source_model", self.model_id)
        return result

    def add_dialog(self, role: str, text: str, metadata: Dict[str, Any] = None) -> Dict[str, Any]:
        """添加对话到短期记忆"""
        dialog = self.short_term.add_dialog(role, text, metadata)

        if self.blackbox:
            self.blackbox.log_module_call(
                caller="user",
                callee="memory",
                action="add_dialog",
                details={"role": role, "text_length": len(text)}
            )

        return dialog

    def save_dialog_turn(
        self,
        user_input: str,
        assistant_response: str,
        metadata: Optional[Dict[str, Any]] = None,
        scope: str = "shared",
    ) -> Dict[str, Any]:
        """保存一轮用户-助手对话到短期记忆。"""
        base_metadata = self._build_memory_metadata(
            metadata=metadata,
            scope=scope,
            owner=self.owner or self.model_id or "shared",
        )
        base_metadata.setdefault("turn_type", "dialog_turn")

        user_metadata = dict(base_metadata)
        user_metadata["dialog_role"] = "user"
        assistant_metadata = dict(base_metadata)
        assistant_metadata["dialog_role"] = "assistant"

        user_memory = self.add_dialog("user", user_input, user_metadata)
        assistant_memory = self.add_dialog("assistant", assistant_response, assistant_metadata)

        return {
            "user": user_memory,
            "assistant": assistant_memory,
            "scope": base_metadata.get("scope"),
            "owner": base_metadata.get("owner"),
            "session_id": base_metadata.get("session_id"),
        }

    def get_context(self, limit: int = None, owner: str = None, session_id: str = None) -> List[Dict[str, Any]]:
        """获取对话上下文（支持按 owner + session_id 过滤）"""
        return self.short_term.get_context(limit, owner=owner, session_id=session_id)

    def set_working_memory(self, key: str, value: Any, ttl: float = None) -> None:
        """设置工作记忆"""
        self.short_term.set_working_memory(key, value, ttl)

    def get_working_memory(self, key: str) -> Optional[Any]:
        """获取工作记忆"""
        return self.short_term.get_working_memory(key)

    def set_current_emotion(self, emotion: Dict[str, Any]) -> None:
        """设置当前情绪"""
        self.short_term.set_current_emotion(emotion)

        if self.blackbox:
            self.blackbox.log_emotion(emotion)

    def get_current_emotion(self) -> Optional[Dict[str, Any]]:
        """获取当前情绪"""
        return self.short_term.get_current_emotion()

    def set_current_task(self, task: Dict[str, Any]) -> None:
        """设置当前任务"""
        self.short_term.set_current_task(task)

    def get_current_task(self) -> Optional[Dict[str, Any]]:
        """获取当前任务"""
        return self.short_term.get_current_task()

    def clear_short_term(self) -> None:
        """清空短期记忆"""
        self.short_term.clear_all()
        self.logger.info("短期记忆已清空")

    # ========== 长期记忆 API ==========

    def save_long_term(
            self,
            memory_type: str,
            content: Dict[str, Any],
            save_to_blackbox: bool = True
    ) -> Dict[str, Any]:
        """
        保存长期记忆（默认优先使用 embedding 语义存储，失败自动回退基础存储）

        行为：
        - 默认调用 save_with_embedding 生成向量索引
        - 若 embedding 不可用或抛出异常，静默回退到 save（纯 JSONL）
        - 调用方无需关心底层存储策略
        """
        if not self.long_term:
            return {"id": "", "type": memory_type, "error": "long_term disabled"}

        try:
            memory = self.long_term.save_with_embedding(memory_type, content)
        except Exception as e:
            self.logger.debug("embedding 存储失败，回退到基础存储: %s", e)
            memory = self.long_term.save(memory_type, content)

        if save_to_blackbox and self.blackbox:
            self.blackbox.log_module_call(
                caller="memory",
                callee="long_term",
                action="save",
                details={"type": memory_type, "id": memory["id"]}
            )

        return memory

    def load_long_term(self, memory_type: str, limit: int = 50) -> List[Dict[str, Any]]:
        """加载长期记忆"""
        if not self.long_term:
            return []
        return self.long_term.load(memory_type, limit)

    def search_long_term(self, memory_type: str, keywords: List[str], limit: int = 20) -> List[Dict[str, Any]]:
        """搜索长期记忆"""
        if not self.long_term:
            return []
        return self.long_term.search(memory_type, keywords, limit)

    def delete_long_term(self, memory_id: str, memory_type: str = None) -> int:
        """删除长期记忆"""
        if not self.long_term:
            return 0
        return self.long_term.delete(memory_id, memory_type)

    # ========== 记忆范围晋升 (Private → Shared/Global) ==========

    def promote_memory(
        self,
        memory_id: str,
        memory_type: str,
        target_scope: str = "shared",
    ) -> bool:
        """
        将指定长期记忆从 private 晋升为 shared/global。

        Args:
            memory_id: 记忆 ID
            memory_type: 记忆类型 (dialog/thought/summary/event)
            target_scope: 目标范围 ("shared" 或 "global")

        Returns:
            是否成功晋升
        """
        if not self.long_term or target_scope not in ("shared", "global"):
            return False

        try:
            memories = self.long_term.load(memory_type, limit=5000)
        except (ValueError, Exception):
            self.logger.warning("晋升失败：不支持的记忆类型 %s", memory_type)
            return False
        target = None
        for m in memories:
            if m.get("id") == memory_id:
                target = m
                break

        if not target:
            self.logger.warning("晋升失败：未找到记忆 %s [%s]", memory_id, memory_type)
            return False

        content = target.get("content", {})
        metadata = content if isinstance(content, dict) else {}
        current_scope = metadata.get("scope", "private")
        if current_scope == target_scope:
            return True

        metadata["scope"] = target_scope
        metadata["visible_to"] = []
        metadata["promoted_at"] = time.time()
        metadata["promoted_from"] = current_scope
        if isinstance(content, dict):
            content.update(metadata)

        target["content"] = content

        # BUG-3: Use storage abstraction instead of direct file write
        # This ensures embedding index stays in sync
        self.long_term.delete(memory_id, memory_type)
        # save() generates new ID and updates embeddings, ensuring consistency
        self.long_term.save(memory_type, target)

        self.logger.info(
            "记忆晋升: %s [%s]  %s → %s", memory_id, memory_type, current_scope, target_scope,
        )
        return True

    def promote_private_memories(
        self,
        memory_type: Optional[str] = None,
        target_scope: str = "shared",
        min_importance: float = 0.6,
        owner: Optional[str] = None,
        max_promote: int = 10,
    ) -> List[str]:
        """
        批量晋升符合条件的 private 记忆。

        筛选条件：
        - scope == "private"
        - importance >= min_importance
        - 若指定 owner，仅晋升属于该 owner 的记忆

        Args:
            memory_type: 记忆类型（None=扫描全部）
            target_scope: 目标范围
            min_importance: 最低重要度阈值
            owner: 所属者过滤
            max_promote: 单次最多晋升条数

        Returns:
            晋升成功的 memory_id 列表
        """
        if not self.long_term:
            return []

        types_to_check = [memory_type] if memory_type else ["dialog", "thought", "summary", "event"]
        promoted = []

        for mtype in types_to_check:
            memories = self.long_term.load(mtype, limit=5000)
            candidates = []
            for m in memories:
                content = m.get("content", {})
                meta = content if isinstance(content, dict) else {}
                if meta.get("scope") != "private":
                    continue
                if meta.get("importance", 0.5) < min_importance:
                    continue
                if owner and meta.get("owner") != owner:
                    continue
                candidates.append(m)

            for m in candidates[:max_promote]:
                mid = m.get("id", "")
                if self.promote_memory(mid, mtype, target_scope):
                    promoted.append(mid)

        return promoted

    # ========== 分类记忆 API ==========

    def save_classified_memory(self, category: str, content: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """保存记忆到分类系统"""
        if not self.classified_memory:
            return {"error": "分类记忆未启用"}

        try:
            normalized_metadata = self._build_memory_metadata(
                metadata=metadata,
                scope=(metadata or {}).get("scope", "global" if category in {"preferences", "knowledge"} else "private"),
            )
            memory_id = self.classified_memory.save_memory(category, content, normalized_metadata)
            return {
                "success": True,
                "memory_id": memory_id,
                "category": category
            }
        except Exception as e:
            self.logger.error(f"保存分类记忆失败: {e}")
            return {"error": str(e)}

    def search_classified_memory(self, query: str, category: str = None, memory_age: str = "all", limit: int = 10) -> \
    List[Dict[str, Any]]:
        """搜索分类记忆"""
        if not self.classified_memory:
            return []

        try:
            return self.classified_memory.search_memories_by_category(query, category, memory_age, limit)
        except Exception as e:
            self.logger.error(f"搜索分类记忆失败: {e}")
            return []

    def get_memory_categories(self) -> List[str]:
        """获取所有记忆类别"""
        if not self.classified_memory:
            return []

        try:
            return self.classified_memory.get_all_categories()
        except Exception as e:
            self.logger.error(f"获取记忆类别失败: {e}")
            return []

    def get_classified_memory_stats(self) -> Dict[str, int]:
        """获取分类记忆统计"""
        if not self.classified_memory:
            return {}

        try:
            return self.classified_memory.get_category_stats()
        except Exception as e:
            self.logger.error(f"获取分类记忆统计失败: {e}")
            return {}

    # ========== 人格记忆 API ==========

    def get_personality(self) -> Dict[str, Any]:
        """获取人格配置"""
        if not self.personality:
            return {"name": "unknown", "traits": {}, "values": {}}
        return self.personality.get_personality()

    def get_personality_trait(self, key: str, default: Any = None) -> Any:
        """获取人格特征"""
        if not self.personality:
            return default
        return self.personality.get_trait(key, default)

    def update_personality_trait(self, key: str, value: Any) -> None:
        """更新人格特征"""
        if not self.personality:
            return
        self.personality.update_trait(key, value)

        if self.blackbox:
            self.blackbox.log_module_call(
                caller="memory",
                callee="personality",
                action="update_trait",
                details={"key": key, "value": str(value)[:100]}
            )

    def get_values(self) -> Dict[str, float]:
        """获取价值观倾向"""
        if not self.personality:
            return {}
        return self.personality.get_values()

    def get_speaking_style(self) -> Dict[str, str]:
        """获取说话风格"""
        if not self.personality:
            return {}
        return self.personality.get_speaking_style()

    # ========== 黑匣子 API ==========

    def log_thinking(self, thought_chain: Dict[str, Any]) -> Dict[str, Any]:
        """记录思考过程"""
        if not self.blackbox:
            return {}
        return self.blackbox.log_thinking(thought_chain)

    def log_evolution(self, evolution_data: Dict[str, Any]) -> Dict[str, Any]:
        """记录自进化行为"""
        if not self.blackbox:
            return {}
        return self.blackbox.log_evolution(evolution_data)

    def log_error(self, error_data: Dict[str, Any]) -> Dict[str, Any]:
        """记录错误"""
        if not self.blackbox:
            return {}
        return self.blackbox.log_error(error_data)

    def get_blackbox_logs(
            self,
            log_type: str,
            limit: int = 50
    ) -> List[Dict[str, Any]]:
        """获取黑匣子日志"""
        if not self.blackbox:
            return []
        return self.blackbox.get_logs(log_type, limit)

    def get_timeline(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取时间线"""
        if not self.blackbox:
            return []
        return self.blackbox.get_timeline(limit=limit)

    # ========== 核心工具 API ==========

    def search_memories(
            self,
            keywords: List[str],
            memory_types: List[str] = None,
            limit: int = 20
    ) -> List[Dict[str, Any]]:
        """搜索所有类型的记忆"""
        results = []

        if self.long_term:
            types_to_search = memory_types or ["dialog", "thought", "summary", "event"]
            for mem_type in types_to_search:
                try:
                    memories = self.long_term.search(mem_type, keywords, limit=limit)
                    results.extend(memories)
                except Exception as e:
                    self.logger.warning("搜索长期记忆 [%s] 失败: %s", mem_type, e)

        if self.blackbox:
            try:
                logs = self.blackbox.search_logs("thinking", keywords, limit=limit)
                results.extend(logs)
            except Exception as e:
                self.logger.warning("搜索黑匣子日志失败: %s", e)

        results.sort(key=lambda x: x.get("search_score", 0), reverse=True)

        return results[:limit]

    def search_memories_by_category(
            self,
            query: str,
            category: str = None,
            time_range: str = "7d",
            limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """按分类检索记忆"""
        if not self.long_term:
            return []

        memory_types = [category] if category else None

        results = self.long_term.search_hybrid(
            query=query,
            memory_types=memory_types,
            expert_name=None,
            limit=limit,
            use_semantic=self.config.enable_semantic_search,
        )

        if time_range and time_range != "all":
            max_age = _parse_time_range(time_range)
            if max_age > 0:
                now = time.time()
                results = [r for r in results
                           if r.get("content", {}).get("timestamp", 0) and
                           now - float(r["content"].get("timestamp", 0)) <= max_age]

        normalized_results = []
        for item in results:
            if not isinstance(item, dict):
                continue
            content = item.get("content", {})
            metadata = item.get("metadata", {})
            if isinstance(content, dict):
                metadata = metadata or content.get("metadata", {}) or content.get("extra_data", {}) or {}
            if metadata:
                item = dict(item)
                item["metadata"] = metadata
            normalized_results.append(item)

        return normalized_results[:limit]

    def save_snapshot(self, snapshot_name: str) -> str:
        """保存记忆快照"""
        snapshot_data = {
            "short_term": self.short_term.get_status(),
            "long_term_stats": self.long_term.get_statistics() if self.long_term else {},
            "personality": self.personality.get_personality() if self.personality else {},
            "blackbox_stats": self.blackbox.get_statistics() if self.blackbox else {},
            "classified_memory_stats": self.get_classified_memory_stats() if self.classified_memory else {},
            "timestamp": time.time()
        }

        return self.core.save_snapshot(snapshot_name, snapshot_data)

    def get_status(self) -> Dict[str, Any]:
        """获取记忆模块综合状态"""
        return {
            "model_id": self.model_id,
            "short_term": self.short_term.get_status(),
            "long_term": self.long_term.get_statistics() if self.long_term else {},
            "personality": self.personality.get_status() if self.personality else {},
            "blackbox": self.blackbox.get_statistics() if self.blackbox else {},
            "notebook": self.notebook.get_statistics() if self.notebook else {},
            "classified_memory": self.get_classified_memory_stats() if self.classified_memory else {},
            "storage": self.core.get_storage_stats(),
            "enabled_modules": {
                "short_term": self.config.enable_short_term,
                "long_term": self.config.enable_long_term,
                "classified_memory": self.config.enable_classified_memory,
                "personality": self.config.enable_personality,
                "emotion": self.config.enable_emotion,
                "blackbox": self.config.enable_blackbox,
                "notebook": self.config.enable_notebook,
                "semantic_search": self.config.enable_semantic_search,
            },
        }

    def get_summary(self) -> str:
        """获取记忆状态摘要"""
        short_status = self.short_term.get_status()
        long_stats = self.long_term.get_statistics() if self.long_term else {}
        personality_name = (
            self.personality.get_personality().get('name', '未知')
            if self.personality else "未启用"
        )
        classified_stats = self.get_classified_memory_stats() if self.classified_memory else {}

        summary = (
            f"记忆状态 [{self.model_id or 'default'}] | "
            f"短期: {short_status['context_turns']} 轮对话, {short_status['working_items']} 工作项 | "
            f"长期: {long_stats.get('total_size_kb', 0):.1f}KB | "
            f"分类记忆: {sum(classified_stats.values()) if classified_stats else 0} 条 | "
            f"人格: {personality_name}"
        )

        return summary

    def reset_all(self) -> None:
        """重置所有记忆（谨慎使用）"""
        self.short_term.clear_all()
        self.logger.warning("所有记忆已重置")

    # ========== AI 记事本 API ==========

    def notebook_write_line(self, text: str) -> str:
        """追加一行到记事本"""
        if self.blackbox:
            self.blackbox.log_module_call(
                caller="memory",
                callee="notebook",
                action="write_line",
                details={"text_length": len(text)}
            )
        if not self.notebook:
            return ""
        return self.notebook.write_line(text)

    def notebook_write_block(self, title: str, content: str) -> None:
        """分块记录到记事本"""
        if not self.notebook:
            return
        self.notebook.write_block(title, content)
        if self.blackbox:
            self.blackbox.log_module_call(
                caller="memory",
                callee="notebook",
                action="write_block",
                details={"title": title, "content_length": len(content)}
            )

    def notebook_write_thought(self, thought: str) -> None:
        """记录思考到记事本"""
        if not self.notebook:
            return
        self.notebook.write_thought(thought)

    def notebook_write_task(self, task: str, status: str = "pending") -> None:
        """记录任务到记事本"""
        if not self.notebook:
            return
        self.notebook.write_task(task, status)

    def notebook_write_result(self, label: str, result: Any) -> None:
        """记录结果到记事本"""
        if not self.notebook:
            return
        self.notebook.write_result(label, result)

    def notebook_read_all(self) -> str:
        """读取记事本全部内容"""
        if not self.notebook:
            return ""
        return self.notebook.read_all()

    def notebook_search(self, keyword: str) -> List[Dict[str, str]]:
        """搜索记事本"""
        if not self.notebook:
            return []
        return self.notebook.search(keyword)

    def notebook_clear(self) -> None:
        """清空记事本"""
        if not self.notebook:
            return
        self.notebook.clear()
        self.logger.info("记事本已清空")

    def notebook_save_version(self, comment: str = None) -> str:
        """保存记事本版本"""
        if not self.notebook:
            return ""
        return self.notebook.save_version(comment)

    def notebook_build_context(self, max_lines: int = None) -> str:
        """构建记事本上下文"""
        if not self.notebook:
            return ""
        return self.notebook.build_prompt_context(max_lines)

    def notebook_get_status(self) -> Dict[str, Any]:
        """获取记事本状态"""
        if not self.notebook:
            return {}
        return self.notebook.get_status()

    # ========== 统一上下文构建 ==========

    def build_full_context(self, query: str = "", max_recent: int = 10,
                           max_history: int = 5, recent_age_seconds: int = 1800,
                           extra_context: List[Dict[str, Any]] = None) -> str:
        """
        构建给模型看的完整上下文（分层检索 + 个性化记忆）

        Tier 0: 内存消息（api_stream 传递，最精准，优先级最高）
        Tier 1: 近期对话（全文，默认30分钟内）— SQLite 短期记忆
        Tier 2: 稍旧历史（30分钟-7天，关键词检索）— SQLite 短期记忆
        Tier 3: 记事本 + 当前任务状态
        Tier 4: 个性化用户记忆（新增）

        长期记忆（JSONL）由 MemoryScheduler 管理，不在此检索。

        Args:
            query: 当前用户输入，用于关键词匹配。空字符串则跳过历史检索
            max_recent: Tier 1 最多取多少条
            max_history: Tier 2 最多取多少条
            recent_age_seconds: Tier 1 的时间窗口（秒），默认 1800 (30分钟)
            extra_context: 外部传入的消息列表 [{"role":..., "content":...}]
                          来自 api_stream 的精准内存消息，优先级高于 SQLite

        Returns:
            完整上下文字符串
        """
        context_parts = []

        # ===== 新增：个性化记忆注入 =====
        try:
            from modules.memory.core.memory_extractor import get_memory_extractor
            extractor = get_memory_extractor(self)
            personalized_context = extractor.build_personalized_context(query)
            if personalized_context:
                context_parts.append(personalized_context)
        except Exception as e:
            self.logger.debug("个性化记忆注入失败 (非致命): %s", e)
        # =================================

        # Tier 0: 外部传入的内存消息（api_stream 维护的精准会话记录）
        if extra_context:
            extra_text = self._format_extra_context(extra_context)
            if extra_text:
                context_parts.append(f"【会话历史（精准）】\n{extra_text}")

        # Tier 1: 近期短时记忆（全文，30分钟内）
        short_context = self._format_short_term(max_age_seconds=recent_age_seconds)
        if short_context:
            context_parts.append(f"【近期对话记忆（{recent_age_seconds // 60}分钟内）】\n{short_context}")

        # Tier 2: 稍旧短期记忆（30分钟-7天，关键词检索）
        if query:
            try:
                history_text = self._search_short_term_history(
                    query=query,
                    min_age_seconds=recent_age_seconds,
                    max_age_seconds=7 * 86400,
                    limit=max_history
                )
                if history_text:
                    context_parts.append(f"【相关历史记忆（30分钟-7天）】\n{history_text}")
            except Exception as e:
                self.logger.debug("短期历史记忆检索失败 (非致命): %s", e)

        # Tier 3: 记事本 + 任务
        if self.notebook:
            notebook_context = self.notebook.build_prompt_context(max_lines=50)
            if notebook_context.strip():
                context_parts.append(f"【工作记事本·重要事项】\n{notebook_context}")

        task_info = self._format_current_task()
        if task_info:
            context_parts.append(f"【当前任务】\n{task_info}")

        return "\n\n".join(context_parts) if context_parts else ""

    def _search_short_term_history(self, query: str, min_age_seconds: int,
                                   max_age_seconds: int, limit: int) -> str:
        """搜索 30分钟-7天 范围内的短期记忆（关键词匹配）"""
        import time as _time

        keywords = [w for w in query.replace('，', ' ').replace('？', ' ').replace('。', ' ').split() if len(w) >= 2]
        keywords = list(dict.fromkeys(keywords))[:5]

        q = self._database.create_memory_query(
            keywords=keywords if keywords else None,
            memory_type="dialog",
            limit=limit * 3,
            max_age_seconds=max_age_seconds
        )
        results = self.short_term_repo.query(q)

        now_ts = _time.time()
        filtered = []
        for r in results:
            created_at = r.get("created_at")
            if created_at:
                try:
                    from datetime import datetime as _dt
                    if isinstance(created_at, str):
                        created_ts = _dt.fromisoformat(created_at).timestamp()
                    else:
                        created_ts = float(created_at)
                    if (now_ts - created_ts) >= min_age_seconds:
                        filtered.append(r)
                except (ValueError, TypeError):
                    continue

        if not filtered:
            return ""

        lines = []
        for r in filtered[:limit]:
            text = r.get("content", "")[:200]
            lines.append(f"- {text}")
        return "\n".join(lines)

    def _format_extra_context(self, messages: List[Dict[str, Any]]) -> str:
        """格式化外部传入的内存消息"""
        if not messages:
            return ""

        lines = []
        for msg in messages[-20:]:
            role = msg.get("role", "unknown")
            text = str(msg.get("content", ""))[:500]
            if text.strip():
                lines.append(f"- [{role}]: {text}")

        return "\n".join(lines) if lines else ""

    def _format_short_term(self, max_age_seconds: int = None) -> str:
        """格式化 SQLite 短期记忆"""
        context = self.short_term.get_context(limit=50, max_age_seconds=max_age_seconds)
        if not context:
            context = self.short_term.get_context(
                limit=50, max_age_seconds=max_age_seconds, session_id=""
            )
        if not context:
            return ""

        lines = []
        for item in context[-20:]:
            role = item.get("role", "unknown")
            text = item.get("text", "")[:500]
            lines.append(f"- [{role}]: {text}")

        return "\n".join(lines)

    def _format_current_task(self) -> str:
        """格式化当前任务"""
        task = self.short_term.get_current_task()
        if not task:
            return ""

        desc = task.get("description", "")
        status = task.get("status", "unknown")
        return f"- 任务: {desc}\n- 状态: {status}"


def _parse_time_range(time_range: str) -> float:
    """解析时间范围字符串为秒数"""
    ranges = {
        "1h": 3600,
        "6h": 21600,
        "24h": 86400,
        "7d": 604800,
        "30d": 2592000,
        "all": 0,
    }
    return ranges.get(time_range, 604800)
