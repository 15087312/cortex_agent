"""
模型间通信模块 — ModelMessageBus

跨模块调用优先依赖 interface 中的 MessageBusPort/get_message_bus_port。
"""
from .interface import Message, MessageBusPort, MessageType, get_message_bus_port
from .message_bus import ModelMessageBus, get_message_bus

__all__ = [
    "Message",
    "MessageBusPort",
    "MessageType",
    "ModelMessageBus",
    "get_message_bus",
    "get_message_bus_port",
]
