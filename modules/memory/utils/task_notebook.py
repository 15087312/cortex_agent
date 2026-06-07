"""
任务记事本 - 用于跟踪连续思考的任务进度
"""
import json
import os
from typing import Optional
from utils.logger import setup_logger

logger = setup_logger("task_notebook")


class TaskNotebook:
    """轻量级任务记事本，基于 JSON 文件存储"""
    
    def __init__(self, session_id: str, data_dir: str = "data/memory"):
        self.session_id = session_id
        self.file_path = os.path.join(data_dir, f"notebook_{session_id}.json")
        self.content = ""
        self.is_finished = False
        self._load()

    def _load(self):
        """加载记事本内容"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.content = data.get("content", "")
                    self.is_finished = data.get("is_finished", False)
            except Exception as e:
                logger.warning("加载任务记事本失败，使用默认内容: %s", e)
                self.content = "任务刚开始，请制定初步计划。"
        else:
            self.content = "任务刚开始，请制定初步计划。"

    def update(self, new_content: str, is_finished: bool = False):
        """更新记事本"""
        self.content = new_content
        self.is_finished = is_finished
        self._save()

    def _save(self):
        """保存到文件"""
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump({
                "session_id": self.session_id,
                "content": self.content,
                "is_finished": self.is_finished
            }, f, ensure_ascii=False, indent=2)

    def get_status(self) -> str:
        """获取当前任务状态字符串"""
        status = "已完成" if self.is_finished else "进行中"
        return f"[任务状态: {status}]\n[当前进度]:\n{self.content}"

    def clear(self):
        """清除记事本"""
        if os.path.exists(self.file_path):
            os.remove(self.file_path)
        self.content = "任务刚开始，请制定初步计划。"
        self.is_finished = False
