"""
感知集成器 - 将感知系统集成到主流程

提供：
1. 订阅感知事件，自动注入到模型上下文
2. 感知上下文注入到对话
3. 对话流程集成
"""
import threading
from typing import List, Dict, Any, Optional
from utils.logger import setup_logger

logger = setup_logger("perception_integration")


class PerceptionIntegrator:
    """
    感知集成器

    将感知系统无缝集成到AI对话流程。
    订阅感知事件总线，自动将环境变化（屏幕/OCR/文件等）注入模型上下文。
    """

    def __init__(self):
        self._auto_monitoring = True
        self._context_injection_enabled = True
        self._attention_items: List[Dict[str, Any]] = []
        self._max_attention = 20
        self._sub_id: str = ""
        logger.info("感知集成器初始化完成")

    def start(self) -> None:
        """启动感知监控并订阅事件"""
        if self._auto_monitoring:
            from modules.perception import get_perception_system
            ps = get_perception_system()
            if not ps._started:
                ps.setup()
                ps.start()
            # 订阅感知事件，自动填充注意力池
            self._subscribe_events()
            logger.info("感知监控已启动，已订阅感知事件")

    def _subscribe_events(self) -> None:
        """订阅感知事件总线"""
        try:
            from modules.perception.events.bus import get_event_bus
            from modules.perception.events.types import PerceptionEventType

            event_bus = get_event_bus()

            # 订阅所有感知事件类型（屏幕、OCR、文件、差异等）
            for event_type in [
                PerceptionEventType.SCREEN_DIFF,
                PerceptionEventType.SCREEN_OCR,
                PerceptionEventType.FILE_CHANGE,
                PerceptionEventType.DIALOG_CHANGE,
                PerceptionEventType.DIFFERENCE_DETECTED,
            ]:
                try:
                    event_bus.subscribe(event_type, self._on_perception_event)
                except Exception:
                    pass
            logger.info("已订阅感知事件")
        except Exception as e:
            logger.debug(f"订阅感知事件失败 (非致命): {e}")

    def _on_perception_event(self, event) -> None:
        """感知事件回调 — 添加到注意力池"""
        try:
            payload = event.payload if hasattr(event, 'payload') else {}
            source = payload.get('source_type', payload.get('type', 'unknown'))
            category = payload.get('category', '')
            description = payload.get('description', payload.get('text', ''))
            intensity = payload.get('intensity', 0)

            if isinstance(description, str) and description:
                # 去重：相同描述的最近事件不再重复添加
                for item in self._attention_items[-3:]:
                    existing = item.get("description", "")
                    if existing == description[:100]:
                        return

                self._attention_items.append({
                    "source": source,
                    "category": category,
                    "description": description[:200],
                    "intensity": intensity,
                    "prompt": f"[{source}] {description[:200]}",
                })
                if len(self._attention_items) > self._max_attention:
                    self._attention_items = self._attention_items[-self._max_attention:]
        except Exception as e:
            logger.debug(f"处理感知事件异常 (非致命): {e}")

    def stop(self) -> None:
        """停止感知监控"""
        from modules.perception import get_perception_system
        ps = get_perception_system()
        ps.stop()
        if self._sub_id:
            try:
                from modules.perception.events.bus import get_event_bus
                get_event_bus().unsubscribe(self._sub_id)
            except Exception:
                pass
            self._sub_id = ""
        logger.info("感知监控已停止")

    def update_dialog(self, messages: List[Dict]) -> None:
        """更新对话上下文（供感知系统追踪）"""
        from modules.perception import get_perception_system
        ps = get_perception_system()
        if ps.dialog_perception:
            ps.dialog_perception.update_snapshot(messages)

    def add_dialog_change(self, role: str, content: str) -> None:
        """添加对话变化到注意力池"""
        from modules.perception.change_event import ChangeEvent
        event = ChangeEvent(
            change_type="created",
            target_type="dialog",
            target=f"[{role}] {content[:100]}",
            details={"role": role}
        )
        self._add_to_attention(event, urgency=0.6)

    def _add_to_attention(self, change, urgency: float = 0.5) -> None:
        """添加到注意力池"""
        self._attention_items.append({
            "change": change,
            "urgency": urgency,
            "prompt": change.to_prompt(),
        })
        if len(self._attention_items) > self._max_attention:
            self._attention_items = self._attention_items[-self._max_attention:]

    def get_attention_prompt(self) -> str:
        """获取注意力提示"""
        if not self._attention_items:
            return ""
        items = self._attention_items[-5:]  # 最近5条
        prompts = []
        for item in items:
            if "prompt" in item:
                prompts.append(item["prompt"])
            elif "description" in item:
                prompts.append(f"[{item.get('source', '感知')}] {item['description']}")
        if not prompts:
            return ""
        return "【环境感知】\n" + "\n".join(prompts)

    def build_system_prompt(self, base_prompt: str) -> str:
        """构建系统提示词（注入感知信息）"""
        if not self._context_injection_enabled:
            return base_prompt
        attention_prompt = self.get_attention_prompt()
        if attention_prompt:
            return f"{base_prompt}\n\n{attention_prompt}"
        return base_prompt

    def build_messages(self, messages: List[Dict], system_prompt: str = None) -> List[Dict]:
        """构建完整的消息列表（包含感知上下文）"""
        if system_prompt:
            system_prompt = self.build_system_prompt(system_prompt)
        full_messages = []
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        full_messages.extend(messages)
        return full_messages

    def get_context_summary(self) -> str:
        """获取感知上下文摘要（由编排层调用，注入到模型 prompt）"""
        attention_prompt = self.get_attention_prompt()
        if attention_prompt:
            return f"\n\n{attention_prompt}"
        return ""

    def check_rule_compliance(self, content: str) -> List:
        """检查输出是否符合规范"""
        try:
            from modules.perception.rule_compliance_perception import get_rule_compliance_perception
            detector = get_rule_compliance_perception()
            return detector.detect_violations(content)
        except Exception:
            return []


_perception_integrator_instance = None
_perception_integrator_lock = threading.Lock()


def get_perception_integrator() -> PerceptionIntegrator:
    """Get or create perception integrator instance (thread-safe)"""
    global _perception_integrator_instance
    if _perception_integrator_instance is None:
        with _perception_integrator_lock:
            if _perception_integrator_instance is None:
                _perception_integrator_instance = PerceptionIntegrator()
    return _perception_integrator_instance


# 向后兼容
perception_integrator = None
