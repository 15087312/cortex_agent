"""对话图片直连多模态 — turn_images contextvar + LargeModelClient 图片块序列化"""
import asyncio

from infra.model.base_model import ChatMessage
from infra.model.large_model_client import LargeModelClient
from modules.thinking.turn_images import set_turn_images, get_turn_images, clear_turn_images


def _client(api_format="openai"):
    return LargeModelClient(
        api_key="test-key",
        api_url="https://example.com/v1/chat/completions",
        api_format=api_format,
    )


# ── contextvar 基础行为 ──

def test_turn_images_contextvar_basic():
    clear_turn_images()
    assert get_turn_images() is None
    set_turn_images(["data:image/png;base64,QUJD"])
    assert get_turn_images() == ["data:image/png;base64,QUJD"]
    set_turn_images(None)
    assert get_turn_images() is None


def test_turn_images_propagates_to_child_task():
    async def _run():
        set_turn_images(["data:image/jpeg;base64,YQ=="])
        seen = await asyncio.create_task(_child())
        return seen

    async def _child():
        return get_turn_images()

    assert asyncio.run(_run()) == ["data:image/jpeg;base64,YQ=="]


# ── OpenAI 格式序列化 ──

def test_openai_attaches_image_url_and_clears():
    async def _run():
        client = _client("openai")
        set_turn_images(["data:image/png;base64,QUJD"])
        msgs = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="看这张图"),
        ]
        out = client._messages_to_api(msgs)
        user_msg = out[-1]
        assert isinstance(user_msg["content"], list)
        assert user_msg["content"][0] == {"type": "text", "text": "看这张图"}
        assert user_msg["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,QUJD"},
        }
        # 挂载后清除：同一回合后续调用不再重复附图
        out2 = client._messages_to_api([ChatMessage(role="user", content="x")])
        assert out2[0]["content"] == "x"
        return True

    assert asyncio.run(_run())


def test_openai_no_images_untouched():
    async def _run():
        client = _client("openai")
        set_turn_images(None)
        msgs = [ChatMessage(role="user", content="普通文本")]
        out = client._messages_to_api(msgs)
        assert out[0]["content"] == "普通文本"
        return True

    assert asyncio.run(_run())


# ── Anthropic 格式序列化 ──

def test_anthropic_attaches_image_block():
    async def _run():
        client = _client("anthropic")
        set_turn_images(["data:image/png;base64,QUJD"])
        msgs = [ChatMessage(role="user", content="看这张图")]
        out = client._messages_to_api(msgs)
        blocks = out[0]["content"]
        assert blocks[0] == {"type": "text", "text": "看这张图"}
        assert blocks[1] == {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
        }
        return True

    assert asyncio.run(_run())


def test_anthropic_skips_tool_result_user_message():
    """图片只挂到纯文本 user 消息，不挂到 tool_result（content 为 list）消息"""
    async def _run():
        client = _client("anthropic")
        set_turn_images(["data:image/png;base64,QUJD"])
        msgs = [
            ChatMessage(role="user", content="看这张图"),
            ChatMessage(role="tool", content="工具返回", tool_call_id="call_1"),
            ChatMessage(role="user", content="继续"),
        ]
        out = client._messages_to_api(msgs)
        # 倒序找最后一个纯文本 user 消息：挂到 "继续" 上
        assert isinstance(out[-1]["content"], list)
        assert out[-1]["content"][0] == {"type": "text", "text": "继续"}
        assert any(b.get("type") == "image" for b in out[-1]["content"])
        # tool_result 消息（role=user, content=list）不被改动
        tool_msg = next(m for m in out if isinstance(m.get("content"), list) and m["content"][0].get("type") == "tool_result")
        assert tool_msg["content"] == [{"type": "tool_result", "tool_use_id": "call_1", "content": "工具返回"}]
        return True

    assert asyncio.run(_run())


# ── dataURL 解析 ──

def test_parse_image_dataurl():
    assert LargeModelClient._parse_image_dataurl("data:image/jpeg;base64,YWJj") == ("image/jpeg", "YWJj")
    assert LargeModelClient._parse_image_dataurl("rawbase64") == ("image/jpeg", "rawbase64")
