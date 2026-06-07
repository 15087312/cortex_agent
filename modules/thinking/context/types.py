"""
全局上下文管理 — 共享数据类型

所有模型通过 ContextView 获取自己的上下文视图，
所有数据通过 GlobalContextPool 统一存储。
"""
import time
import hashlib
from enum import Enum
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


# ============================================================================
# 模型角色枚举
# ============================================================================

class ModelRole(str, Enum):
    """模型角色 — 决定 ContextView 的内容和结构"""
    MANAGER = "manager"              # 总控/主管
    EXPERT = "expert"                 # 通用专家
    PROBE = "probe"                  # 探针
    EMOTION_EXPERT = "emotion"       # 情绪专家
    VALUES_EXPERT = "values"         # 价值观专家
    SECURITY_EXPERT = "security"     # 安全专家
    MEMORY_EXPERT = "memory"         # 记忆专家
    TOOL_EXPERT = "tool"             # 工具专家
    AUDITOR = "auditor"              # 审计器


class CompressionLevel(str, Enum):
    """压缩级别"""
    NONE = "none"                    # 不压缩
    LIGHT = "light"                  # 去空行/注释
    MODERATE = "moderate"            # 摘要旧事件
    HEAVY = "heavy"                  # 结构化压缩
    AGGRESSIVE = "aggressive"        # 仅保留关键词


class EventType(str, Enum):
    """事件类型"""
    MODEL_OUTPUT = "model_output"
    TOOL_CALL = "tool_call"
    PROBE_SIGNAL = "probe_signal"
    EXPERT_RESULT = "expert_result"
    SYSTEM = "system"
    MEMORY_CONTEXT = "memory_context"
    FILE_CHANGE = "file_change"


# ============================================================================
# 核心数据类型
# ============================================================================

@dataclass
class FileInfo:
    """文件信息 — 全局只存一份"""
    path: str
    content: str
    hash: str = ""
    last_modified: float = 0.0
    vector_embedding: Optional[List[float]] = None
    summary: str = ""
    size_bytes: int = 0

    def __post_init__(self):
        if not self.hash and self.content:
            self.hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]
        if not self.size_bytes:
            self.size_bytes = len(self.content.encode("utf-8"))


@dataclass
class ProjectMetadata:
    """项目元数据"""
    project_root: str = ""
    file_tree: List[str] = field(default_factory=list)
    dependencies: Dict[str, str] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


@dataclass
class GlobalState:
    """全局状态 — 所有模型共享的进展信息"""
    todos: List[Dict[str, Any]] = field(default_factory=list)
    active_tasks: List[Dict[str, Any]] = field(default_factory=list)
    completed_tasks: List[Dict[str, Any]] = field(default_factory=list)
    failed_tasks: List[Dict[str, Any]] = field(default_factory=list)
    current_step: int = 0
    overall_progress: float = 0.0  # 0.0 ~ 1.0
    session_id: str = ""
    user_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EventRecord:
    """事件记录 — 所有模型输出、工具调用、探针信号等"""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    event_type: EventType = EventType.SYSTEM
    source_role: str = "system"  # 产生事件的模型角色
    content: Any = None          # 事件内容 (字符串或结构化数据)
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5      # 重要性 (影响压缩和保留策略)

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"evt_{int(self.timestamp)}_{hashlib.md5(str(self.content).encode()).hexdigest()[:8]}"


@dataclass
class ContextView:
    """上下文视图 — 每个模型看到的都是自己的视图"""
    view_id: str = ""
    model_role: ModelRole = ModelRole.EXPERT
    relevant_files: List[str] = field(default_factory=list)   # 文件路径列表
    relevant_state: Dict[str, Any] = field(default_factory=dict)  # 部分 GlobalState
    relevant_events: List[EventRecord] = field(default_factory=list)
    max_tokens: int = 8000
    compressed_content: str = ""  # 压缩后的上下文文本
    generated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if not self.view_id:
            self.view_id = f"view_{self.model_role.value}_{int(self.generated_at)}"
