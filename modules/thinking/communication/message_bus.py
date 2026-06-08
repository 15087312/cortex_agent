"""
模型消息总线 — 模型间通信基础设施

支持:
- 寻址消息 (point-to-point)
- 请求-响应 (RPC 风格，带超时)
- 广播 (一对多)
- 订阅 (推模式)
- 消息 TTL 自动清理

替代 GlobalContextPool 的通信角色（数据存储角色保留）。
"""
import time
import uuid
import asyncio
import threading
from enum import Enum
from typing import Dict, List, Optional, Any, Callable, Awaitable
from dataclasses import dataclass, field
from collections import defaultdict, deque

from utils.logger import setup_logger

logger = setup_logger("message_bus")

# 配置
MAX_QUEUE_SIZE = 1000
DEFAULT_TTL = 300  # 5 分钟
CLEANUP_INTERVAL = 60  # 每 60 秒清理过期消息


class MessageType(str, Enum):
    """消息类型"""
    TASK_ASSIGN = "task_assign"            # 任务分配 (large → supervisor)
    TASK_RESULT = "task_result"            # 任务结果 (supervisor → large)
    EXPERT_DISPATCH = "expert_dispatch"    # 专家调度 (supervisor → expert)
    EXPERT_RESULT = "expert_result"        # 专家结果 (expert → supervisor)
    QUERY = "query"                        # 信息查询 (任意 → 任意)
    RESPONSE = "response"                  # 查询响应
    BROADCAST = "broadcast"                # 广播 (large → 所有)
    ALERT = "alert"                        # 警报 (任意 → large)
    HEARTBEAT = "heartbeat"                # 心跳
    SYSTEM = "system"                      # 系统消息


@dataclass
class Message:
    """消息 — 模型间通信的基本单位"""

    msg_id: str = ""
    msg_type: MessageType = MessageType.SYSTEM
    sender: str = ""                       # 发送者 model_id
    recipient: str = ""                    # 接收者 model_id 或 "broadcast"
    correlation_id: str = ""               # 请求-响应匹配 ID
    content: Any = None                    # 消息体
    timestamp: float = field(default_factory=time.time)
    ttl: float = DEFAULT_TTL               # 过期时间 (秒)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.msg_id:
            self.msg_id = f"msg_{int(self.timestamp)}_{uuid.uuid4().hex[:8]}"

    @property
    def expired(self) -> bool:
        return (time.time() - self.timestamp) > self.ttl

    def to_dict(self) -> dict:
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "correlation_id": self.correlation_id,
            "content": str(self.content) if self.content else "",
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


