"""
自动记忆提取器 - 从对话中自动提取用户信息并保存
"""
import json
import re
import threading as _threading
from typing import Dict, List, Any, Optional
from modules.utils.logger import setup_logger
from modules.management.core.error_bus import error_bus, ErrorContext


class MemoryExtractor:
    """
    从对话中自动提取用户个人信息、偏好、历史问题等

    类似豆包的"越用越了解你"功能
    """

    def __init__(self, memory_manager=None):
        self.memory_manager = memory_manager
        self.logger = setup_logger("memory_extractor")

        # 提取规则（关键词模式）
        self.extraction_patterns = {
            "name": [
                r"我叫(.+?)[，,。！？]",
                r"名字是(.+?)[，,。！？]",
                r"称呼我(.+?)[，,。！？]",
            ],
            "preference": [
                r"我喜欢(.+?)[。！？]",
                r"我偏好(.+?)[。！？]",
                r"我更倾向于(.+?)[。！？]",
            ],
            "profession": [
                r"我是(.+?)[，,。！？]",
                r"我的工作(.+?)[。！？]",
                r"我从事(.+?)[。！？]",
            ],
            "interest": [
                r"我对(.+?)感兴[趣味]",
                r"我平时喜欢(.+?)[。！？]",
            ],
        }

    def extract_from_dialog(self, user_input: str, assistant_response: str = "") -> List[Dict[str, Any]]:
        """
        从对话中提取记忆

        Args:
            user_input: 用户输入
            assistant_response: AI回复（可选）

        Returns:
            提取的记忆列表 [{"category": "...", "content": "...", "type": "..."}]
        """
        memories = []

        # 1. 基于规则提取
        rule_memories = self._extract_by_rules(user_input)
        memories.extend(rule_memories)

        # 2. 如果有memory_manager，可以用LLM做更智能的提取
        if self.memory_manager and len(user_input) > 20:
            llm_memories = self._extract_by_llm(user_input, assistant_response)
            memories.extend(llm_memories)

        # 3. 去重并保存
        if memories:
            for mem in memories:
                self._save_extracted_memory(mem)

        return memories

    def _extract_by_rules(self, text: str) -> List[Dict[str, Any]]:
        """基于正则规则提取"""
        memories = []

        # 提取姓名
        for pattern in self.extraction_patterns["name"]:
            matches = re.findall(pattern, text)
            for match in matches:
                name = match.strip()
                if len(name) > 1 and len(name) < 20:
                    memories.append({
                        "category": "preferences",
                        "content": f"用户姓名：{name}",
                        "type": "name",
                        "importance": 0.9
                    })

        # 提取偏好
        for pattern in self.extraction_patterns["preference"]:
            matches = re.findall(pattern, text)
            for match in matches:
                pref = match.strip()
                if len(pref) > 2:
                    memories.append({
                        "category": "preferences",
                        "content": f"用户偏好：{pref}",
                        "type": "preference",
                        "importance": 0.7
                    })

        # 提取职业
        for pattern in self.extraction_patterns["profession"]:
            matches = re.findall(pattern, text)
            for match in matches:
                job = match.strip()
                if len(job) > 2 and len(job) < 50:
                    memories.append({
                        "category": "preferences",
                        "content": f"用户职业/身份：{job}",
                        "type": "profession",
                        "importance": 0.8
                    })

        # 提取兴趣
        for pattern in self.extraction_patterns["interest"]:
            matches = re.findall(pattern, text)
            for match in matches:
                interest = match.strip()
                if len(interest) > 2:
                    memories.append({
                        "category": "preferences",
                        "content": f"用户兴趣：{interest}",
                        "type": "interest",
                        "importance": 0.6
                    })

        return memories

    def _extract_by_llm(self, user_input: str, assistant_response: str = "") -> List[Dict[str, Any]]:
        """使用LLM智能提取记忆"""
        try:
            prompt = f"""
你是一个记忆提取助手。请分析以下对话，提取出关于用户的个人信息、偏好、习惯等值得长期记忆的内容。

用户输入：{user_input}

如果对话中包含以下信息，请提取：
1. 用户的姓名、昵称
2. 用户的职业、身份
3. 用户的兴趣爱好、偏好
4. 用户的重要经历或背景
5. 用户对某些事物的看法或态度

请以JSON格式返回，格式如下：
{{
  "memories": [
    {{
      "category": "preferences|skills|knowledge|experience",
      "content": "提取的具体内容",
      "type": "name|preference|profession|interest|other",
      "importance": 0.5-1.0
    }}
  ]
}}

如果没有值得记忆的信息，返回：{{"memories": []}}

只返回JSON，不要其他内容。
"""

            from modules.thinking.core.model_manager import model_manager

            client = model_manager.big_model
            if not client:
                return []
            response = client.generate(prompt, max_tokens=500, temperature=0.3)

            try:
                result = json.loads(response)
                return result.get("memories", [])
            except (json.JSONDecodeError, ValueError):
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                    return result.get("memories", [])

        except Exception as e:
            self.logger.warning(f"LLM记忆提取失败: {e}")
            error_bus.report_error(
                e,
                ErrorContext(
                    module="memory_extractor",
                    function="_extract_by_llm",
                    extra={"user_input_length": len(user_input) if user_input else 0}
                )
            )

        return []

    def _save_extracted_memory(self, memory: Dict[str, Any]):
        """保存提取的记忆"""
        if not self.memory_manager:
            return

        try:
            category = memory.get("category", "general")
            content = memory.get("content", "")
            importance = memory.get("importance", 0.5)

            existing = self.memory_manager.search_classified_memory(
                query=content[:20],
                category=category,
                memory_age="all",
                limit=5
            )

            for ex in existing:
                if content in ex.get("content", "") or ex.get("content", "") in content:
                    return

            self.memory_manager.save_classified_memory(
                category=category,
                content=content,
                metadata={"importance": importance, "auto_extracted": True}
            )

            self.logger.info(f"自动提取记忆: [{category}] {content[:50]}")

        except Exception as e:
            self.logger.error(f"保存提取记忆失败: {e}")

    def build_personalized_context(self, query: str) -> str:
        """
        为当前查询构建个性化上下文

        在AI回复前，先检索相关的用户记忆，注入到prompt中
        """
        if not self.memory_manager:
            return ""

        context_parts = []

        preferences = self.memory_manager.search_classified_memory(
            query=query,
            category="preferences",
            memory_age="all",
            limit=5
        )

        if preferences:
            lines = ["【用户偏好记忆】"]
            for pref in preferences:
                lines.append(f"- {pref.get('content', '')}")
            context_parts.append("\n".join(lines))

        experiences = self.memory_manager.search_classified_memory(
            query=query,
            category="experience",
            memory_age="mid",
            limit=3
        )

        if experiences:
            lines = ["【相关历史经验】"]
            for exp in experiences:
                lines.append(f"- {exp.get('content', '')}")
            context_parts.append("\n".join(lines))

        skills = self.memory_manager.search_classified_memory(
            query=query,
            category="skills",
            memory_age="all",
            limit=3
        )

        if skills:
            lines = ["【用户掌握的技能】"]
            for skill in skills:
                lines.append(f"- {skill.get('content', '')}")
            context_parts.append("\n".join(lines))

        return "\n\n".join(context_parts) if context_parts else ""


_memory_extractor = None
_memory_extractor_lock = _threading.Lock()


def get_memory_extractor(memory_manager=None):
    """获取记忆提取器单例"""
    global _memory_extractor
    if _memory_extractor is None:
        with _memory_extractor_lock:
            if _memory_extractor is None:
                _memory_extractor = MemoryExtractor(memory_manager)
    elif memory_manager and _memory_extractor.memory_manager is None:
        _memory_extractor.memory_manager = memory_manager
    return _memory_extractor
