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
        
        from modules.perception import perception_manager
        self.perception = perception_manager
        
        self._auto_monitoring = True
        self._context_injection_enabled = True
        
        logger.info("感知集成器初始化完成")
    
    def start(self) -> None:
        """启动感知监控"""
        if self._auto_monitoring:
            self.perception.start_monitoring()
            logger.info("感知监控已启动")
    
    def stop(self) -> None:
        """停止感知监控"""
        self.perception.stop_monitoring()
        logger.info("感知监控已停止")
    
    def update_dialog(self, messages: List[Dict]) -> None:
        """更新对话上下文（供感知系统追踪）"""
        self.perception.dialog_perception.update_snapshot(messages)
    
    def add_dialog_change(self, role: str, content: str) -> None:
        """添加对话变化到注意力池"""
        from modules.perception import ChangeEvent
        event = ChangeEvent(
            change_type="created",
            target_type="dialog",
            target=f"[{role}] {content[:100]}",
            details={"role": role}
        )
        self.perception.add_to_attention(event, urgency=0.6)
    
    def build_system_prompt(self, base_prompt: str) -> str:
        """
        构建系统提示词（注入感知信息）
        
        用法：
            system_prompt = integrator.build_system_prompt(base_prompt)
        """
        if not self._context_injection_enabled:
            return base_prompt
        
        attention_prompt = self.perception.get_attention_prompt()
        
        if attention_prompt:
            return f"{base_prompt}\n\n{attention_prompt}"
        
        return base_prompt
    
    def build_messages(self, messages: List[Dict], system_prompt: str = None) -> List[Dict]:
        """
        构建完整的消息列表（包含感知上下文）
        
        用法：
            full_messages = integrator.build_messages(messages, system_prompt)
        """
        if system_prompt:
            system_prompt = self.build_system_prompt(system_prompt)
        
        full_messages = []
        
        if system_prompt:
            full_messages.append({"role": "system", "content": system_prompt})
        
        full_messages.extend(messages)
        
        return full_messages
    
    def get_context_summary(self) -> str:
        """获取感知上下文摘要"""
        attention_prompt = self.perception.get_attention_prompt()
        if attention_prompt:
            return f"\n\n{attention_prompt}"
        return ""
    
    def on_message_received(self, role: str, content: str) -> None:
        """消息接收回调（自动添加到注意力池）"""
        self.add_dialog_change(role, content)
    
    def get_full_context(self) -> Dict[str, Any]:
        """获取完整感知上下文"""
        return self.perception.get_full_context()

    def clear(self) -> None:
        """清空感知历史"""
        self.perception.clear_attention_pool()

    def enable_context_injection(self) -> None:
        """启用上下文注入"""
        self._context_injection_enabled = True

    def disable_context_injection(self) -> None:
        """禁用上下文注入"""
        self._context_injection_enabled = False

    def check_output_compliance(self, content: str) -> None:
        """检测输出中的规范违反

        在大模型生成输出后调用此方法，自动检测规范违反并添加到注意力池。

        Args:
            content: 要检查的输出内容
        """
        try:
            from modules.perception.rule_compliance_perception import (
                get_rule_compliance_perception,
            )

            compliance_checker = get_rule_compliance_perception()
            violations = compliance_checker.detect_violations(content)

            if violations:
                logger.info(f"[规范检测] 检测到 {len(violations)} 个违反")

                # 将违反事件添加到注意力池
                for violation in violations:
                    event = violation.to_perception_event()
                    urgency = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(
                        violation.severity, 0.5
                    )
                    self.perception.add_to_attention(event, urgency=urgency)
                    logger.debug(
                        f"  - {violation.rule_category}: {violation.violation_details}"
                    )
        except Exception as e:
            logger.debug(f"规范检测失败（非致命）: {e}")


# CONC-7: Use lazy factory instead of module-level singleton
# Avoid initializing hardware at import time (breaks CI/headless environments)
_perception_integrator_instance = None
_perception_integrator_lock = threading.Lock()

def get_perception_integrator() -> PerceptionIntegrator:
    """Get or create perception integrator instance (lazy factory, thread-safe)"""
    global _perception_integrator_instance
    if _perception_integrator_instance is None:
        with _perception_integrator_lock:
            if _perception_integrator_instance is None:
                _perception_integrator_instance = PerceptionIntegrator()
    return _perception_integrator_instance

# Backwards compatibility: module-level access via property
class _PerceptionIntegratorProxy:
    def __getattr__(self, name):
        return getattr(get_perception_integrator(), name)

perception_integrator = _PerceptionIntegratorProxy()
