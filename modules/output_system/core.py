"""
输出系统核心 - 统一流水线调度

集成：
- 安全系统 API：输出内容校验
- 基础设施层：鼠标键盘控制
- 样式适配：根据情绪和人格调整输出
"""
from typing import Dict, Generator, Optional, Tuple

from infra.ports import resolve
from .distributor import OutputDistributor
from .input_controller import InputController
from utils.logger import setup_logger

logger = setup_logger("output_system")


class OutputSystem:
    def __init__(self, _memory_module=None):
        # 安全校验器不再直接实例化（那会让 output_system 反向 import security_system）。
        # 改为延迟到真正校验时向端口注册表取；构造期不依赖安全系统。
        self._validators = None
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
        """清洗模型输出：去除记事本标记、格式化冗余等。

        实现已下沉 infra.text_cleanup（纯函数），此处仅为兼容既有调用方保留转发。
        思考侧不再需要 import output_system 就能做同一套清洗。
        """
        from infra.text_cleanup import clean_response as _clean
        return _clean(text)

    @property
    def _sec(self):
        """安全校验器（延迟解析端口）。

        只有真正要校验输出时才需要 security_system 已加载；构造 OutputSystem
        本身不产生依赖。缺失时显式报错，而不是静默放行。
        """
        bundle = getattr(self, "_validators", None)
        if bundle is not None:
            return bundle
        # 兼容显式注入（测试或自定义装配）：对象上有三个校验器就直接用
        explicit = getattr(self, "core_validator", None)
        if explicit is not None:
            bundle = (explicit, self.content_validator, self.output_validator)
            self._validators = bundle
            return bundle
        bundle = resolve("security.validators")
        if bundle is None:
            raise RuntimeError(
                "安全校验器未注册：security_system 未加载，无法校验输出"
            )
        self._validators = bundle
        return self._validators

    def validate(self, content: str, output_type: str = "text") -> Tuple[bool, str]:
        core_validator, content_validator, output_validator = self._sec
        passed, result = core_validator.validate_all(content)
        if not passed:
            return False, result

        if self._content_enabled:
            passed, result = content_validator.validate(content)
            if not passed:
                return False, result

        if self._output_fmt_enabled:
            passed, result = output_validator.validate(content)
            if not passed:
                return False, result

        return True, content

    # @link:block OutputPipe
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
        # @link:block OutPipe_Guard = Guard
        passed, validated = self.validate(raw_content, output_type)
        if not passed:
            logger.warning(f"[安全拦截] {validated}")
            return None

        if self.interrupt_flag:
            logger.info("[输出中断]")
            return None

        # @link:block OutPipe_Emit = Emit
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
