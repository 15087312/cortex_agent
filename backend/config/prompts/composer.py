"""
Prompt assembly engine — builds system prompts from base rules + identity + memory context.
"""
from pathlib import Path
import yaml

from backend.config.settings import settings
from backend.utils.logger import setup_logger

logger = setup_logger("prompt_composer")

PROMPTS_DIR = Path(__file__).parent


class PromptComposer:
    """Builds system prompts from base rules + identity + memory context."""

    def __init__(self):
        self._identity = ""
        self._load_prompts()

    def _load_prompts(self):
        try:
            base_path = PROMPTS_DIR / "base.yaml"
            with open(base_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._identity = data.get("identity", "")
        except Exception as e:
            logger.warning(f"Failed to load prompts: {e}, using defaults")
            self._identity = ""

    def build_system(self, memory_context: str = "") -> str:
        """Assemble full system prompt.

        Structure（纯对话模式）:
        1. Advanced override (设置页高级修改，完全控制系统提示词)
        2. Identity section (支持设置页自定义人设)
        3. 感知说明（主 base.yaml 的 perception，纯对话只保留这一项规则）
        4. Memory context (injected if available)
        """
        # 与 agent 模式共用主 settings：设置页的人设管理 / 高级修改对纯对话模式同样生效
        persona_override = ""
        custom_persona = ""
        try:
            from config.settings import settings as main_settings
            persona_override = main_settings.get_system_override("orchestrator")
            custom_persona = main_settings.get_persona("orchestrator")
        except Exception:
            pass
        if persona_override:
            return persona_override

        parts = []

        # Identity with name substitution（自定义人设优先）
        identity_src = custom_persona or self._identity
        try:
            identity = identity_src.format(
                assistant_name=settings.ASSISTANT_NAME,
                user_name=settings.USER_NAME,
            )
        except Exception:
            # 自定义人设可能含 { 花括号（JSON/代码/角色扮演设定等），不能 format，原样使用
            identity = identity_src
        if identity.strip():
            parts.append(identity.strip())

        # 纯对话只保留：感知说明（主 base.yaml 的 perception）
        perception = self._build_perception_section()
        if perception:
            parts.append(perception)

        # Memory context
        if memory_context and memory_context.strip():
            parts.append(memory_context.strip())

        return "\n\n".join(parts)

    def _build_perception_section(self) -> str:
        """感知说明（来自主 config/prompts/base.yaml 的 perception section）"""
        import yaml
        try:
            # composer.py 位于 backend/config/prompts/，parents[3] 为项目根
            main_base = Path(__file__).resolve().parents[3] / "config" / "prompts" / "base.yaml"
            data = yaml.safe_load(main_base.read_text(encoding="utf-8"))
            perception = data.get("perception", [])
            if perception:
                return "【被动感知系统】\n" + "\n".join(perception)
        except Exception as e:
            logger.warning(f"加载感知说明失败: {e}")
        return ""
