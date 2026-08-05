"""
Blackboard — simplified session state manager.
Stores messages and metadata per session.
"""
from typing import Dict, List
from dataclasses import dataclass, field

from utils.logger import setup_logger

logger = setup_logger("blackboard")


@dataclass
class SessionState:
    """Per-session state."""
    session_id: str
    messages: List[dict] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


class Blackboard:
    """Simplified session state manager."""

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}

    def get_or_create(self, session_id: str) -> SessionState:
        if session_id not in self._sessions:
            self._sessions[session_id] = SessionState(session_id=session_id)
        return self._sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str) -> None:
        state = self.get_or_create(session_id)
        state.messages.append({"role": role, "content": content})

    def get_messages(self, session_id: str) -> List[dict]:
        state = self.get_or_create(session_id)
        return list(state.messages)

    def set_metadata(self, session_id: str, key: str, value) -> None:
        state = self.get_or_create(session_id)
        state.metadata[key] = value

    def get_metadata(self, session_id: str) -> Dict:
        state = self.get_or_create(session_id)
        return dict(state.metadata)

    def clear_session(self, session_id: str) -> None:
        if session_id in self._sessions:
            del self._sessions[session_id]

    def clear_all(self) -> None:
        self._sessions.clear()
