"""
输出系统核心 - 统一流水线调度

集成：
- 安全系统 API：输出内容校验
- 基础设施层：鼠标键盘控制
- 样式适配：根据情绪和人格调整输出
"""
from typing import Dict, Optional, Generator, Tuple

from modules.security_system.interface import SecurityPort, get_security_port
from modules.security_system.validators import CoreValidator, ContentValidator, OutputValidator
from .distributor import OutputDistributor
from .input_controller import InputController
from utils.logger import setup_logger

logger = setup_logger("output_system")


class OutputSystem:
    def __init__(self, _memory_module=None, security: Optional[SecurityPort] = None):
        self.security_api = security or get_security_port()

        self.core_validator = CoreValidator()
        self.content_validator = ContentValidator()
        self.output_validator = OutputValidator()
        self.distributor = OutputDistributor()
        self.input_controller = InputController()
        
        self._content_enabled = True
        self._output_fmt_enabled = True
        self.interrupt_flag = False
        
        logger.info("输出系统初始化完成")

    def _enable_content_check(self, enabled: bool = True) -> None:
        """Internal: bypass content validation (security-sensitive, use with caution)"""
        if not enabled:
            logger.warning("[安全警告] 内容检查已禁用，将跳过内容验证")
        self._content_enabled = enabled

    def _enable_format_check(self, enabled: bool = True) -> None:
        """Internal: bypass format validation"""
        if not enabled:
            logger.warning("[安全警告] 格式检查已禁用，将跳过格式验证")
        self._output_fmt_enabled = enabled

    # 公开 API（供管理接口/测试调用）
    def enable_content_check(self, enabled: bool = True) -> None:
        """公开接口：启用/禁用内容检查"""
        self._enable_content_check(enabled)

    def enable_format_check(self, enabled: bool = True) -> None:
        """公开接口：启用/禁用格式检查"""
        self._enable_format_check(enabled)

    def set_interrupt(self) -> None:
        self.interrupt_flag = True
        logger.info("输出中断指令已接收")

    def reset_interrupt(self) -> None:
        self.interrupt_flag = False

    @staticmethod
    def clean_response(text: str) -> str:
        """清洗模型输出：去除记事本标记、格式化冗余等。"""
        if not text:
            return text
        import re
        text = re.sub(r'【更新记事本】.*', '', text, flags=re.DOTALL)
        lines = text.split('\n')
        in_notebook = False
        result = []
        for line in lines:
            s = line.strip()
            if s.startswith('- 任务状态:') or s.startswith('- 当前进度:'):
                in_notebook = True
                continue
            if in_notebook and s.startswith('- '):
                continue
            if in_notebook and s and not s.startswith('- '):
                in_notebook = False
            result.append(line)
        text = '\n'.join(result)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def validate(self, content: str, output_type: str = "text") -> Tuple[bool, str]:
        passed, result = self.core_validator.validate_all(content)
        if not passed:
            return False, result

        if self._content_enabled:
            passed, result = self.content_validator.validate(content)
            if not passed:
                return False, result

        if self._output_fmt_enabled:
            passed, result = self.output_validator.validate(content)
            if not passed:
                return False, result

        return True, content

    def process(
        self,
        raw_content: str,
        input_context: Dict,
        output_type: str = "text",
        channel: str = "console",
        stream: bool = False,
    ) -> Optional[Generator[str, None, None]]:
        self.reset_interrupt()

        raw_content = self.clean_response(raw_content)
        passed, validated = self.validate(raw_content, output_type)
        if not passed:
            logger.warning(f"[安全拦截] {validated}")
            return None

        if self.interrupt_flag:
            logger.info("[输出中断]")
            return None

        if stream:
            return self.distributor.stream_output(validated, channel)
        else:
            self.distributor.distribute(validated, channel)
            return None

    def output_text(
        self,
        text: str,
        user_input: str = "",
        stream: bool = False,
    ) -> Optional[Generator]:
        input_context = {"user_input": user_input}
        return self.process(text, input_context, "text", "console", stream)

    def output_code(
        self,
        code: str,
        language: str = "",
        user_input: str = ""
    ) -> str:
        input_context = {"user_input": user_input}
        passed, validated = self.validate(code, "code")
        if not passed:
            logger.warning(f"[安全拦截] 代码验证失败: {validated}")
            return "[代码未通过安全验证]"
        self.distributor.distribute(validated, "console")
        return validated

    def output_system_msg(self, message: str) -> None:
        self.distributor.distribute(f"[系统] {message}", "console")

    # ========== 硬件控制方法 ==========

    def move_mouse(self, x: int, y: int, duration: float = 0.3) -> bool:
        return self.input_controller.move_to(x, y, duration)

    def click_mouse(self, x: int = None, y: int = None, button: str = "left", clicks: int = 1) -> bool:
        return self.input_controller.click(x, y, button, clicks)

    def type_text(self, text: str, interval: float = 0.05) -> bool:
        return self.input_controller.typewrite(text, interval)

    def press_key(self, key: str) -> bool:
        return self.input_controller.press(key)

    def hotkey(self, *keys) -> bool:
        return self.input_controller.hotkey(*keys)

    def scroll(self, clicks: int, x: int = None, y: int = None) -> bool:
        return self.input_controller.scroll(clicks, x, y)

    def screenshot(self, region: Tuple[int, int, int, int] = None) -> Optional[bytes]:
        return self.input_controller.screenshot(region)

    def get_mouse_position(self) -> Tuple[int, int]:
        return self.input_controller.get_current_position()

    def pause_input(self) -> None:
        self.input_controller.pause()

    def resume_input(self) -> None:
        self.input_controller.resume()
