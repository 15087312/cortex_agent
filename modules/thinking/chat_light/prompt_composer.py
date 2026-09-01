"""
Prompt assembly engine — builds system prompts from base rules + identity + memory context.
"""
from pathlib import Path
import yaml

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("prompt_composer")

PROMPTS_DIR = Path(__file__).parent


class PromptComposer:
    """Builds system prompts from base rules + identity + memory context."""

    def __init__(self):
        self._identity = ""
        self._base_mtime = None
        self._load_prompts()

    def _load_prompts(self):
        try:
            base_path = PROMPTS_DIR / "base.yaml"
            mtime = base_path.stat().st_mtime
            with open(base_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._identity = data.get("identity", "")
            self._base_mtime = mtime
        except Exception as e:
            logger.warning(f"Failed to load prompts: {e}, using defaults")
            self._identity = ""

    def _ensure_fresh_identity(self):
        """检测 base.yaml 是否被修改（mtime 变化），变化则重读——改提示词实时生效，无需重启"""
        try:
            base_path = PROMPTS_DIR / "base.yaml"
            mtime = base_path.stat().st_mtime
            if self._base_mtime != mtime:
                with open(base_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                self._identity = data.get("identity", "")
                self._base_mtime = mtime
                logger.info("[PromptComposer] 检测到 base.yaml 变更，已热重载 identity")
        except Exception:
            pass

    def build_system(self, memory_context: str = "") -> str:
        """Assemble full system prompt.

        Structure（纯对话模式）:
        1. Advanced override (设置页高级修改，完全控制系统提示词)
        2. Identity section (支持设置页自定义人设)
        3. 感知说明（主 base.yaml 的 perception，纯对话只保留这一项规则）
        4. Memory context (injected if available)
        """
        # 提示词热重载：base.yaml 被修改时自动重读（实时生效，无需重启）
        self._ensure_fresh_identity()

        # 与 agent 模式共用主 settings：设置页的人设管理 / 高级修改对纯对话模式同样生效
        persona_override = ""
        custom_persona = ""
        try:
            from config.settings import settings as main_settings
            persona_override = main_settings.get_system_override("orchestrator")
            # 尊重编排的 active 状态：只从"激活的" agent 中选取纯对话人设，
            # 停用的 agent 不应被强制套用（用户编排里设置了哪个启动，就用哪个）。
            # 优先级：orchestrator（总指挥）> 激活的自定义 large agent > 内置 base.yaml
            if main_settings.get_agent_active("orchestrator"):
                custom_persona = main_settings.get_persona("orchestrator") or ""
            # 激活的自定义 large agent 若设置了完整 system_override（高级修改），
            # 与 orchestrator 的 override 同等对待：未命中 orchestrator override 时采用它。
            # 修复：此前只读 get_persona，导致自定义总指挥的 system_override 永远不进纯对话 system。
            if not persona_override:
                for ca in main_settings.get_custom_agents():
                    if (ca.get("tier") == "large" and ca.get("role")
                            and main_settings.get_agent_active(ca.get("role"))):
                        so = main_settings.get_system_override(ca["role"])
                        if so:
                            persona_override = so
                        if not custom_persona:
                            p = main_settings.get_persona(ca["role"])
                            if p:
                                custom_persona = p
                        break
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
