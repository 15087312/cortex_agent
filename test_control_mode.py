#!/usr/bin/env python3
"""
Control 模式集成测试
"""
import asyncio
import sys
from unittest.mock import AsyncMock, patch

# 设置 control 模式
import os
os.environ['EXECUTION_MODE'] = 'control'


async def test_control_mode():
    """测试 control 模式的工具安全检查"""
    from config.settings import settings
    from modules.security_system.tool_security_gate import (
        get_tool_security_gate, HIGH_RISK_TOOLS, MEDIUM_RISK_TOOLS
    )

    print("=" * 60)
    print("Control 模式集成测试")
    print("=" * 60)

    # 验证配置
    print(f"\n✓ EXECUTION_MODE: {settings.EXECUTION_MODE}")
    print(f"✓ effective_execution_mode: {settings.effective_execution_mode}")
    print(f"✓ SECURITY_REVIEW_MODE: {settings.SECURITY_REVIEW_MODE}")
    print(f"✓ effective_security_review_mode: {settings.effective_security_review_mode}")

    # 获取安全门控
    gate = get_tool_security_gate()

    # 测试 1: LOW 风险工具应该直接放行
    print("\n" + "-" * 60)
    print("测试 1: LOW 风险工具（web_search）")
    print("-" * 60)
    allowed, reason = await gate.check(
        tool_name="web_search",
        tool_params={"query": "test"},
        caller_tier="large",
        caller_model_id="test_model",
        dialog_context="test context"
    )
    print(f"结果: allowed={allowed}, reason={reason}")
    assert allowed == True, "LOW 风险工具应该被放行"
    print("✓ 通过")

    # 测试 2: MEDIUM 风险工具应该等待用户确认
    print("\n" + "-" * 60)
    print("测试 2: MEDIUM 风险工具（write_file）")
    print("-" * 60)

    # 模拟用户批准
    async def mock_user_review(*args, **kwargs):
        print("  → 模拟用户审批: 批准")
        return True, "用户批准"

    with patch.object(gate, '_check_user_review', new=mock_user_review):
        allowed, reason = await gate.check(
            tool_name="write_file",
            tool_params={"path": "/tmp/test.txt", "content": "test"},
            caller_tier="large",
            caller_model_id="test_model",
            dialog_context="test context"
        )

    print(f"结果: allowed={allowed}, reason={reason}")
    assert allowed == True, "用户批准后应该允许执行"
    print("✓ 通过")

    # 测试 3: HIGH 风险工具应该等待用户确认
    print("\n" + "-" * 60)
    print("测试 3: HIGH 风险工具（exec_command）")
    print("-" * 60)

    # 模拟用户拒绝
    async def mock_user_reject(*args, **kwargs):
        print("  → 模拟用户审批: 拒绝")
        return False, "用户拒绝"

    with patch.object(gate, '_check_user_review', new=mock_user_reject):
        allowed, reason = await gate.check(
            tool_name="exec_command",
            tool_params={"command": "echo test"},
            caller_tier="large",
            caller_model_id="test_model",
            dialog_context="test context"
        )

    print(f"结果: allowed={allowed}, reason={reason}")
    assert allowed == False, "用户拒绝应该拦截执行"
    print("✓ 通过")

    # 版本信息测试
    print("\n" + "-" * 60)
    print("版本信息")
    print("-" * 60)
    from cortex.version import get_version_string
    print(f"版本: {get_version_string()}")
    print("✓ 通过")

    print("\n" + "=" * 60)
    print("所有测试通过! ✓")
    print("=" * 60)


if __name__ == "__main__":
    try:
        asyncio.run(test_control_mode())
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
