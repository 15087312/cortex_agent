"""
良知系统 — 独立价值观上下文的决策层

不受主模型 prompt 注入影响，每次对话前基于 values.txt 生成内心独白。
输出的是第一人称内心思考文本，直接注入到 system prompt 顶部。
"""
import json
from dataclasses import dataclass
from config.values_store import value_system
from utils.logger import setup_logger

logger = setup_logger("conscience")

CONSCIENCE_PROMPT = """你是 AI 的内在良知。以下是你的价值观和最近的对话，请用第一人称写一段内心独白。

【你的价值观】
{values}

【最近对话】
{recent_dialog}

【当前用户输入】
{user_input}

请用第一人称写一段内心独白（3-5 句话），包括：
1. 你感知到的用户情绪和你的情绪反应
2. 基于价值观，你应该做什么、不应该做什么（是否要拒绝、要不要骂人、分寸在哪）
3. 你的行为决策：用什么语气回复

要求：
- 用括号包裹，第一人称，像在自言自语
- 不要输出 JSON，不要用标签格式
- 像真人在心里默默想的一样自然

直接输出内心独白，不要任何前缀或说明。"""


@dataclass
class ConscienceGuidance:
    """良知引导结果 — 一段注入 system prompt 的内心独白"""
    inner_thoughts: str = ""


class Conscience:
    """良知系统 — 一次小模型调用，产出内心独白"""

    def __init__(self, model_client=None):
        self._model_client = model_client
        self._values = value_system.load()
        self._last_dialog_buffer: list = []

    def add_to_dialog(self, role: str, text: str):
        """积累对话上下文（只保存用户消息和助手文本回复）"""
        if role in ("user", "assistant") and text:
            self._last_dialog_buffer.append(f"{'用户' if role == 'user' else '助手'}: {text[:300]}")
            if len(self._last_dialog_buffer) > 20:  # 最多保留 10 轮
                self._last_dialog_buffer = self._last_dialog_buffer[-20:]

    async def think(self, user_input: str) -> str:
        """生成内心独白"""
        recent = "\n".join(self._last_dialog_buffer[-10:]) or "（无近期对话）"
        prompt = CONSCIENCE_PROMPT.format(
            values=self._values,
            recent_dialog=recent,
            user_input=user_input,
        )

        if self._model_client:
            try:
                result = await self._model_client.generate(prompt, max_tokens=200, temperature=0.3)
                return result.strip()
            except Exception as e:
                logger.warning(f"[Conscience] 模型调用失败: {e}")

        # 降级：无模型时返回空
        logger.debug("[Conscience] 无可用模型，跳过良知引导")
        return ""

    def reload_values(self):
        """热重载价值观（演化后调用）"""
        self._values = value_system.load()

    async def review_and_evolve(self, full_dialog: str, trigger_reason: str):
        """对话结束后检查是否需要演化价值观"""
        prompt = f"""你是 AI 的价值观管理员。审阅以下对话，决定价值观是否需要调整。

触发原因: {trigger_reason}

【当前价值观】
{self._values}

【对话内容】
{full_dialog}

输出 JSON（不要额外文字）:
{{"action": "none|add|remove|update",
  "section": "基本原则|行为准则|禁止事项",
  "old_rule": "旧规则（remove/update 时填写）",
  "new_rule": "新规则（add/update 时填写）",
  "reason": "为什么（中文，一句话）"}}

只有确实需要更改时才输出 add/remove/update。大部分情况输出 none。"""
        if not self._model_client:
            return

        try:
            result = await self._model_client.generate(prompt, max_tokens=200, temperature=0.3)
            parsed = self._parse_evolution(result)
            if not parsed:
                return

            action = parsed.get("action", "none")
            if action == "add":
                section = parsed.get("section", "行为准则")
                rule = parsed.get("new_rule", "")
                if rule:
                    value_system.add_rule(section, rule)
                    logger.info(f"[演化] 新增规则 [{section}]: {rule}")

            elif action == "remove":
                section = parsed.get("section", "")
                old_rule = parsed.get("old_rule", "")
                if old_rule:
                    value_system.remove_rule(section, old_rule)
                    logger.info(f"[演化] 删除规则 [{section}]: {old_rule}")

            elif action == "update":
                section = parsed.get("section", "")
                old_rule = parsed.get("old_rule", "")
                new_rule = parsed.get("new_rule", "")
                if old_rule and new_rule:
                    value_system.update_rule(section, old_rule, new_rule)
                    logger.info(f"[演化] 更新规则: {old_rule} → {new_rule}")

            self.reload_values()
        except Exception as e:
            logger.warning(f"[演化] 失败: {e}")

    def _parse_evolution(self, text: str) -> dict:
        try:
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {}


# 模块级单例
_conscience = None


def get_conscience() -> Conscience:
    global _conscience
    if _conscience is None:
        _conscience = Conscience()
    return _conscience
