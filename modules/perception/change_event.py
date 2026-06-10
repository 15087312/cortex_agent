"""变化事件类型 — 感知模块共用"""
import time
from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class ChangeEvent:
    """变化事件"""
    change_type: str
    target_type: str
    target: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_prompt(self) -> str:
        """转换为提示文本"""
        if self.target_type == "file":
            icon_map = {
                "created": "📄", "modified": "📝", "deleted": "🗑️", "moved": "📦"
            }
            icon = icon_map.get(self.change_type, "📁")
            if self.change_type == "moved":
                return f"{icon} 移动: {self.details.get('from', self.target)} → {self.target}"
            return f"{icon} {self.change_type.title()}: {self.target}"

        elif self.target_type == "dialog":
            icon = "💬" if self.change_type == "created" else "🔄"
            return f"{icon} {self.target}"

        elif self.target_type == "screen":
            return f"🖥️ {self.target}: {self.details.get('change_desc', '画面变化')}"

        elif self.target_type == "speech":
            return f"🎤 语音输入: {self.target}"

        return f"[{self.target_type}] {self.change_type}: {self.target}"
