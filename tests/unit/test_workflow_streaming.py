"""流式工作流引擎 (StreamingWorkflowEngine) 单元测试

验证工作流与实时流式（Token 增量、工具进度、状态差集）的完美协同。
"""
import asyncio
from typing import Any, Dict, List

import pytest

from modules.thinking.core.workflow_streaming import (
    ContextPrepNode,
    EventType,
    NodeContext,
    StreamingModelNode,
    StreamingNode,
    StreamingToolNode,
    StreamingWorkflowEngine,
    WorkflowStreamEvent,
)


def _run(coro):
    return asyncio.run(coro)


def test_streaming_workflow_event_to_dict_and_sse():
    ev = WorkflowStreamEvent(
        seq=1,
        event="llm/text_chunk",
        node_id="model",
        payload={"delta": "你好"},
    )
    d = ev.to_dict()
    assert d["seq"] == 1
    assert d["event"] == "llm/text_chunk"
    assert d["node_id"] == "model"
    assert d["data"] == {"delta": "你好"}

    sse = ev.to_sse()
    assert sse.startswith("event: llm/text_chunk\ndata: {")
    assert '"delta": "你好"' in sse
    assert sse.endswith("\n\n")


def test_linear_streaming_pipeline_produces_incremental_chunks():
    """验证线性流式工作流能够逐步产生流式 Token 帧，并在节点完成时原子更新状态"""
    engine = StreamingWorkflowEngine(session_id="test_stream_1")
    prep_node = ContextPrepNode("prep")
    model_node = StreamingModelNode(
        "model",
        mock_chunks=[
            ("先思考任务背景", True),
            ("开始回答：", False),
            ("这是第一点内容", False),
        ],
    )

    engine.add_node(prep_node)
    engine.add_node(model_node)
    engine.set_entry_point("prep")
    engine.add_edge("prep", "model")

    async def collect_events():
        events: List[WorkflowStreamEvent] = []
        async for ev in engine.run_stream({"question": "如何优化代码性能？"}):
            events.append(ev)
        return events

    events = _run(collect_events())

    # 1. 验证事件序列与类型
    ev_types = [e.event for e in events]
    assert EventType.WORKFLOW_START.value in ev_types
    assert EventType.NODE_START.value in ev_types
    assert EventType.LLM_THINKING_CHUNK.value in ev_types
    assert EventType.LLM_TEXT_CHUNK.value in ev_types
    assert EventType.NODE_END.value in ev_types
    assert EventType.WORKFLOW_END.value in ev_types

    # 2. 验证序列号单调递增
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))  # 无重复 seq

    # 3. 验证持久化事件日志完整保留
    assert len(engine.durable_event_log) == len(events)

    # 4. 验证最终状态原子合并
    end_ev = [e for e in events if e.event == EventType.WORKFLOW_END.value][0]
    final_state = end_ev.payload["final_state"]
    assert final_state["turn_count"] == 1
    assert "优化代码性能" in final_state["prompt"]
    assert final_state["thought"] == "先思考任务背景"
    assert final_state["output"] == "开始回答：这是第一点内容"


def test_conditional_edge_react_tool_loop():
    """验证工作流支持带条件边的 ReAct 工具调用循环：
    prep -> model -> [若触发工具 -> tool -> model -> 结束]
    """
    engine = StreamingWorkflowEngine(session_id="test_react_loop")
    prep_node = ContextPrepNode("prep")
    # 第一次运行 model 会触发工具调用，第二次不再触发
    model_node = StreamingModelNode(
        "model",
        mock_chunks=[("决定调用搜索工具", True)],
        tool_call_to_trigger={"tool": "web_search", "args": {"q": "python"}},
    )
    tool_node = StreamingToolNode("tool")

    engine.add_node(prep_node)
    engine.add_node(model_node)
    engine.add_node(tool_node)

    engine.set_entry_point("prep")
    engine.add_edge("prep", "model")

    # 条件边：如果模型生成了 pending_tool_call 则跳转工具节点，否则结束
    def route_after_model(state: Dict[str, Any]):
        if state.get("pending_tool_call"):
            return "tool"
        return None

    engine.add_conditional_edge("model", route_after_model)
    # 工具节点跑完后结束流程（或者可循环回 model）
    engine.add_edge("tool", None)

    async def collect_events():
        events: List[WorkflowStreamEvent] = []
        async for ev in engine.run_stream({"question": "查一下最新技术"}):
            events.append(ev)
        return events

    events = _run(collect_events())

    ev_types = [e.event for e in events]
    assert EventType.TOOL_CALL.value in ev_types
    assert EventType.TOOL_CHUNK.value in ev_types
    assert EventType.TOOL_RESULT.value in ev_types

    # 验证工具卡片内容与最终状态
    tool_results = [e for e in events if e.event == EventType.TOOL_RESULT.value]
    assert len(tool_results) == 1
    assert tool_results[0].payload["tool"] == "web_search"
    assert tool_results[0].payload["result"]["status"] == "ok"

    end_ev = [e for e in events if e.event == EventType.WORKFLOW_END.value][0]
    final_state = end_ev.payload["final_state"]
    assert final_state["tool_executed"] is True
    assert final_state["pending_tool_call"] is None


def test_streaming_mute_replay_mode():
    """验证回放或静默模式下（is_live=False），瞬态帧不发射，仅记录骨架状态"""
    engine = StreamingWorkflowEngine(session_id="test_mute")
    model_node = StreamingModelNode("model", mock_chunks=[("思考中", True), ("回答", False)])
    engine.add_node(model_node)
    engine.set_entry_point("model")

    async def collect_events():
        events: List[WorkflowStreamEvent] = []
        async for ev in engine.run_stream({}, is_live=False):
            events.append(ev)
        return events

    events = _run(collect_events())

    # 在 Mute 模式下，中间的 llm/thinking_chunk 和 llm/text_chunk 不推流
    ev_types = [e.event for e in events]
    assert EventType.LLM_THINKING_CHUNK.value not in ev_types
    assert EventType.LLM_TEXT_CHUNK.value not in ev_types

    # 但结构化生命周期事件与最终状态仍然完整保留
    assert EventType.WORKFLOW_START.value in ev_types
    assert EventType.NODE_START.value in ev_types
    assert EventType.NODE_END.value in ev_types
    assert EventType.WORKFLOW_END.value in ev_types


def test_node_error_handling_in_stream():
    """验证节点抛异常时工作流发射 node/error 帧并安全退出"""
    class ExplodingNode(StreamingNode):
        async def execute(self, state: Dict[str, Any], ctx: NodeContext) -> Dict[str, Any]:
            await ctx.emit(EventType.TOOL_CHUNK, {"info": "即将异常"})
            raise RuntimeError("模拟服务熔断")

    engine = StreamingWorkflowEngine(session_id="test_error")
    engine.add_node(ExplodingNode("bad_node"))
    engine.set_entry_point("bad_node")

    async def collect_events():
        events: List[WorkflowStreamEvent] = []
        async for ev in engine.run_stream({}):
            events.append(ev)
        return events

    events = _run(collect_events())

    ev_types = [e.event for e in events]
    assert EventType.NODE_ERROR.value in ev_types
    err_ev = [e for e in events if e.event == EventType.NODE_ERROR.value][0]
    assert "模拟服务熔断" in err_ev.payload["error"]
    assert err_ev.payload["error_type"] == "RuntimeError"
