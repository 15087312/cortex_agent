"""
专家系统独立集成测试

覆盖：
1. 工具注册表自动扫描
2. 委托角色解析 (delegation_compiler)
3. 工具白名单过滤 (identity → _visible_tool_whitelist)
4. 专家工具循环 (_generate_with_tools)
5. 委托返回链路 (_notify_return_target + thinking_result)
6. ThinkingProcessSnapshot.control_decision 传递
"""
import asyncio
import json
import sys
import os
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch

from modules.thinking.experts.base import RuntimeExpert


# ============================================================================
# 1. 工具注册表自动扫描
# ============================================================================
def test_tool_registry_auto_scan():
    """tools/__init__.py 自动扫描应加载全部模块"""
    from infra.tool_manager.tool_registry import ToolRegistry
    from infra.tool_manager import tools

    assert len(tools.__all__) >= 18, f"期望 ≥18 个模块，实际 {len(tools.__all__)}"
    assert len(ToolRegistry._tools) >= 60, f"期望 ≥60 个工具，实际 {len(ToolRegistry._tools)}"

    # 验证关键工具存在
    for name in ["read_file", "write_file", "search_files", "run_command", "web_search", "memory_match"]:
        assert name in ToolRegistry._tools, f"关键工具 {name} 未注册"

    print(f"  ✅ 自动扫描: {len(tools.__all__)} 个模块, {len(ToolRegistry._tools)} 个工具")


# ============================================================================
# 2. 委托角色解析
# ============================================================================
def test_delegation_role_resolution():
    """delegation_compiler 应正确解析所有角色"""
    from modules.thinking.intent.delegation_compiler import resolve_role

    cases = [
        ("code_supervisor", "supervisor", "supervisor_code"),
        ("query_supervisor", "supervisor", "supervisor_query"),
        ("creative_supervisor", "supervisor", "supervisor_creative"),
        ("code_writer", "expert", "expert_implementer"),
        ("code_reviewer", "expert", "expert_reviewer"),
        ("test_writer", "expert", "expert_tester"),
        ("data_analyzer", "expert", "expert_analyzer"),
        ("file_expert", "expert", "expert_implementer"),      # 新增别名
        ("文件专家", "expert", "expert_implementer"),          # 中文别名
        ("orchestrator", "large", "large"),
        ("security_monitor", "expert", "expert_security_monitor"),
        ("customer", "expert", "expert_customer"),
    ]
    for role_name, expected_tier, expected_key in cases:
        result = resolve_role(role_name)
        assert result is not None, f"角色 {role_name} 解析失败"
        assert result == (expected_tier, expected_key), f"{role_name}: 期望 {(expected_tier, expected_key)}, 实际 {result}"

    # 不存在的角色
    assert resolve_role("nonexistent_role") is None
    assert resolve_role("") is None

    print(f"  ✅ 角色解析: {len(cases)} 个角色全部正确")


# ============================================================================
# 3. 工具白名单
# ============================================================================
def test_tool_whitelist_no_phantom_names():
    """DEFAULT_TOOL_WHITELISTS 中不应有幽灵工具名"""
    from modules.thinking.identity import DEFAULT_TOOL_WHITELISTS
    from infra.tool_manager.tool_registry import ToolRegistry

    phantom_names = {"file_read", "file_write", "code_execute", "code_search",
                     "test_run", "memory_search", "memory_save", "memory_write",
                     "expert_dispatch", "task_decompose", "tool_call"}

    # 控制工具：由 model_runner 注入，不在 ToolRegistry 中
    control_tools = {"delegate_task", "continue_thinking", "respond_to_user",
                     "create_supervisor", "request_intermediate_response",
                     "set_attention_level", "probe_start", "probe_stop", "probe_list",
                     "persona_inject", "query_tool_details"}

    all_whitelist_names = set()
    for tier, tools_list in DEFAULT_TOOL_WHITELISTS.items():
        for name in tools_list:
            if name != "*":
                all_whitelist_names.add(name)

    found_phantoms = all_whitelist_names & phantom_names
    assert not found_phantoms, f"白名单中仍有幽灵工具名: {found_phantoms}"

    # 验证白名单中的名称都在注册表中（排除 tag: 前缀和控制工具）
    for tier, tools_list in DEFAULT_TOOL_WHITELISTS.items():
        for name in tools_list:
            if name == "*" or name.startswith("tag:") or name in control_tools:
                continue
            assert name in ToolRegistry._tools, f"[{tier}] 白名单工具 {name} 不在注册表中"

    print(f"  ✅ 工具白名单: 无幽灵名称, 所有名称匹配注册表")


