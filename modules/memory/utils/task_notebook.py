"""
TaskNotebook — 任务记事本

记录当前任务的进展状态、阶段性结论、待办事项。
供 ContinuousThinker 追踪多轮思考的进度，跨委托唤醒时保留。
"""
import time
from typing import Any, Dict, List


class TaskNotebook:
    """任务记事本 — 轻量内存中的任务状态追踪，追加模式跨循环保留"""

    def __init__(self, session_id: str = ""):
        self._session_id = session_id
        self._entries: List[str] = []
        self.is_finished = False
        self._updated_at = time.time()

    @property
    def content(self) -> str:
        """返回编号的记事本内容，最新在最上"""
        if not self._entries:
            return "任务刚开始，请制定初步计划。"
        lines = []
        for i, entry in enumerate(reversed(self._entries), 1):
            lines.append(f"{i}. {entry}")
        return "\n".join(lines)

    def clear(self):
        """清空记事本"""
        self._entries.clear()
        self.is_finished = False
        self._updated_at = time.time()

    def update(self, new_content: str, is_finished: bool = False):
        """替换最后一条（不追加）"""
        if not self._entries:
            self._entries.append(new_content)
        else:
            self._entries[-1] = new_content
        self.is_finished = is_finished
        self._updated_at = time.time()

    def append(self, new_content: str, is_finished: bool = False, max_entries: int = 20):
        """追加一条新进展，自动去重 + 限制条数"""
        clean = new_content.strip()
        if not clean:
            return
        if self._entries and self._entries[-1] == clean:
            return  # 去重：连续相同不重复追加
        self._entries.append(clean)
        if len(self._entries) > max_entries:
            self._entries = self._entries[-max_entries:]
        self.is_finished = is_finished
        self._updated_at = time.time()

    def get_status(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "entries": len(self._entries),
            "is_finished": self.is_finished,
            "updated_at": self._updated_at,
        }
