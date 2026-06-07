"""
任务完成后记忆提取器 — 从会话中提取偏好/教训/状态变更并写入永久记忆

参照 Claude 的任务后记忆沉淀设计:
- 任务完成 30 秒后触发
- 提取: 用户偏好、经验教训、项目状态变更
- 分类写入: preference → long_term, evolution → long_term
- 所有操作 fire-and-forget, 不阻塞, 失败不影响主流程
"""
import re
import time
from typing import Dict, Any, List, Optional
from utils.logger import setup_logger

logger = setup_logger("post_task_extractor")


# 用户偏好提取正则
PREFERENCE_PATTERNS = [
    (re.compile(r'(?:我(?:更)?(?:喜欢|偏好|想要|希望|习惯|倾向于))(.+?)(?:[。！\n]|$)'), "preference"),
    (re.compile(r'(?:不要|别|禁止|千万别|避免)(.+?)(?:[。！\n]|$)'), "constraint"),
    (re.compile(r'(?:以后|下次|往后|记住)(.+?)(?:[。！\n]|$)'), "reminder"),
    (re.compile(r'(?:我觉得|我认为|我的观点是|我建议)(.+?)(?:[。！\n]|$)'), "opinion"),
]


class PostTaskMemoryExtractor:
    """任务完成后自动提取记忆并写入 long_term"""

    def __init__(self, memory_manager=None):
        self.memory = memory_manager

    async def extract_and_write(
        self,
        conversation: List[Dict[str, Any]],
        user_input: str,
        final_response: str,
        session_id: str,
    ) -> Dict[str, Any]:
        """主入口: 提取并写入记忆

        Returns:
            {"preferences": int, "lessons": int, "state_changes": int}
        """
        written: Dict[str, int] = {}

        try:
            # 1. 提取用户偏好
            preferences = self._extract_preferences(conversation)
            if preferences:
                self._write_preferences(preferences)
                written["preferences"] = len(preferences)

            # 2. 提取经验教训
            lessons = self._extract_lessons(conversation, final_response)
            if lessons:
                self._write_lessons(lessons, session_id)
                written["lessons"] = len(lessons)

            # 3. 提取项目状态变更
            state_changes = self._extract_state_changes(conversation)
            if state_changes:
                self._write_state_changes(state_changes)
                written["state_changes"] = len(state_changes)

        except Exception as e:
            logger.debug(f"[T5] 提取过程异常 (非致命): {e}")

        return written

    # ── 提取方法 ──

    def _extract_preferences(self, conversation: List[Dict]) -> List[Dict[str, str]]:
        """从对话中提取用户偏好"""
        preferences = []
        seen = set()

        for msg in conversation:
            content = str(msg.get("content", msg.get("text", "")))
            if not content:
                continue

            for pattern, ptype in PREFERENCE_PATTERNS:
                matches = pattern.findall(content)
                for match in matches:
                    text = match.strip()[:200]
                    if text and text not in seen:
                        seen.add(text)
                        preferences.append({
                            "type": ptype,
                            "content": text,
                            "timestamp": time.time(),
                            "source": "post_task_extractor",
                        })

        return preferences

    def _extract_lessons(
        self, conversation: List[Dict], final_response: str
    ) -> List[Dict[str, str]]:
        """从对话中提取经验教训 — 启发式规则"""
        lessons = []

        # 合并所有文本
        all_text = " ".join(
            str(m.get("content", m.get("text", ""))) for m in conversation
        )

        # 1. 工具成功模式
        if "成功" in all_text or "success" in all_text.lower():
            tool_pattern = re.compile(
                r'(?:用|使用|调用)(\w+)\s*(?:工具|tool).*?(?:成功|得到|返回)'
            )
            matches = tool_pattern.findall(all_text)
            for tool_name in matches[:3]:
                lessons.append({
                    "type": "tool_success",
                    "tool": tool_name,
                    "content": f"工具 {tool_name} 成功执行",
                    "timestamp": time.time(),
                })

        # 2. 工具失败 / 踩坑模式
        if "失败" in all_text or "错误" in all_text or "error" in all_text.lower():
            error_pattern = re.compile(
                r'(?:遇到|出现|发生)([^。！\n]{10,80}?)(?:错误|失败|问题)'
            )
            matches = error_pattern.findall(all_text)
            for error_desc in matches[:3]:
                lessons.append({
                    "type": "error_encountered",
                    "content": f"遇到问题: {error_desc}",
                    "timestamp": time.time(),
                })

        # 3. 解决模式
        if "解决" in all_text or "修复" in all_text or "fixed" in all_text.lower():
            solve_pattern = re.compile(
                r'(?:通过|用|使用)([^。！\n]{10,80}?)(?:解决|修复|搞定)'
            )
            matches = solve_pattern.findall(all_text)
            for solve_desc in matches[:3]:
                lessons.append({
                    "type": "solution_found",
                    "content": f"解决方法: {solve_desc}",
                    "timestamp": time.time(),
                })

        return lessons[:5]  # 最多保留5条

    def _extract_state_changes(self, conversation: List[Dict]) -> List[Dict[str, str]]:
        """检测项目状态变更"""
        changes = []

        # 合并所有助手消息
        assistant_texts = []
        for msg in conversation:
            if str(msg.get("role", "")).lower() in ("assistant", "ai", "bot"):
                assistant_texts.append(str(msg.get("content", msg.get("text", ""))))

        all_text = " ".join(assistant_texts)

        # 检测完成标志
        completion_patterns = [
            (re.compile(r'(?:已完成|任务完成|功能实现|部署成功)'), "task_completed"),
            (re.compile(r'(?:创建了|新增了|添加了)([^。！\n]+)'), "file_created"),
            (re.compile(r'(?:修改了|更新了|优化了)([^。！\n]+)'), "file_modified"),
        ]

        for pattern, change_type in completion_patterns:
            matches = pattern.findall(all_text)
            for match in matches[:3]:
                text = match.strip() if isinstance(match, str) else match
                changes.append({
                    "type": change_type,
                    "content": text[:200] if text else change_type,
                    "timestamp": time.time(),
                })

        return changes

    # ── 写入方法 ──

    def _write_preferences(self, preferences: List[Dict[str, str]]):
        """写入偏好到 long_term (type=preference)"""
        if not self.memory:
            return
        for pref in preferences:
            try:
                self.memory.save_long_term("preference", pref)
            except Exception as e:
                logger.debug("写入用户偏好失败 (非致命): %s", e)

    def _write_lessons(self, lessons: List[Dict[str, str]], session_id: str):
        """写入经验教训到 long_term (type=evolution)"""
        if not self.memory:
            return
        for lesson in lessons:
            try:
                lesson["session_id"] = session_id
                self.memory.save_long_term("evolution", lesson)
            except Exception as e:
                logger.debug("写入经验教训失败 (非致命): %s", e)

    def _write_state_changes(self, changes: List[Dict[str, str]]):
        """写入状态变更到 long_term (type=summary)"""
        if not self.memory:
            return
        for change in changes:
            try:
                self.memory.save_long_term("summary", change)
            except Exception as e:
                logger.debug("写入状态变更失败 (非致命): %s", e)