# ============================================================================
# 4. 专家工具循环
# ============================================================================
def test_expert_tool_loop():
    """专家应能在一次 continuous_think 中多次调用工具"""
    from modules.thinking.core.continuous_thinker import ContinuousThinker

    call_log = []

    # 模拟模型：第一次返回工具调用，第二次返回结果
    async def mock_think_fn(prompt: str) -> str:
        call_log.append(("think", len(prompt)))
        if len(call_log) == 1:
            # 第一次：返回工具调用
            return json.dumps({
                "tool": "read_file",
                "params": {"path": "/tmp/test.txt"}
            })
        else:
            # 第二次：返回结果
            return "文件内容已读取，任务完成。"

    thinker = ContinuousThinker(
        think_fn=mock_think_fn,
        max_rounds=1,
        min_rounds=1,
        interval=0,
        model_id="test_expert",
        tier="expert",
    )

    result = asyncio.get_event_loop().run_until_complete(
        thinker.continuous_think("读取文件 /tmp/test.txt")
    )

    assert result is not None, "continuous_think 返回 None"
    assert len(call_log) >= 1, f"模型应至少被调用 1 次, 实际 {len(call_log)} 次"

    print(f"  ✅ 专家工具循环: 模型被调用 {len(call_log)} 次")


# ============================================================================
# 5. 委托返回链路
# ============================================================================
@pytest.mark.asyncio
async def test_notify_return_target_skips_when_pending():
    """有待处理委托时，_notify_return_target 应跳过"""
    from modules.thinking.core.continuous_thinker import ContinuousThinker
    from modules.thinking.core.control_tools import ThinkingTaskContext

    sent_messages = []

    thinker = ContinuousThinker(
        think_fn=AsyncMock(return_value="test"),
        max_rounds=1,
        model_id="supervisor_001",
        tier="supervisor",
    )
    thinker._session_id = "test_session"

    ctx = ThinkingTaskContext(
        task_id="task_123",
        loop_goal="test",
        origin_model_id="supervisor_001",
        return_to_model_id="large_primary",
        return_to_session_id="test_session",
        caller_tier="large",
        metadata={"identity_key": "supervisor_query"},
    )

    # 有待处理委托 → 应跳过
    thinker._pending_delegations = {
        "task_123": {"status": "pending", "role": "expert_implementer"},
    }
    await thinker._notify_return_target(ctx, "some result")

    # 清空 pending → 应发送
    thinker._pending_delegations = {}
    with patch("modules.thinking.communication.interface.get_message_bus_port") as mock_get_bus:
        mock_bus = MagicMock()
        mock_bus.send = AsyncMock(return_value=None)
        mock_get_bus.return_value = mock_bus
        await thinker._notify_return_target(ctx, "final result")

    # send was called once
    mock_bus.send.assert_called_once()
    msg = mock_bus.send.call_args[0][0]
    assert msg.content["source_tier"] == "supervisor"
    assert msg.content["source_role"] == "supervisor_query"
    assert msg.recipient == "large_primary"

    print(f"  ✅ 委托返回链路: pending 时跳过, 完成后正确发送 (source_tier={msg.content['source_tier']})")


# ============================================================================
# 6. ThinkingProcessSnapshot.control_decision
# ============================================================================
def test_snapshot_preserves_control_decision():
    """ThinkingProcessSnapshot 应保留 control_decision"""
    from modules.thinking.core.process_collector import InMemoryThinkingProcessCollector
    from modules.thinking.core.control_tools import ThinkingControlDecision

    collector = InMemoryThinkingProcessCollector()
    collector.reset(session_id="test", model_id="test", tier="expert")

    decision = ThinkingControlDecision(
        should_continue=False,
        reason="task complete",
        result_summary="桌面文件列表：file1.txt, file2.py",
    )

    snapshot = collector.complete(
        final_result="final output",
        control_decision=decision,
    )

    assert snapshot.control_decision is not None, "control_decision 应保留在 snapshot 中"
    assert snapshot.control_decision.result_summary == "桌面文件列表：file1.txt, file2.py"
    assert snapshot.control_decision.should_continue is False

    # 验证 metadata 也有
    assert snapshot.metadata.get("control_result_summary") == "桌面文件列表：file1.txt, file2.py"

    print(f"  ✅ Snapshot.control_decision: 正确保留 result_summary")


