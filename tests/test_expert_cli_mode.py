"""
单元测试：RuntimeExpert CLI 模式
验证专家的多轮工具调用能力
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

from modules.thinking.experts.base import RuntimeExpert


# ============================================================================
# Mock 类和工厂函数
# ============================================================================

class MockModelInstance:
    """模拟模型实例"""

    def __init__(self, responses: List[str] = None):
        self.responses = responses or []
        self.call_count = 0
        self.tool_calls = []

    async def generate(self, prompt: str, stream: bool = False) -> str:
        """返回预定义的响应"""
        if self.call_count < len(self.responses):
            response = self.responses[self.call_count]
            self.call_count += 1
            return response
        return "任务完成"

    async def _execute_tool(self, tool_name: str, tool_input: Dict) -> str:
        """执行工具调用"""
        self.tool_calls.append({"name": tool_name, "input": tool_input})
        return f"tool {tool_name} executed with {tool_input}"


class MockExpert(RuntimeExpert):
    """用于测试的 Mock 专家"""

    template_key = "expert_test"

    async def process(
        self,
        request_text: str,
        messages: List[Dict[str, Any]],
        dialog_context: str,
    ) -> str:
        return "Mock process result"

    def _load_identity(self):
        """返回 Mock identity"""
        mock_identity = MagicMock()
        mock_identity.name = "Test Expert"
        mock_identity.role = "test_expert"
        mock_identity.expertise = ["testing", "mocking"]
        mock_identity.tier = "standard"
        return mock_identity


def create_mock_expert(model_instance=None):
    """创建一个 Mock 专家实例"""
    expert = MockExpert(
        model_instance=model_instance or MockModelInstance(),
        blackboard=None,
        session_id="test_session",
        model_id="test_model",
    )
    return expert


# ============================================================================
# 单元测试
# ============================================================================

@pytest.mark.asyncio
async def test_expert_cli_single_iteration():
    """测试单轮执行（无工具调用）"""
    # 响应中不包含工具调用标记，应该直接完成
    model = MockModelInstance(["这是最终答案"])
    expert = create_mock_expert(model)

    result = await expert.run_cli_mode(
        task="解释 Python 闭包",
        max_iterations=5,
        timeout=60,
    )

    assert result['success'] is True
    assert result['iterations'] == 1
    assert result['tool_calls'] == 0
    assert "这是最终答案" in result['result']


@pytest.mark.asyncio
async def test_expert_cli_multi_iterations():
    """测试多轮迭代（包含工具调用）"""
    # 第一轮：调用 search_web 工具
    # 第二轮：处理结果，输出最终答案
    responses = [
        """搜索相关信息...
<tool>
name: search_web
arguments: {"query": "Python闭包"}
</tool>""",
        "基于搜索结果的最终答案"
    ]

    model = MockModelInstance(responses)
    expert = create_mock_expert(model)

    result = await expert.run_cli_mode(
        task="查询 Python 闭包并总结",
        max_iterations=5,
        timeout=60,
    )

    assert result['success'] is True
    assert result['iterations'] == 2  # 两轮
    assert result['tool_calls'] == 1  # 一次工具调用
    assert len(result['tool_history']) == 1


@pytest.mark.asyncio
async def test_expert_cli_tool_history_injection():
    """测试工具历史注入到提示词中"""
    responses = [
        """执行搜索...
<tool>
name: search_web
arguments: {"query": "test"}
</tool>""",
        "最终答案"
    ]

    model = MockModelInstance(responses)
    expert = create_mock_expert(model)

    # 捕获传给 generate() 的提示词
    prompts = []
    original_generate = model.generate

    async def capture_generate(prompt, stream=False):
        prompts.append(prompt)
        return await original_generate(prompt, stream)

    model.generate = capture_generate

    result = await expert.run_cli_mode(task="测试")

    assert result['success'] is True
    # 第二轮的提示词应该包含第一轮的工具执行历史
    assert len(prompts) >= 2
    assert "【已执行的步骤】" in prompts[1]
    assert "search_web" in prompts[1]


@pytest.mark.asyncio
async def test_expert_cli_timeout():
    """测试超时保护"""
    import asyncio

    async def slow_generate(prompt, stream=False):
        await asyncio.sleep(10)  # 延迟10秒
        return "太慢了"

    model = MockModelInstance()
    model.generate = slow_generate
    expert = create_mock_expert(model)

    result = await expert.run_cli_mode(
        task="测试超时",
        timeout=1,  # 1秒超时
    )

    assert result['success'] is False
    assert 'timeout' in result.get('error', '').lower()


@pytest.mark.asyncio
async def test_expert_cli_max_iterations():
    """测试最大迭代次数限制"""
    # 每次都调用工具，永不完成，应该在达到 max_iterations 时停止
    tool_response = """继续执行...
