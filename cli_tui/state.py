"""全局应用状态 — 类似 Open-ClaudeCode 的 AppStateStore"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AppState:
    """应用全局状态，由 Textual App 持有"""

    # 连接状态
    api_url: str = "http://localhost:8080"
    connected: bool = False
    session_id: str = ""
    processing: bool = False

    # 执行模式: "plan" / "edit" / "yolo"
    execution_mode: str = "edit"
    companion_mode: bool = False  # 陪伴模式状态（陪伴模式下强制 plan）

    # 对话数据
    dialog_entries: List[Dict[str, Any]] = field(default_factory=list)
    max_entries: int = 100
    sub_sessions: List[Dict[str, Any]] = field(default_factory=list)  # 副会话对话记录（思考结束后收集，供前端使用）

    # 工具调用
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_stats: Dict[str, Any] = field(default_factory=lambda: {
        "total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0,
    })

    # AI 响应
    final_response: str = ""
    elapsed_ms: float = 0.0
    trace_id: str = ""

    # 面板切换
    show_tools: bool = False
    show_thinking: bool = True

    # 进度提示
    thinking_hint: str = ""
    debug_enabled: bool = False
    debug_phase: str = ""
    debug_badge: str = ""
    debug_card: Dict[str, Any] = field(default_factory=dict)
    debug_events: List[Dict[str, Any]] = field(default_factory=list)
    max_debug_events: int = 200
    last_error: str = ""

    # 活跃专家追踪
    active_experts: List[str] = field(default_factory=list)  # ["代码审查", "安全检测"]
    error_chain: List[Dict[str, Any]] = field(default_factory=list)  # 错误链：专家→主管→大模型→CLI

    # 上下文窗口追踪
    context_tokens: int = 0          # 当前 prompt 估算 token 数
    context_window_size: int = 0     # 窗口大小（0=未收到心跳更新，UI 应隐藏）

    # 重试相关
    last_user_input: str = ""  # 用于 Ctrl+Y 重试

    # 进度 / 容错跟踪
    processing_start_time: float = 0.0
    last_event_time: float = 0.0
    consecutive_timeouts: int = 0
    retry_count: int = 0
    cancel_requested: bool = False

    # 输入历史
    input_history: List[str] = field(default_factory=list)
    max_history: int = 200

    # 安全审批状态
    pending_security_review: Optional[Dict[str, Any]] = field(default=None)

    def reset_for_new_input(self):
        """每次新输入前重置"""
        self.dialog_entries = []
        self.tool_calls = []
        self.tool_stats = {"total": 0, "success": 0, "failed": 0, "total_latency_ms": 0.0}
        self.final_response = ""
        self.elapsed_ms = 0.0
        self.trace_id = ""
        self.thinking_hint = ""
        self.debug_phase = ""
        self.debug_badge = ""
        self.debug_card = {}
        self.debug_events = []
        self.last_error = ""
        self.active_experts = []
        self.error_chain = []
        self.context_tokens = 0
        self.context_window_size = 0
        self.processing = True
        self.processing_start_time = time.time()
        self.last_event_time = time.time()
        self.consecutive_timeouts = 0
        self.retry_count = 0
        self.cancel_requested = False

    def add_dialog_entry(self, entry: Dict[str, Any]):
        """添加对话框条目（流式替换 + 普通去重）"""
        # 流式更新：替换同 tier+round 的旧条目
        if entry.get("entry_type") == "streaming":
            for i in range(len(self.dialog_entries) - 1, max(-1, len(self.dialog_entries) - 6), -1):
                existing = self.dialog_entries[i]
                if (existing.get("tier") == entry.get("tier")
                        and existing.get("round_num") == entry.get("round_num")
                        and existing.get("entry_type") == "streaming"):
                    self.dialog_entries[i] = entry
                    return
            self.dialog_entries.append(entry)
        else:
            prefix = entry.get("content", "")[:40]
            for existing in reversed(self.dialog_entries[-5:]):
                if (existing.get("tier") == entry.get("tier")
                        and existing.get("round_num") == entry.get("round_num")
                        and existing.get("content", "")[:40] == prefix):
                    return  # 重复，跳过
            self.dialog_entries.append(entry)
        if len(self.dialog_entries) > self.max_entries:
            self.dialog_entries = self.dialog_entries[-self.max_entries:]

    def add_tool_call(self, record: Dict[str, Any]):
        """添加工具调用记录"""
        self.tool_calls.append(record)
        if len(self.tool_calls) > 100:
            self.tool_calls = self.tool_calls[-100:]
        self.tool_stats["total"] += 1
        if record.get("success"):
            self.tool_stats["success"] += 1
        else:
            self.tool_stats["failed"] += 1
        self.tool_stats["total_latency_ms"] += record.get("latency_ms", 0)

    def add_input_history(self, text: str):
        """添加输入历史"""
        self.input_history.append(text)
        if len(self.input_history) > self.max_history:
            self.input_history = self.input_history[-self.max_history:]

    @property
    def avg_latency_ms(self) -> float:
        if self.tool_stats["total"] == 0:
            return 0
        return self.tool_stats["total_latency_ms"] / self.tool_stats["total"]
