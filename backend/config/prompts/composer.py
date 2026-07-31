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
        self._base_rules = ""
        self._identity = ""
        self._load_prompts()

    def _load_prompts(self):
        try:
            base_path = PROMPTS_DIR / "base.yaml"
            with open(base_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            self._base_rules = data.get("base_rules", "")
            self._identity = data.get("identity", "")
        except Exception as e:
            logger.warning(f"Failed to load prompts: {e}, using defaults")
            self._base_rules = "You are a helpful AI assistant."
            self._identity = ""

    def build_system(self, memory_context: str = "") -> str:
        """Assemble full system prompt.

        Structure:
        1. Base rules
        2. Identity section
        3. Memory context (injected if available)
        """
        parts = []

        # Base rules with name substitution
        rules = self._base_rules.format(
            assistant_name=settings.ASSISTANT_NAME,
            user_name=settings.USER_NAME,
        )
        if rules.strip():
            parts.append(rules.strip())

        # Identity with name substitution
        identity = self._identity.format(
            assistant_name=settings.ASSISTANT_NAME,
            user_name=settings.USER_NAME,
        )
        if identity.strip():
            parts.append(identity.strip())

        # Memory context
        if memory_context and memory_context.strip():
            parts.append(memory_context.strip())

        return "\n\n".join(parts)
