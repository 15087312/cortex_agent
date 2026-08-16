"""验证各模型客户端 POST 请求体格式

拦截 HTTP 请求，检查 system prompt、headers、URL 是否符合预期。
不实际调用 API — 使用 MockSession 在内存中完成验证。
"""
import pytest
import asyncio
from dataclasses import dataclass
from unittest.mock import patch


# ── Mock Session（拦截 POST 请求） ──

class _MockResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    async def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}

    async def text(self):
        return '{"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}'

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockSession:
    closed = False

    def __init__(self):
        self.captured = {}

    def post(self, url, **kwargs):
        self.captured[str(url)] = {
            "headers": dict(kwargs.get("headers", {})),
            "json": kwargs.get("json", {}),
        }
        return _MockResponse()

    async def close(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


# ── 辅助函数 ──

async def _capture_generate(client_cls, api_url, tier: str, role: str) -> dict:
    """创建一个客户端，注入 mock session，调用 generate()，返回捕获的 POST 数据

    人设由调用方用 PromptComposer 显式构建后传入（generate() 不再自动注入默认人设，
    仅原样发送调用方提供的 system_prompt）。
    """
    from config.settings import settings
    from config.prompts.composer import PromptComposer, PromptRequest

    # CI 无 ~/.cortex/settings.json → key/url 为空；fallback 保证格式层走 openai 结构
    api_key = settings.LARGE_MODEL_API_KEY or "test-key"
    api_url = api_url or "http://localhost:1/v1/chat/completions"
    client = client_cls(api_key=api_key, api_url=api_url)

    session = MockSession()
    client._session = session
    system_prompt = PromptComposer().build_system(
        PromptRequest(tier=tier, role=role, mode=settings.effective_execution_mode)
    )
    await client.generate("写一个 hello world", system_prompt=system_prompt)

    # 返回第一个捕获的 POST
    for url, data in session.captured.items():
        return {"url": url, "headers": data["headers"], "body": data["json"]}
    return {}


# ── 测试用例 ──


class TestLargeModelPostFormat:
    """大模型 POST 请求格式（tier=large, role=orchestrator）"""

    @pytest.mark.asyncio
    async def test_system_prompt_contains_identity(self):
        from infra.model.large_model_client import LargeModelClient
        from config.settings import settings

        # 隔离用户的自定义人设/覆盖，保证断言默认人格（不受 ~/.cortex 配置影响）
        with patch.object(type(settings), 'get_persona', return_value=""), \
             patch.object(type(settings), 'get_system_override', return_value=""):
            data = await _capture_generate(LargeModelClient, settings.LARGE_MODEL_API_URL, "large", "orchestrator")
            sys_content = data["body"]["messages"][0]["content"]

            assert "系统主模型" in sys_content, "缺少层级身份声明"
            assert "用户与系统之间的唯一桥梁" in sys_content, "缺少 orchestrator 人格"

    @pytest.mark.asyncio
    async def test_system_prompt_contains_safety_rules(self):
        from infra.model.large_model_client import LargeModelClient
        from config.settings import settings

        data = await _capture_generate(LargeModelClient, settings.LARGE_MODEL_API_URL, "large", "orchestrator")
        sys_content = data["body"]["messages"][0]["content"]

        assert "安全规则" in sys_content, "缺少安全规则段"
        assert "must_follow=True" in sys_content, "缺少安全指令标记"

    @pytest.mark.asyncio
    async def test_system_prompt_contains_perception(self):
        from infra.model.large_model_client import LargeModelClient
        from config.settings import settings

        data = await _capture_generate(LargeModelClient, settings.LARGE_MODEL_API_URL, "large", "orchestrator")
        sys_content = data["body"]["messages"][0]["content"]

        assert "被动感知" in sys_content, "缺少感知系统段"

    @pytest.mark.asyncio
    async def test_post_url_and_auth(self):
        from infra.model.large_model_client import LargeModelClient
        from config.settings import settings

        # CI 无 ~/.cortex 配置 → model 名固定 mock，避免依赖本机 LARGE_MODEL_NAME
        with patch.object(settings, "LARGE_MODEL_NAME", "deepseek-v4-flash"):
            data = await _capture_generate(LargeModelClient, settings.LARGE_MODEL_API_URL, "large", "orchestrator")

        assert "chat/completions" in data["url"], f"URL 格式错误: {data['url']}"
        assert "Bearer" in data["headers"].get("Authorization", ""), "缺少 Bearer 认证"
        assert data["body"]["model"] == "deepseek-v4-flash", f"model 字段错误: {data['body']['model']}"

    @pytest.mark.asyncio
    async def test_no_old_hardcoded_patterns(self):
        from infra.model.large_model_client import LargeModelClient
        from config.settings import settings

        data = await _capture_generate(LargeModelClient, settings.LARGE_MODEL_API_URL, "large", "orchestrator")
        sys_content = data["body"]["messages"][0]["content"]

        assert "你是本AI系统的【主模型】" not in sys_content, "残留旧硬编码"


class TestMediumModelPostFormat:
    """中模型 POST 请求格式（tier=supervisor, role=code_supervisor）"""

    @pytest.mark.asyncio
    async def test_system_prompt_contains_supervisor_identity(self):
        from infra.model.medium_model_client import MediumModelClient
        from config.settings import settings

        data = await _capture_generate(MediumModelClient, settings.MEDIUM_MODEL_API_URL, "supervisor", "code_supervisor")
        msgs = data["body"]["messages"]
        assert msgs[0]["role"] == "system", "中模型缺 system prompt"

        sys_content = msgs[0]["content"]
        assert "主管模型" in sys_content, "缺少主管身份"
        assert "delegate_task" in sys_content, "缺少委托工具指令"

    @pytest.mark.asyncio
    async def test_has_user_message_after_system(self):
        from infra.model.medium_model_client import MediumModelClient
        from config.settings import settings

        data = await _capture_generate(MediumModelClient, settings.MEDIUM_MODEL_API_URL, "supervisor", "code_supervisor")
        msgs = data["body"]["messages"]
        assert len(msgs) >= 2, f"需要至少2条消息，实际: {len(msgs)}"
        assert msgs[1]["role"] == "user", "system 后面必须是 user 消息"


class TestSmallModelPostFormat:
    """小模型 POST 请求格式（tier=expert, role=code_writer）"""

    @pytest.mark.asyncio
    async def test_system_prompt_contains_expert_identity(self):
        from infra.model.small_model_client import SmallModelClient
        from config.settings import settings

        data = await _capture_generate(SmallModelClient, settings.SMALL_MODEL_API_URL, "expert", "code_writer")
        msgs = data["body"]["messages"]
        assert msgs[0]["role"] == "system", "小模型缺 system prompt"

        sys_content = msgs[0]["content"]
        assert "执行专家" in sys_content, "缺少专家身份"
        assert "continue_thinking" in sys_content, "缺少 continue_thinking 指令"


class TestTodoInstructionInPrompt:
    """todo 工具指令应注入 agent 模式各模型的系统提示词"""

    @pytest.mark.asyncio
    async def test_all_tiers_prompt_contains_todo(self):
        from config.prompts.composer import PromptComposer, PromptRequest
        composer = PromptComposer()
        for tier, role in [("large", "orchestrator"), ("supervisor", "code_supervisor"), ("expert", "code_writer")]:
            sp = composer.build_system(PromptRequest(tier=tier, role=role, mode="edit"))
            assert "todo" in sp, f"{tier} 系统提示应含 todo 指令"
            assert "先使用 todo" in sp or "先创建" in sp or "先使用 todo 工具" in sp, f"{tier} 应有'先使用 todo'引导"
