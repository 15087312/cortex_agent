"""对话感知器 — 检测对话内容变化"""
from typing import Dict, Any, List

from modules.perception.change_event import ChangeEvent


class DialogPerception:
    """对话感知器"""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self.last_snapshot: List[Dict[str, Any]] = []
        self._messages_cache: List[Dict] = []

    def update_snapshot(self, messages: List[Dict[str, Any]]) -> None:
        """更新对话快照"""
        self._messages_cache = messages
        self.last_snapshot = [
            {"id": m.get("id", i), "role": m.get("role"), "content": m.get("content", "")[:100]}
            for i, m in enumerate(messages)
        ]

    def check_changes(self, old_messages: List[Dict], new_messages: List[Dict]) -> List[ChangeEvent]:
        """检查对话变化"""
        changes = []

        old_ids = {m.get("id") or i for i, m in enumerate(old_messages)}
        new_ids = {m.get("id") or i for i, m in enumerate(new_messages)}

        for i, msg in enumerate(new_messages):
            msg_id = msg.get("id") or i

            if msg_id not in old_ids:
                content = msg.get("content", "")[:100]
                role = msg.get("role", "user")
                changes.append(ChangeEvent(
                    change_type="created",
                    target_type="dialog",
                    target=f"[{role}] {content}",
                    details={"role": role, "id": msg_id}
                ))

        return changes

    def auto_check(self, messages: List[Dict]) -> List[ChangeEvent]:
        """自动检查变化（使用上次快照）"""
        return self.check_changes(self.last_snapshot, messages)
