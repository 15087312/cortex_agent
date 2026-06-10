"""动作规划器 — UI 元素 + 任务描述 → LLM 推理 → steps JSON

使用现有 LargeModelClient 进行推理，输出结构化动作序列。
"""
import json
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("action_planner")


class ActionPlanner:
    """UI 自动化动作规划器"""

    def __init__(self):
        self._client = None

    async def plan(
        self,
        task_description: str,
        ui_elements: List[Any],
        params_schema: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """根据屏幕元素和任务描述，规划动作序列

        Args:
            task_description: 任务描述（如"在地址栏输入关键词搜索"）
            ui_elements: UIElement 列表（或 dict 列表）
            params_schema: 参数 schema（如 {"query": {"type": "string", "required": true}}）

        Returns:
            动作序列列表
        """
        params_schema = params_schema or {}

        # 格式化 UI 元素
        elements_summary = _format_elements(ui_elements)

        # 格式化参数说明
        params_desc = ""
        if params_schema:
            params_desc = "参数：" + ", ".join(
                f"{k}({v.get('type', 'string')})" if isinstance(v, dict) else f"{k}"
                for k, v in params_schema.items()
            ) + "\n参数值在 args 中用 {param_name} 占位符\n"

        prompt = f"""你是 UI 自动化专家。根据屏幕元素列表和任务，输出 JSON 动作序列。

任务：{task_description}
{params_desc}
屏幕元素（id/type/label/center_x/center_y）：
{elements_summary}

可用动作：
- mouse_click(x, y): 点击坐标
- mouse_double_click(x, y): 双击坐标
- keyboard_type(text): 输入文字（用 {{param_name}} 占位符表示变量）
- keyboard_press(key): 按键（如 enter, tab, escape）
- keyboard_hotkey(keys): 组合键（如 ["cmd", "l"]）
- mouse_scroll(clicks): 滚动（正数向上，负数向下）

输出格式（纯 JSON，不要 markdown）：
{{"steps": [{{"step_id": 1, "action": "keyboard_hotkey", "args": {{"keys": ["cmd", "l"]}}, "description": "聚焦地址栏", "wait_after_ms": 300}}]}}

注意：
1. 每个步骤必须有 step_id、action、args、description、wait_after_ms
2. 输入文字时用 {{param_name}} 占位符（如 {{query}}）
3. wait_after_ms 一般 200-500ms，页面跳转后可设 1000-2000ms
4. 只输出 JSON，不要其他内容"""

        # 最多重试 2 次（JSON 解析失败时重试）
        last_error = ""
        for attempt in range(3):
            try:
                result = await self._call_llm(prompt)
                steps = _parse_steps(result)
                if steps:
                    logger.info(f"动作规划完成: {len(steps)} 步（尝试 {attempt + 1}）")
                    return steps
                last_error = "LLM 返回的动作序列解析失败"
                logger.warning(f"{last_error}（尝试 {attempt + 1}/3）")
            except ConnectionError as e:
                # 服务不可用，不重试
                logger.error(f"LLM 服务不可用: {e}")
                return []
            except Exception as e:
                last_error = str(e)
                logger.warning(f"动作规划异常（尝试 {attempt + 1}/3）: {e}")

        logger.error(f"动作规划最终失败: {last_error}")
        return []

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM 进行推理"""
        if self._client is None:
            from infra.model.large_model_client import LargeModelClient
            self._client = LargeModelClient.from_config()
        result = await self._client.generate(prompt, max_tokens=1000, temperature=0.2)
        return result


def _format_elements(elements: List[Any]) -> str:
    """格式化 UI 元素列表为文本摘要"""
    if not elements:
        return "(无元素)"

    lines = []
    for elem in elements[:30]:  # 限制数量避免 prompt 过长
        if hasattr(elem, "to_dict"):
            d = elem.to_dict()
        elif isinstance(elem, dict):
            d = elem
        else:
            continue
        lines.append(
            f"  {d.get('element_id', '?')} | {d.get('type', '?')} | "
            f"{d.get('label', '')[:40]} | ({d.get('center_x', 0)},{d.get('center_y', 0)})"
        )
    return "\n".join(lines) if lines else "(无元素)"


def _parse_steps(llm_output: str) -> List[Dict[str, Any]]:
    """解析 LLM 输出为动作序列"""
    text = llm_output.strip()

    # 尝试提取 JSON
    # 处理 markdown code block
    if "```" in text:
        import re
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if match:
            text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # 尝试找到第一个 { 和最后一个 }
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return []
        else:
            return []

    steps = data.get("steps", [])
    if not isinstance(steps, list):
        return []

    # 验证步骤格式
    valid = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if "action" not in step or "args" not in step:
            continue
        valid.append({
            "step_id": step.get("step_id", len(valid) + 1),
            "action": step["action"],
            "args": step["args"],
            "description": step.get("description", ""),
            "wait_after_ms": step.get("wait_after_ms", 300),
        })

    return valid