<tool>
name: dummy_tool
arguments: {"x": 1}
</tool>"""

    model = MockModelInstance([tool_response] * 20)  # 多个响应
    expert = create_mock_expert(model)

    result = await expert.run_cli_mode(
        task="无限循环测试",
        max_iterations=5,
        timeout=60,
    )

    assert result['success'] is True
    assert result['iterations'] == 5  # 停在5轮
    assert result['reached_max_iterations'] is True


@pytest.mark.asyncio
async def test_expert_cli_tool_execution():
    """测试工具执行和结果收集"""
    responses = [
        """调用工具...
<tool>
name: search_web
arguments: {"query": "test"}
</tool>""",
        """处理结果...
<tool>
name: parse_json
arguments: {"data": {"a": 1}}
</tool>""",
        "最终答案"
    ]

    model = MockModelInstance(responses)
    expert = create_mock_expert(model)

    result = await expert.run_cli_mode(
        task="多个工具调用",
        max_iterations=10,
    )

    assert result['success'] is True
    assert result['iterations'] >= 2
    assert result['tool_calls'] >= 1
    assert len(result['tool_history']) >= 1


@pytest.mark.asyncio
async def test_extract_tool_calls():
    """测试工具调用解析"""
    expert = create_mock_expert()

    # 测试单个工具调用
    response = """让我执行工具...
<tool>
name: search_web
arguments: {"query": "python"}
</tool>

其他文本内容"""

    tool_calls = expert._extract_tool_calls(response)

    assert len(tool_calls) >= 1
    assert tool_calls[0]['name'] == 'search_web'
    assert tool_calls[0]['arguments']['query'] == 'python'


@pytest.mark.asyncio
async def test_build_cli_prompt_without_history():
    """测试提示词构建（无历史）"""
    expert = create_mock_expert()

    prompt = expert._build_cli_prompt(
        task="测试任务",
        tool_history=[],
        iteration=1,
    )

    assert "测试任务" in prompt
    assert "第 1 轮迭代" in prompt
    assert "Test Expert" in prompt
    assert "【已执行的步骤】" not in prompt


@pytest.mark.asyncio
async def test_build_cli_prompt_with_history():
    """测试提示词构建（包含历史）"""
    expert = create_mock_expert()

    tool_history = [
        {
            'iteration': 1,
            'tool': 'search_web',
            'input': {'query': 'test'},
            'output': '搜索结果内容',
        },
        {
            'iteration': 2,
            'tool': 'parse_json',
            'input': {'data': {}},
            'output': '解析结果',
        },
    ]

    prompt = expert._build_cli_prompt(
        task="分析数据",
        tool_history=tool_history,
        iteration=3,
    )

    assert "【已执行的步骤】" in prompt
    assert "search_web" in prompt
    assert "parse_json" in prompt
    assert "搜索结果内容" in prompt
    assert "解析结果" in prompt
    assert "第 3 轮迭代" in prompt


@pytest.mark.asyncio
async def test_expert_cli_empty_response():
    """测试空响应处理"""
    model = MockModelInstance([""])  # 返回空字符串
    expert = create_mock_expert(model)

    result = await expert.run_cli_mode(task="测试空响应")

    assert result['success'] is False
    assert 'Empty response' in result.get('error', '')


# ============================================================================
# 集成测试（需要真实的模型实例）
# ============================================================================

@pytest.mark.asyncio
@pytest.mark.skip(reason="需要真实的模型实例")
async def test_expert_cli_with_real_model():
    """集成测试：使用真实模型"""
    # 这个测试需要真实的模型实例
    # 在实际环境中运行
    pass


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