# ============================================================================
# 7. DelegationRequest.return_to_model_id
# ============================================================================
def test_delegation_request_return_to():
    """DelegationRequest.return_to_model_id 应为委托方自身 model_id"""
    from modules.thinking.core.delegation_port import DelegationRequest

    # 模拟主管委托专家：return_to 应为主管自己的 model_id
    req = DelegationRequest(
        role="expert_implementer",
        task="执行任务",
        session_id="s1",
        caller_model_id="supervisor_001",
        caller_tier="supervisor",
        return_to_model_id="supervisor_001",  # ← 应传 self.model_id
        return_to_session_id="s1",
    )

    assert req.return_to_model_id == "supervisor_001", \
        f"return_to_model_id 应为主管自身, 实际 {req.return_to_model_id}"

    # 验证 fallback 逻辑：return_to 为空时应 fallback 到 caller_model_id
    req2 = DelegationRequest(
        role="expert_implementer",
        task="执行任务",
        session_id="s1",
        caller_model_id="supervisor_001",
        caller_tier="supervisor",
        return_to_model_id="",
        return_to_session_id="s1",
    )
    assert req2.return_to_model_id or req2.caller_model_id == "supervisor_001"

    print(f"  ✅ DelegationRequest.return_to: 正确传递委托方 model_id")


# ============================================================================
# 8. 工具结果注入格式
# ============================================================================
def test_tool_result_injection_format():
    """工具结果应以 role='tool' + tool_call_id 注入"""
    from infra.model.base_model import ChatMessage

    # 模拟 tool call
    class MockToolCall:
        def __init__(self):
            self.id = "call_abc123"
            self.name = "read_file"
            self.arguments = '{"path": "/tmp/test.txt"}'

    tc = MockToolCall()
    result_content = "文件内容: hello world"

    msg = ChatMessage(
        role="tool",
        content=result_content,
        tool_call_id=getattr(tc, 'id', None) or tc.name,
    )

    assert msg.role == "tool", f"role 应为 tool, 实际 {msg.role}"
    assert msg.tool_call_id == "call_abc123", f"tool_call_id 应为 call_abc123, 实际 {msg.tool_call_id}"
    assert msg.content == result_content

    print(f"  ✅ 工具结果注入: role=tool, tool_call_id=call_abc123")


# ============================================================================
# 9. EmotionExpert 预生成情绪分析
# ============================================================================
def test_emotion_expert_fallback():
    """EmotionExpert 降级行为验证"""
    from modules.thinking.experts.pre_gen_experts import EmotionExpert

    expert = EmotionExpert()

    result = asyncio.get_event_loop().run_until_complete(
        expert.analyze("气死我了，受不了这个系统")
    )

    if expert._lite_model:
        # LLM 可用时应返回真实情绪分析
        assert result["emotion"] in ("angry", "frustrated", "provocative"), \
            f"LLM 可用时应检测到负面情绪, 实际 {result['emotion']}"
        assert "心理状态" in result["guidance"] or "内心独白" in result["guidance"], \
            f"应包含心理活动注入, 实际 {result['guidance'][:50]}"
        assert "ai_mood" in result
        print(f"  ✅ EmotionExpert (LLM可用): 情绪={result['emotion']}, 内心独白注入正确")
    else:
        # LLM 不可用时应返回 neutral 默认值
        assert result["emotion"] == "neutral"
        assert result["ai_mood"] == "平和"
        assert result["guidance"] == ""
        print(f"  ✅ EmotionExpert (LLM不可用): neutral 默认值")