class ModelMessageBus:
    """模型消息总线"""

    def __init__(self):
        # 消息队列: recipient_id → deque[Message]
        self._queues: Dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_QUEUE_SIZE))
        # 响应等待: correlation_id → asyncio.Future
        self._pending_responses: Dict[str, asyncio.Future] = {}
        # 订阅者: recipient_id → List[callback]
        self._subscriptions: Dict[str, List[Callable]] = defaultdict(list)
        # 广播历史 (最近 200 条)
        self._broadcast_history: deque = deque(maxlen=200)
        # 统计
        self._stats = {"sent": 0, "received": 0, "expired": 0, "broadcasts": 0}
        # S6: 事件发射器 (per-session，供 WebSocket 流使用) - session_id → emitter
        self._event_emitters: Dict[str, Optional[Callable[[Dict[str, Any]], None]]] = {}

        # 异步锁（事件循环变化时自动重建）
        try:
            self.__lock_loop_id = id(asyncio.get_running_loop())
        except RuntimeError:
            self.__lock_loop_id = None
        self.__lock = asyncio.Lock()
        logger.info("[消息总线] 初始化完成")

    @property
    def lock(self) -> asyncio.Lock:
        """获取当前事件循环的锁 — 跨循环时自动重建"""
        try:
            current_loop = asyncio.get_running_loop()
            current_id = id(current_loop)
        except RuntimeError:
            # 无运行中的事件循环，返回已有的锁
            return self.__lock

        if current_id != self.__lock_loop_id:
            logger.warning("[消息总线] 检测到事件循环变化，重建 async lock")
            self.__lock = asyncio.Lock()
            self.__lock_loop_id = current_id
        return self.__lock

    # ------------------------------------------------------------------
    # 事件发射器 (将消息总线事件桥接到 WebSocket 流)
    # ------------------------------------------------------------------

    def set_event_emitter(
        self,
        emitter: Optional[Callable[[Dict[str, Any]], None]],
        session_id: str = "",
    ) -> None:
        """设置事件发射器 — 所有 send/broadcast 都会回调此函数

        Args:
            emitter: fn(event_dict) — 接收统一事件字典
            session_id: 会话ID（per-session 隔离；留空表示全局设置）
        """
        if session_id:
            if emitter is None:
                self._event_emitters.pop(session_id, None)
            else:
                self._event_emitters[session_id] = emitter
        else:
            # 兼容旧调用：全局/广播模式
            if emitter is None:
                self._event_emitters.clear()
            else:
                # 全局 emitter 使用空字符串 key
                self._event_emitters[""] = emitter

    def _emit_event(self, event_type: str, message: "Message", extra: Dict[str, Any] = None) -> None:
        """向已注册的事件发射器发送事件"""
        # 只转发 broadcast 事件（Blackboard 对话框条目），过滤内部协调噪音
        if event_type != "broadcast":
            return
        # S6: 从 message.metadata 或尝试获取 session_id
        session_id = message.metadata.get("session_id", "") if message.metadata else ""

        # 优先使用 session-specific emitter，其次使用全局 emitter
        emitter = self._event_emitters.get(session_id)
        if emitter is None and session_id:
            # 如果没有特定会话的 emitter，试试全局
            emitter = self._event_emitters.get("")

        if emitter is None:
            return

        event = {
            "event_id": message.msg_id,
            "timestamp": message.timestamp,
            "source": message.sender,
            "type": "model_comm",
            "action": event_type,
            "target": message.recipient,
            "success": True,
            "latency_ms": 0,
            "payload": {
                "msg_type": message.msg_type.value,
                "sender": message.sender,
                "recipient": message.recipient,
                "content": message.content if isinstance(message.content, dict) else (str(message.content) if message.content else ""),
                "correlation_id": message.correlation_id,
                "metadata": message.metadata,
                **(extra or {}),
            }
        }
        try:
            emitter(event)
        except Exception as e:
            logger.debug(f"[消息总线] 事件发射器回调失败: {e}")

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    async def send(self, message: Message) -> str:
        """发送消息到指定接收者

        Returns:
            msg_id
        """
        async with self.lock:
            self._queues[message.recipient].append(message)
            self._stats["sent"] += 1

        logger.debug(
            f"[消息总线] {message.sender} → {message.recipient} "
            f"[{message.msg_type.value}] {message.msg_id}"
        )
        self._emit_event("message_sent", message)
        await self.notify_subscribers(message.recipient)
        return message.msg_id

    async def broadcast(self, message: Message) -> str:
        """广播消息到所有已知接收者"""
        message.msg_type = MessageType.BROADCAST
        async with self.lock:
            # 发送给所有有队列的接收者
            known_recipients = list(self._queues.keys())
            for recipient in known_recipients:
                if recipient != message.sender:  # 不发给自己
                    msg_copy = Message(
                        msg_type=MessageType.BROADCAST,
                        sender=message.sender,
                        recipient=recipient,
                        content=message.content,
                        correlation_id=message.correlation_id,
                        metadata=message.metadata,
                    )
                    self._queues[recipient].append(msg_copy)

            self._broadcast_history.append(message)
            self._stats["broadcasts"] += 1
            self._stats["sent"] += len(known_recipients)

        logger.info(f"[消息总线] 广播: {message.sender} → {len(known_recipients)} 个接收者")
        self._emit_event("broadcast", message, {"recipient_count": len(known_recipients)})
        return message.msg_id

    async def request(self, message: Message, timeout: float = 30) -> Optional[Message]:
        """发送请求并等待响应 (RPC 风格)

        Args:
            message: 请求消息
            timeout: 超时秒数

        Returns:
            响应消息，超时返回 None
        """
        correlation_id = message.correlation_id or f"corr_{uuid.uuid4().hex[:8]}"
        message.correlation_id = correlation_id

        # 创建等待 Future
        future = asyncio.get_running_loop().create_future()
        async with self.lock:
            self._pending_responses[correlation_id] = future

        # 发送请求
        await self.send(message)

        try:
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"[消息总线] 请求超时: {correlation_id} ({timeout}s)")
            async with self.lock:
                self._pending_responses.pop(correlation_id, None)
            return None

    async def send_response(self, request_msg: Message, content: Any) -> str:
        """发送响应消息"""
        response = Message(
            msg_type=MessageType.RESPONSE,
            sender=request_msg.recipient,
            recipient=request_msg.sender,
            correlation_id=request_msg.correlation_id,
            content=content,
        )

        # 检查是否有等待的 Future
        async with self.lock:
            future = self._pending_responses.pop(request_msg.correlation_id, None)

        if future and not future.done():
            future.set_result(response)
            logger.debug(f"[消息总线] 响应匹配: {request_msg.correlation_id}")
        else:
            # 无等待者，作为普通消息发送
            await self.send(response)

        self._emit_event("response_sent", response)
        return response.msg_id

    # ------------------------------------------------------------------
    # 接收
    # ------------------------------------------------------------------

    async def receive(self, recipient_id: str, limit: int = 10) -> List[Message]:
        """接收消息（推荐用于 async 上下文）"""
        async with self.lock:
            queue = self._queues.get(recipient_id, deque())
            messages = []
            for _ in range(min(limit, len(queue))):
                msg = queue.popleft()
                if not msg.expired:
                    messages.append(msg)
                else:
                    self._stats["expired"] += 1
            self._stats["received"] += len(messages)
            return messages

    async def receive_one(self, recipient_id: str) -> Optional[Message]:
        """接收一条消息"""
        messages = await self.receive(recipient_id, limit=1)
        return messages[0] if messages else None

    async def peek(self, recipient_id: str, limit: int = 50) -> List[Message]:
        """查看消息但不消费（非破坏性读取）"""
        async with self.lock:
            queue = self._queues.get(recipient_id, deque())
            messages = []
            for msg in list(queue)[:limit]:
                if not msg.expired:
                    messages.append(msg)
            return messages

    async def peek_all(self) -> Dict[str, List[Message]]:
        """查看所有队列的消息（非破坏性）"""
        async with self.lock:
            result = {}
            for rid, queue in self._queues.items():
                msgs = [msg for msg in list(queue) if not msg.expired]
                if msgs:
                    result[rid] = msgs
            return result

    async def list_recipients(self) -> List[str]:
        """列出所有已知的接收者ID"""
        async with self.lock:
            return list(self._queues.keys())

    # ------------------------------------------------------------------
    # 订阅
    # ------------------------------------------------------------------

    async def subscribe(self, recipient_id: str, callback: Callable[[Message], None]) -> None:
        """订阅消息 — 当有新消息时回调"""
        async with self.lock:
            self._subscriptions[recipient_id].append(callback)
        logger.debug(f"[消息总线] 订阅: {recipient_id} (回调数: {len(self._subscriptions[recipient_id])})")

    async def unsubscribe(self, recipient_id: str, callback: Callable = None) -> None:
        """取消订阅"""
        async with self.lock:
            if callback is None:
                self._subscriptions.pop(recipient_id, None)
            elif recipient_id in self._subscriptions:
                self._subscriptions[recipient_id] = [
                    cb for cb in self._subscriptions[recipient_id] if cb is not callback
                ]

    async def notify_subscribers(self, recipient_id: str) -> None:
        """通知订阅者有新消息"""
        async with self.lock:
            callbacks = self._subscriptions.get(recipient_id, [])
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(None)
                else:
                    cb(None)
            except Exception as e:
                logger.debug(f"[消息总线] 订阅回调异常: {e}")

    # ------------------------------------------------------------------
    # 维护
    # ------------------------------------------------------------------

    async def cleanup(self) -> int:
        """清理过期消息和悬挂的 Future"""
        removed = 0
        async with self.lock:
            for queue in self._queues.values():
                while queue and queue[0].expired:
                    queue.popleft()
                    removed += 1
                    self._stats["expired"] += 1

            # 清理已完成或已取消的 Future
            stale_futures = [
                cid for cid, f in self._pending_responses.items()
                if f.done()
            ]
            for cid in stale_futures:
                self._pending_responses.pop(cid, None)
                removed += 1

        if removed:
            logger.debug(f"[消息总线] 清理 {removed} 条过期消息/悬挂 Future")
        return removed

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    async def get_queue_size(self, recipient_id: str = None) -> int:
        if recipient_id:
            async with self.lock:
                return len(self._queues.get(recipient_id, deque()))
        async with self.lock:
            return sum(len(q) for q in self._queues.values())

    async def get_stats(self) -> dict:
        async with self.lock:
            stats = dict(self._stats)
        stats["queue_count"] = len(self._queues)
        stats["pending_responses"] = len(self._pending_responses)
        stats["subscription_count"] = sum(len(v) for v in self._subscriptions.values())
        stats["total_queued"] = await self.get_queue_size()
        return stats

    async def get_status(self) -> dict:
        return await self.get_stats()


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

_message_bus = None
_message_bus_lock = threading.Lock()


def get_message_bus() -> ModelMessageBus:
    """获取全局消息总线单例"""
    global _message_bus
    if _message_bus is None:
        with _message_bus_lock:
            if _message_bus is None:
                _message_bus = ModelMessageBus()
    return _message_bus
