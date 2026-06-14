"""
TaskNotebook — 任务记事本

记录当前任务的进展状态、阶段性结论、待办事项。
供 ContinuousThinker 追踪多轮思考的进度。
"""
import time
from typing import Any, Dict, Optional


class TaskNotebook:
    """任务记事本 — 轻量内存中的任务状态追踪"""

    def __init__(self, session_id: str = ""):
        self._session_id = session_id
        self.content = "任务刚开始，请制定初步计划。"
        self.is_finished = False
        self._updated_at = time.time()

    def clear(self):
        """清空记事本"""
        self.content = "任务刚开始，请制定初步计划。"
        self.is_finished = False
        self._updated_at = time.time()

    def update(self, new_content: str, is_finished: bool = False):
        """更新记事本内容"""
        self.content = new_content
        self.is_finished = is_finished
        self._updated_at = time.time()

    def get_status(self) -> Dict[str, Any]:
        """获取当前状态（供 prompt 构建使用）"""
        return {
            "content": self.content,
            "is_finished": self.is_finished,
            "updated_at": self._updated_at,
        }
