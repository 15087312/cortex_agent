"""
感知集成器 - 将感知系统集成到主流程

提供：
1. 自动启动/停止感知监控
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

    将感知系统无缝集成到AI对话流程
    """

    def __init__(self):
        self._auto_monitoring = True
        self._context_injection_enabled = True
        self._attention_items: List[Dict[str, Any]] = []
        self._max_attention = 20
        logger.info("感知集成器初始化完成")

    def start(self) -> None:
        """启动感知监控"""
        if self._auto_monitoring:
            from modules.perception import get_perception_system
            ps = get_perception_system()
            if not ps._started:
                ps.setup()
                ps.start()
            logger.info("感知监控已启动")

    def stop(self) -> None:
        """停止感知监控"""
        from modules.perception import get_perception_system
        ps = get_perception_system()
        ps.stop()
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
        prompts = [item["prompt"] for item in self._attention_items[-5:]]
        return "【感知变化】\n" + "\n".join(prompts)

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
        """获取感知上下文摘要"""
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
