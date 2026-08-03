"""
探针管理工具 — 模型通过工具调用来控制探针

保留工具:
- deep_recall: 深度因果回忆（因果图+树下钻+事件召回）
- request_intermediate_response: 请求中途回复（大模型在专家工作时先回复用户）

已移除的遗留工具（功能已被控制工具替代）:
  probe_start, probe_stop, probe_list, persona_inject,
  recall_guidance, tool_download, probe_create, view_sub_session
"""
import asyncio
from typing import Dict, Any

from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("probe_tools")

# 模块级存储：编排器注入的当前会话引导文本
# 按 (model_id, session_id) 双键存储，支持多 Large 模型共存
_session_guidance: Dict[tuple, Dict[str, str]] = {}


def set_session_guidance(session_id: str, guidance: Dict[str, str], model_id: str = "large_primary") -> None:
    """编排器调用，写入当前会话的引导文本"""
    _session_guidance[(model_id, session_id)] = guidance


# ============================================================================
# deep_recall — 深度因果回忆
# ============================================================================

@ToolRegistry.register(
    "deep_recall",
    description="深度因果回忆 — 当需要分析原因、预测后果、归纳规律时使用。比普通记忆检索更深，能给出因果链路和规律结论。",
    params={
        "query": "查询内容（如\"项目延期的原因\"）",
        "depth_level": "1=标准（默认），2=深度（2跳邻域+全树遍历）",
        "max_events": "最多返回佐证事件数，默认5",
    },
    risk_level="LOW",
    category="query",
    tags=["memory", "causal"],
)
def deep_recall(
    query: str, depth_level: int = 1, max_events: int = 5, **kwargs
) -> Dict[str, Any]:
    """执行深度因果回忆"""
    import asyncio
    from modules.memory.depth_recall import DepthRecallScheduler
    from modules.memory.result_fusion import format_deep_recall_result

    try:
        scheduler = DepthRecallScheduler()
        loop = asyncio.get_event_loop()
        if loop.is_running():
            result = asyncio.run_coroutine_threadsafe(
                scheduler.deep_recall(query, max_results=max_events, depth_level=depth_level),
                loop,
            ).result(timeout=30)
        else:
            result = loop.run_until_complete(
                scheduler.deep_recall(query, max_results=max_events, depth_level=depth_level)
            )

        if result.success and not result.fallback:
            formatted = format_deep_recall_result(result, max_events=max_events)
            return {
                "success": True,
                "result": formatted,
                "causal_chains": len(result.causal_chains),
                "supporting_events": len(result.supporting_events),
            }
        return {
            "success": False,
            "error": f"深度回忆未找到因果关联 ({result.error})",
            "hint": "请尝试使用普通记忆检索(event_query)",
        }
    except Exception as e:
        logger.error(f"[deep_recall] 失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================================
# request_intermediate_response — 中途回复
# ============================================================================

@ToolRegistry.register(
    "request_intermediate_response",
    description=(
        "请求从当前已完成的思考中提取中间回复，发送给用户。"
        "用于大模型在主管/专家仍在工作时，先给用户一个初步答案。"
        "中间回复会以 [preliminary] 前缀显示给用户，最终回复不受影响。"
    ),
    params={
        "max_length": "可选，中间回复最大长度（字符），默认 500",
    },
    risk_level="LOW",
    category="mutation",
)
def request_intermediate_response(
    max_length: int = 500,
    **kwargs,
) -> Dict[str, Any]:
    """请求中途回复 — 大模型可以在专家工作时先回复用户"""
    try:
        caller_role = kwargs.get("_caller_role", "large")
        intermediate_text = ""

        # 尝试从 CognitiveBlackboard 获取最近的思考内容
        try:
            from modules.thinking.multi_model_orchestrator import get_active_sessions
            for s in get_active_sessions():
                bb = s.get("blackboard")
                if bb:
                    entries = bb.read_dialog(limit=10)
                    thoughts = [e for e in entries if e.get("type") == "thought"]
                    if thoughts:
                        latest = thoughts[-1]
                        content = latest.get("content", "")
                        import re
                        cleaned = re.sub(r'【[^】]+】', '', content)
                        cleaned = re.sub(r'<tool_use>.*?</tool_use>', '', cleaned, flags=re.DOTALL)
                        paragraphs = [p.strip() for p in cleaned.split('\n\n') if len(p.strip()) > 20]
                        if paragraphs:
                            from modules.thinking.context.compression import get_compression_engine
                            engine = get_compression_engine()
                            max_tokens = max(max_length // 4, 50)
                            intermediate_text = engine._truncate_to_tokens(paragraphs[-1], max_tokens)
                            break
        except Exception:
            pass

        if not intermediate_text:
            return {
                "success": False,
                "error": "暂无足够的思考内容可用于中间回复",
            }

        # 通过 MessageBus 发送 preliminary_response 事件
        try:
            from modules.thinking.communication.message_bus import (
                Message, MessageType, get_message_bus,
            )
            bus = get_message_bus()
            msg = Message(
                msg_type=MessageType.BROADCAST,
                sender="probe_tools",
                recipient="broadcast",
                content={
                    "action": "preliminary_response",
                    "content": intermediate_text,
                    "caller_tier": caller_role,
                },
                metadata={"event": "preliminary_response"},
            )
            try:
                asyncio.get_running_loop().create_task(bus.send(msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.warning(f"[intermediate_response] MessageBus 发送失败: {e}")

        logger.info(
            f"[intermediate_response] {caller_role} 发送中途回复 "
            f"({len(intermediate_text)} 字符)"
        )
        return {
            "success": True,
            "content": intermediate_text,
            "message": f"中途回复已发送 ({len(intermediate_text)} 字符)\n{intermediate_text[:200]}",
        }

    except Exception as e:
        logger.error(f"[intermediate_response] 失败: {e}")
        return {"success": False, "error": str(e)}