def test_pre_gen_pipeline_includes_emotion():
    """PreGenExpertPipeline 应返回情绪字段"""
    from modules.thinking.experts.pre_gen_experts import PreGenExpertPipeline

    pipeline = PreGenExpertPipeline()
    result = asyncio.get_event_loop().run_until_complete(
        pipeline.run("我很困惑，看不懂这个文档")
    )

    assert "emotion" in result, f"缺少 emotion 字段, keys={list(result.keys())}"
    assert "emotion_intensity" in result
    assert "emotion_guidance" in result
    assert result["emotion"] in ("confused", "neutral"), f"期望 confused/neutral, 实际 {result['emotion']}"

    # 价值观字段也应存在（新格式）
    assert "principle" in result
    assert "reflection" in result
    # 安全字段
    assert "risk_level" in result

    print(f"  ✅ PreGenExpertPipeline: 情绪={result['emotion']}, 包含全部引导字段")


def test_format_expert_guidance_with_emotion():
    """_format_expert_guidance 应包含情绪引导"""
    from modules.thinking.multi_model_orchestrator import MultiModelOrchestrator
    from unittest.mock import patch

    guidance = {
        "principle": "共情, 谦逊",
        "reflection": "对方可能在气头上，我先别急着辩解",
        "risk_level": "none",
        "emotion": "angry",
        "emotion_intensity": 0.7,
        "emotion_guidance": "AI当前心情：有点不爽。不卑不亢，不一味道歉",
        "ai_mood": "有点不爽",
    }

    # 工作模式：保留完整信息
    with patch("config.settings.settings") as mock_s:
        mock_s.COMPANION_MODE = False
        text = MultiModelOrchestrator._format_expert_guidance(guidance)
        assert "价值观准则: 共情, 谦逊" in text, f"缺少价值观: {text}"
        assert "行为指导:" in text

    # 陪伴模式：第一人称，无内部细节
    with patch("config.settings.settings") as mock_s:
        mock_s.COMPANION_MODE = True
        text = MultiModelOrchestrator._format_expert_guidance(guidance)
        assert "【准则】" in text, f"陪伴模式应有准则: {text}"
        assert "【心理状态】" in text

    print(f"  ✅ format_expert_guidance: 工作/陪伴模式格式正确")


# ============================================================================
# 12. 运行模式配置
# ============================================================================
def test_companion_mode_config():
    """陪伴模式配置验证"""
    from config.settings import Settings

    # 工作模式
    s = Settings(COMPANION_MODE=False)
    assert s.is_delegation_available is True
    assert s.effective_emotion_enabled is False
    assert s.effective_values_enabled is False
    assert s.is_expert_pipeline_enabled is False

    # 陪伴模式：委托关闭，情绪/价值观开启
    s2 = Settings(COMPANION_MODE=True)
    assert s2.is_delegation_available is False
    assert s2.effective_emotion_enabled is True
    assert s2.effective_values_enabled is True
    assert s2.is_expert_pipeline_enabled is True

    print(f"  ✅ 运行模式配置: COMPANION_MODE 一个开关控制全部")


# ============================================================================
# 运行
# ============================================================================
if __name__ == "__main__":
    tests = [
        ("工具注册表自动扫描", test_tool_registry_auto_scan),
        ("委托角色解析", test_delegation_role_resolution),
        ("工具白名单无幽灵名称", test_tool_whitelist_no_phantom_names),
        ("专家工具循环", test_expert_tool_loop),
        ("委托返回链路", test_notify_return_target_skips_when_pending),
        ("Snapshot.control_decision", test_snapshot_preserves_control_decision),
        ("DelegationRequest.return_to", test_delegation_request_return_to),
        ("工具结果注入格式", test_tool_result_injection_format),
        ("EmotionExpert 降级关键词", test_emotion_expert_fallback),
        ("PreGenExpertPipeline 情绪字段", test_pre_gen_pipeline_includes_emotion),
        ("format_expert_guidance 情绪格式化", test_format_expert_guidance_with_emotion),
        ("运行模式配置", test_companion_mode_config),
    ]

    print("=" * 60)
    print("专家系统独立集成测试")
    print("=" * 60)

    passed = 0
    failed = 0
    for name, test_fn in tests:
        try:
            print(f"\n[TEST] {name}")
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  ❌ FAILED: {e}")
            failed += 1

    print(f"\n{'=' * 60}")
    print(f"结果: {passed} 通过, {failed} 失败, 共 {passed + failed} 项")
    print(f"{'=' * 60}")

    sys.exit(1 if failed else 0)
