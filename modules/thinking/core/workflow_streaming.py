"""工作流实时流式引擎 (Streaming Workflow Engine)

解决工作流“离散节点粒度（批处理事务状态转换）”与“实时流式（逐字 Token 与瞬态事件交互）”的冲突。
核心机制：
1. 状态流 (State Plane) 与 事件流 (Event Plane) 双轨分流
2. NodeContext 注入式事件发射器 (emit)
3. 引擎级实时异步生成器 (run_stream)，可直接对接 SSE / WebSocket
4. 原子状态合并 (State Delta Commit) 与条件边路由 (Edge Routing)
5. 回放静默 (Mute Replay) 与序列号追踪 (Event Sequencing)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Set, Tuple


class EventType(str, Enum):
    # 节点生命周期
    NODE_START = "node/start"
    NODE_END = "node/end"
    NODE_ERROR = "node/error"
    
    # LLM 实时流
    LLM_THINKING_CHUNK = "llm/thinking_chunk"
    LLM_TEXT_CHUNK = "llm/text_chunk"
    
    # 工具调用生命周期与流
    TOOL_CALL = "tool/call"
    TOOL_CHUNK = "tool/chunk"
    TOOL_RESULT = "tool/result"
    
    # 流程控制
    WORKFLOW_START = "workflow/start"
    WORKFLOW_END = "workflow/end"


@dataclass
class WorkflowStreamEvent:
    """发往客户端的标准化事件帧 (对齐 dsh Typed Event 协议)"""
    seq: int
    event: str
    node_id: str
    payload: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "seq": self.seq,
            "event": self.event,
            "node_id": self.node_id,
            "data": self.payload,
            "timestamp": self.timestamp,
        }

    def to_sse(self) -> str:
        import json
        return f"event: {self.event}\ndata: {json.dumps(self.to_dict(), ensure_ascii=False)}\n\n"


class NodeContext:
    """节点运行时上下文，提供瞬态事件发射能力"""

    def __init__(
        self,
        node_id: str,
        session_id: str,
        queue: asyncio.Queue[WorkflowStreamEvent],
        seq_counter: Callable[[], int],
        is_live: bool = True,
    ) -> None:
        self.node_id = node_id
        self.session_id = session_id
        self._queue = queue
        self._seq_counter = seq_counter
        self.is_live = is_live

    async def emit(self, event_type: EventType | str, payload: Dict[str, Any]) -> None:
        """发射瞬态过程事件（Token、工具进度、状态变更等）"""
        if not self.is_live:
            # 回放模式或测试静默模式下不向流中推帧
            return

        ev_name = event_type.value if isinstance(event_type, EventType) else str(event_type)
        event = WorkflowStreamEvent(
            seq=self._seq_counter(),
            event=ev_name,
            node_id=self.node_id,
            payload=payload,
        )
        await self._queue.put(event)


class StreamingNode:
    """流式工作流节点抽象基类"""
    node_id: str

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id

    async def execute(
        self,
        state: Dict[str, Any],
        ctx: NodeContext,
    ) -> Dict[str, Any]:
        """执行节点逻辑。
        
        过程通过 ctx.emit(...) 实时推流；
        完成后返回状态增量 (State Delta)，由引擎原子性合并到主状态。
        """
        raise NotImplementedError


class StreamingWorkflowEngine:
    """支持实时流式广播与条件图调度的执行引擎"""

    def __init__(self, session_id: Optional[str] = None) -> None:
        self.session_id = session_id or str(uuid.uuid4())
        self._nodes: Dict[str, StreamingNode] = {}
        # 边定义：node_id -> 下一个 node_id，或者判断函数 (state) -> next_node_id
        self._edges: Dict[str, str | Callable[[Dict[str, Any]], Optional[str]]] = {}
        self._entry_node: Optional[str] = None
        self._current_seq = 0
        self.durable_event_log: List[WorkflowStreamEvent] = []

    def _next_seq(self) -> int:
        self._current_seq += 1
        return self._current_seq

    def add_node(self, node: StreamingNode) -> StreamingWorkflowEngine:
        self._nodes[node.node_id] = node
        return self

    def set_entry_point(self, node_id: str) -> StreamingWorkflowEngine:
        if node_id not in self._nodes:
            raise ValueError(f"节点 {node_id} 尚未注册")
        self._entry_node = node_id
        return self

    def add_edge(self, from_node: str, to_node: str) -> StreamingWorkflowEngine:
        self._edges[from_node] = to_node
        return self

    def add_conditional_edge(
        self,
        from_node: str,
        condition: Callable[[Dict[str, Any]], Optional[str]],
    ) -> StreamingWorkflowEngine:
        self._edges[from_node] = condition
        return self

    async def run_stream(
        self,
        initial_state: Dict[str, Any],
        is_live: bool = True,
    ) -> AsyncIterator[WorkflowStreamEvent]:
        """工作流运行主入口，作为异步生成器实时产生流式事件帧。"""
        if not self._entry_node:
            raise RuntimeError("工作流未设置入口节点 (entry_point)")

        state = dict(initial_state)
        current_node_id: Optional[str] = self._entry_node
        event_queue: asyncio.Queue[WorkflowStreamEvent] = asyncio.Queue()

        # 发射工作流启动事件
        start_event = WorkflowStreamEvent(
            seq=self._next_seq(),
            event=EventType.WORKFLOW_START.value,
            node_id="root",
            payload={"session_id": self.session_id, "entry": self._entry_node},
        )
        self.durable_event_log.append(start_event)
        yield start_event

        while current_node_id and current_node_id in self._nodes:
            node = self._nodes[current_node_id]
            ctx = NodeContext(
                node_id=node.node_id,
                session_id=self.session_id,
                queue=event_queue,
                seq_counter=self._next_seq,
                is_live=is_live,
            )

            # 发送节点启动事件
            node_start_event = WorkflowStreamEvent(
                seq=self._next_seq(),
                event=EventType.NODE_START.value,
                node_id=node.node_id,
                payload={"status": "running"},
            )
            self.durable_event_log.append(node_start_event)
            yield node_start_event

            # 后台运行节点执行任务，同时主协程实时泵出内部事件
            node_task = asyncio.create_task(node.execute(state, ctx))

            while not node_task.done() or not event_queue.empty():
                try:
                    # 短超时轮询，避免在节点静默期间挂死
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.02)
                    self.durable_event_log.append(event)
                    yield event
                except asyncio.TimeoutError:
                    continue

            # 获取节点执行结果（或异常处理）
            try:
                state_delta = await node_task
                if isinstance(state_delta, dict):
                    # 原子合并状态差集
                    state.update(state_delta)

                node_end_event = WorkflowStreamEvent(
                    seq=self._next_seq(),
                    event=EventType.NODE_END.value,
                    node_id=node.node_id,
                    payload={"status": "completed"},
                )
                self.durable_event_log.append(node_end_event)
                yield node_end_event

            except Exception as exc:
                node_err_event = WorkflowStreamEvent(
                    seq=self._next_seq(),
                    event=EventType.NODE_ERROR.value,
                    node_id=node.node_id,
                    payload={"error": str(exc), "error_type": type(exc).__name__},
                )
                self.durable_event_log.append(node_err_event)
                yield node_err_event
                break

            # 决定下一个跳转节点 (Edge Routing)
            edge = self._edges.get(current_node_id)
            if callable(edge):
                current_node_id = edge(state)
            elif isinstance(edge, str):
                current_node_id = edge
            else:
                current_node_id = None

        # 工作流结束事件
        end_event = WorkflowStreamEvent(
            seq=self._next_seq(),
            event=EventType.WORKFLOW_END.value,
            node_id="root",
            payload={"final_state": state},
        )
        self.durable_event_log.append(end_event)
        yield end_event


# ─────────────────────────────────────────────────────────────────────────────
# 标准实战节点实现 (用于 Agent 思维流 + 工具流)
# ─────────────────────────────────────────────────────────────────────────────

class ContextPrepNode(StreamingNode):
    """上下文准备节点：负责记忆召回、黑板状态同步"""

    def __init__(self, node_id: str = "context_prep") -> None:
        super().__init__(node_id)

    async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
        question = state.get("question", "")
        # 发射阶段性进度提示
        await ctx.emit(
            EventType.TOOL_CHUNK,
            {"stage": "retrieval", "message": f"正在检索与 {question[:20]} 相关的历史记忆..."},
        )
        await asyncio.sleep(0.01)  # 模拟异步检索 I/O
        return {
            "prompt": f"【系统提示】你是一个专家。\n【用户问题】{question}",
            "turn_count": state.get("turn_count", 0) + 1,
        }


class StreamingModelNode(StreamingNode):
    """流式模型推理节点：边输出思考流与正文流，边累积最终生成结果"""

    def __init__(
        self,
        node_id: str = "model_inference",
        mock_chunks: Optional[List[Tuple[str, bool]]] = None,
        tool_call_to_trigger: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(node_id)
        # mock_chunks: List of (text, is_thinking)
        self.mock_chunks = mock_chunks
        self.tool_call_to_trigger = tool_call_to_trigger

    async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
        accumulated_thought: List[str] = []
        accumulated_text: List[str] = []

        chunks = self.mock_chunks or [
            ("让我分析一下这个任务...", True),
            ("需要先调用工具获取数据...", True),
            ("根据分析，现在给出方案：", False),
            ("第一步：执行环境检测。", False),
        ]

        for text, is_thinking in chunks:
            ev_type = EventType.LLM_THINKING_CHUNK if is_thinking else EventType.LLM_TEXT_CHUNK
            await ctx.emit(ev_type, {"delta": text, "is_thinking": is_thinking})
            if is_thinking:
                accumulated_thought.append(text)
            else:
                accumulated_text.append(text)
            await asyncio.sleep(0.01)  # 模拟网络流式延迟

        delta: dict[str, Any] = {
            "thought": "".join(accumulated_thought),
            "output": "".join(accumulated_text),
        }

        # 如果触发了工具调用，写入状态并发出 tool/call 帧
        if self.tool_call_to_trigger and not state.get("tool_executed"):
            call_id = f"call_{uuid.uuid4().hex[:8]}"
            await ctx.emit(EventType.TOOL_CALL, {
                "call_id": call_id,
                "tool": self.tool_call_to_trigger.get("tool"),
                "args": self.tool_call_to_trigger.get("args"),
            })
            delta["pending_tool_call"] = {
                "call_id": call_id,
                "tool": self.tool_call_to_trigger.get("tool"),
                "args": self.tool_call_to_trigger.get("args"),
            }

        return delta


class StreamingToolNode(StreamingNode):
    """流式工具执行节点：推送实时控制台日志与结果卡片"""

    def __init__(self, node_id: str = "tool_execution") -> None:
        super().__init__(node_id)

    async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
        call_info = state.get("pending_tool_call") or {"call_id": "call_1", "tool": "search"}
        call_id = call_info.get("call_id", "")
        tool_name = call_info.get("tool", "")

        # 实时推工具 stdout 日志
        for line in [f"开始执行工具 {tool_name}...", "连接远程服务端点...", "响应完成，耗时 12ms"]:
            await ctx.emit(EventType.TOOL_CHUNK, {"call_id": call_id, "stdout": line})
            await asyncio.sleep(0.01)

        # 产出最终卡片与视图
        result_payload = {"status": "ok", "items_found": 3, "view": "card"}
        await ctx.emit(EventType.TOOL_RESULT, {
            "call_id": call_id,
            "tool": tool_name,
            "result": result_payload,
        })

        return {
            "tool_executed": True,
            "tool_result": result_payload,
            "pending_tool_call": None,
        }

