"""当前回合图片上下文 — 把用户上传的图片透传给本轮大模型请求（直连多模态）。

WS 网关在派发回合任务前调用 set_turn_images(images)；contextvar 随
asyncio.create_task 复制到子任务（agent 编排 / chatonly 思考链路），
LargeModelClient._messages_to_api 序列化时读取，挂到最后一个 user 消息上，
挂载后立即清除，避免同一回合 ReAct 循环重复附图。

注意：只对"当前回合"生效；历史回放/新回合不会带旧图片。
"""
import contextvars
from typing import List, Optional

_turn_images: contextvars.ContextVar = contextvars.ContextVar(
    "cortex_turn_images", default=None
)


def set_turn_images(images: Optional[List[str]]) -> None:
    """设置当前回合的图片 dataURL 列表；无图片传 None/[] 以清除。"""
    _turn_images.set(images)


def get_turn_images() -> Optional[List[str]]:
    return _turn_images.get()


def clear_turn_images() -> None:
    _turn_images.set(None)
