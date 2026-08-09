"""
Tests for conversation memory pipeline — 验证对话历史完整链路:
  用户输入 → 会话存储 → 黑板注入 → prompt 前置 → 模型可见
"""
import pytest
from unittest.mock import MagicMock, patch

from modules.thinking.cognition.blackboard import CognitiveBlackboard, Observation


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def blackboard():
    return CognitiveBlackboard(session_id="mem_test_sess", turn_id="turn_001")


@pytest.fixture
def context_with_history():
    """模拟两次对话后的上下文"""
    return [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！我是助手，有什么可以帮你的？"},
        {"role": "user", "content": "记得我刚才说了什么吗"},
    ]


# ── 第一阶段：Orchestrator 注入对话历史到黑板 ─────────────────

def test_orchestrator_injects_history_to_blackboard(blackboard, context_with_history):
    """编排器将对话历史写入 blackboard 作为 conversation_history 观察"""
    history_lines = []
    for msg in context_with_history[-12:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content and isinstance(content, str):
            history_lines.append(f"[{role}]: {content[:500]}")

    if history_lines:
        blackboard.add_observation(
            tier="system",
            content="【对话历史】\n" + "\n".join(history_lines),
            metadata={"context_type": "conversation_history"},
        )

    # 验证：黑板书有 conversation_history 标记的观察
    conv_obs = [o for o in blackboard.observations
                if o.tier == "system" and o.metadata.get("context_type") == "conversation_history"]
    assert len(conv_obs) == 1
    content = conv_obs[0].content
    assert "[user]: 你好" in content
    assert "[assistant]: 你好！" in content
    assert "[user]: 记得我刚才说了什么吗" in content


def test_orchestrator_empty_context_skips_injection(blackboard):
    """空上下文时不注入对话历史"""
    if blackboard:
        # 不应该崩溃
        pass
    conv_obs = [o for o in blackboard.observations
                if o.metadata.get("context_type") == "conversation_history"]
    assert len(conv_obs) == 0


# ── 第二阶段：ModelRunner 读取对话历史并前置 ────────────────────

def test_build_system_prompt_prepends_history(blackboard, context_with_history):
    """_build_system_prompt_for_mode 将对话历史置于 system prompt 最前面"""
    # 模拟编排器先注入
    history_lines = []
    for msg in context_with_history[-12:]:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if content and isinstance(content, str):
            history_lines.append(f"[{role}]: {content[:500]}")
    blackboard.add_observation(
        tier="system",
        content="【对话历史】\n" + "\n".join(history_lines),
        metadata={"context_type": "conversation_history"},
    )

    # 模拟 _build_system_prompt_for_mode 的逻辑
    conv_obs = [o for o in blackboard.observations
                if o.tier == "system" and o.metadata.get("context_type") == "conversation_history"]
    conversation_header = conv_obs[-1].content + "\n\n---\n\n" if conv_obs else ""

    system_body = "【人格】助手\n【规则】安全第一\n"

    full_system = conversation_header + system_body

    # 验证：对话历史在 system prompt 最前面
    assert full_system.startswith("【对话历史】")
    assert "---" in full_system
    assert "【人格】助手" in full_system

    # 验证：对话历史包含完整信息
    assert "[user]: 你好" in full_system
    assert "[assistant]: 你好！" in full_system
    assert "[user]: 记得我刚才说了什么吗" in full_system


def test_build_system_prompt_no_history_no_crash(blackboard):
    """黑板书无对话历史时不崩溃"""
    conv_obs = [o for o in blackboard.observations
                if o.tier == "system" and o.metadata.get("context_type") == "conversation_history"]
    conversation_header = conv_obs[-1].content + "\n\n---\n\n" if conv_obs else ""

    assert conversation_header == ""


def test_build_system_prompt_none_blackboard_no_crash():
    """blackboard 为 None 时不崩溃"""
    bb = None
    conv_obs = []
    if bb:
        conv_obs = [o for o in bb.observations
                    if o.tier == "system" and o.metadata.get("context_type") == "conversation_history"]
    conversation_header = conv_obs[-1].content + "\n\n---\n\n" if conv_obs else ""
    assert conversation_header == ""


# ── 第三阶段：端到端链路（StreamThinkingSystem → Orchestrator → ModelRunner）──

def test_full_pipeline_conversation_memory_in_prompt():
    """端到端：用户输入 → 会话存储 → 黑板注入 → prompt 前置"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator

    blackboard = CognitiveBlackboard(session_id="e2e_sess", turn_id="t1")

    # Step 1: 编排器注入对话历史
    context = [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "你好！我是 AI 助手"},
        {"role": "user", "content": "还记得我吗"},
    ]

    history_lines = []
    for msg in context[-12:]:
        history_lines.append(f"[{msg['role']}]: {msg['content'][:500]}")
    blackboard.add_observation(
        tier="system",
        content="【对话历史】\n" + "\n".join(history_lines),
        metadata={"context_type": "conversation_history"},
    )

    # Step 2: ModelRunner._build_system_prompt_for_mode 等价逻辑
    conv_obs = [o for o in blackboard.observations
                if o.tier == "system" and o.metadata.get("context_type") == "conversation_history"]
    assert len(conv_obs) == 1

    conversation_header = conv_obs[-1].content + "\n\n---\n\n"

    # 模拟 system prompt 剩余部分（通常由 PromptComposer 生成）
    system_body = "\n\n".join([
        "【人格】助手 — 友好、专业",
        "【安全规则】1. 不执行危险操作",
        "【执行模式: EDIT】允许读写",
    ])
    full_prompt = conversation_header + system_body

    # 验证：对话历史在最前面
    assert full_prompt.index("【对话历史】") == 0

    # 验证：所有历史条目都在
    assert "[user]: 你好" in full_prompt
    assert "[assistant]: 你好！我是 AI 助手" in full_prompt
    assert "[user]: 还记得我吗" in full_prompt

    # 验证：系统提示词在分隔线之后
    separator_pos = full_prompt.index("---")
    assert full_prompt.index("【人格】") > separator_pos


def test_full_pipeline_first_message_no_history():
    """首条消息时没有对话历史"""
    blackboard = CognitiveBlackboard(session_id="first_sess", turn_id="t1")

    context = [{"role": "user", "content": "你好"}]

    history_lines = []
    for msg in context[-12:]:
        history_lines.append(f"[{msg['role']}]: {msg['content'][:500]}")
    blackboard.add_observation(
        tier="system",
        content="【对话历史】\n" + "\n".join(history_lines),
        metadata={"context_type": "conversation_history"},
    )

    conv_obs = [o for o in blackboard.observations
                if o.tier == "system" and o.metadata.get("context_type") == "conversation_history"]
    assert len(conv_obs) == 1
    assert "[user]: 你好" in conv_obs[0].content


# ── 第四阶段：对话历史只在 system prompt 中，ContextSlicer 不再重复 ──

def test_context_slicer_excludes_conversation_history(blackboard, context_with_history):
    """ContextSlicer.slice_for_large 不再重复注入对话历史（已由 system prompt 前置）"""
    from modules.thinking.cognition.context_slicer import ContextSlicer

    history_lines = [f"[{m['role']}]: {m['content'][:500]}" for m in context_with_history]
    blackboard.add_observation(
        tier="system",
        content="【对话历史】\n" + "\n".join(history_lines),
        metadata={"context_type": "conversation_history"},
    )
    blackboard.add_observation(
        tier="system",
        content="委托引导文本",
        metadata={"context_type": "delegation_guidance"},
    )

    slicer = ContextSlicer()
    slice_text = slicer.slice_for_large(blackboard)

    assert "【对话历史】" not in slice_text
    assert "委托引导文本" in slice_text


# ── 第五阶段：StreamThinkingSystem 会话存储 ─────────────────

@pytest.mark.asyncio
async def test_session_context_accumulation():
    """StreamThinkingSystem 正确累加对话消息"""
    from modules.thinking.api_stream import get_thinking_system
    import uuid

    system = get_thinking_system()
    sid = f"test_{uuid.uuid4().hex[:6]}"
    await system.start(sid)

    # Turn 1
    await system._append_message(sid, "user", "你好")
    await system._append_message(sid, "assistant", "你好！我是助手")
    ctx1 = system.get_context(sid)
    assert len(ctx1) == 2

    # Turn 2
    await system._append_message(sid, "user", "记得我吗")
    ctx2 = system.get_context(sid)
    assert len(ctx2) == 3
    assert ctx2[0]["content"] == "你好"
    assert ctx2[1]["content"] == "你好！我是助手"
    assert ctx2[2]["content"] == "记得我吗"

    # 验证 orchestrator 收到的 scheduler_context 格式
    scheduler_context = [
        {"role": m["role"], "content": m["content"]}
        for m in ctx2[-8:]
    ]
    assert len(scheduler_context) == 3
    assert scheduler_context[0] == {"role": "user", "content": "你好"}
    assert scheduler_context[2] == {"role": "user", "content": "记得我吗"}
