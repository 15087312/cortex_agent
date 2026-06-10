"""Skill 生成器 — 为每个 App 的已学工具集维护 Skill YAML

生成的 Skill 文件存放在 skills/learned/ 目录下：
  skills/learned/<app_name>_skill.yaml

绑定规则：
- open_app(app_name) 后自动加载对应 skill
- close_app(app_name) 后 skill 上下文失效
- 每个 app 一个 skill 文件，单会话上下文
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from utils.logger import setup_logger
from .recipe_engine import RecipeEngine, _sanitize_name

logger = setup_logger("skill_generator")

_SKILLS_LEARNED_DIR = None


def _get_skills_learned_dir() -> Path:
    global _SKILLS_LEARNED_DIR
    if _SKILLS_LEARNED_DIR is None:
        project_root = Path(__file__).parent.parent.parent
        _SKILLS_LEARNED_DIR = project_root / "skills" / "learned"
    return _SKILLS_LEARNED_DIR


class SkillGenerator:
    """已学工具 Skill 生成器"""

    @staticmethod
    def generate_or_update(app_name: str) -> Optional[Path]:
        """创建或更新指定 app 的 Skill YAML

        扫描 data/plugins/learned_tools/<app_name>/ 下所有已学工具，
        生成包含工具列表和使用规则的 Skill 文件。

        Returns:
            生成的 Skill 文件路径，无工具时返回 None
        """
        from infra.tool_manager.tool_registry import ToolRegistry

        tools = RecipeEngine.list_all()
        app_tools = [t for t in tools if t["app_name"] == app_name]

        if not app_tools:
            logger.info(f"应用 {app_name} 无已学工具，跳过 Skill 生成")
            return None

        skills_dir = _get_skills_learned_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)

        skill_path = skills_dir / f"{_sanitize_name(app_name)}_skill.yaml"

        # 构建工具摘要（供 AI 决策参考）
        tools_summary = []
        for tool in app_tools:
            tools_summary.append({
                "name": tool["tool_name"],
                "description": tool.get("task_description", ""),
                "params": tool.get("params", []),
                "stats": {
                    "runs": tool["stats"].get("total_runs", 0),
                    "success_rate": _success_rate(tool["stats"]),
                },
            })

        skill_yaml = _build_skill_yaml(app_name, tools_summary)
        skill_path.write_text(skill_yaml, encoding="utf-8")

        logger.info(f"Skill 已生成: {skill_path} ({len(app_tools)} 个工具)")
        return skill_path

    @staticmethod
    def remove_tool(app_name: str, tool_name: str) -> Optional[Path]:
        """删除工具时同步更新 skill 文件"""
        # 先删除工具
        RecipeEngine.delete(tool_name, app_name)

        # 检查该 app 是否还有其他工具
        tools = RecipeEngine.list_all()
        app_tools = [t for t in tools if t["app_name"] == app_name]

        if not app_tools:
            # 无工具了，删除 skill 文件
            skill_path = _get_skills_learned_dir() / f"{_sanitize_name(app_name)}_skill.yaml"
            if skill_path.exists():
                skill_path.unlink()
                logger.info(f"Skill 已删除（无剩余工具）: {skill_path}")
                return None
            return None

        # 还有工具，重新生成
        return SkillGenerator.generate_or_update(app_name)

    @staticmethod
    def load_for_app(app_name: str) -> bool:
        """尝试加载指定 app 的 skill（供 open_app 调用）

        Returns:
            是否成功加载
        """
        skill_path = _get_skills_learned_dir() / f"{_sanitize_name(app_name)}_skill.yaml"
        if not skill_path.exists():
            return False

        try:
            from modules.thinking.skills.manager import skill_manager
            # load_skills() 已包含 skills/learned/ 扫描
            skill_manager.load_skills()
            logger.info(f"已加载 {app_name} 的 learned skill")
            return True
        except Exception as e:
            logger.debug(f"加载 learned skill 失败: {e}")
            return False


def _build_skill_yaml(app_name: str, tools_summary: List[Dict[str, Any]]) -> str:
    """构建 Skill YAML 内容"""
    tool_names = [t["name"] for t in tools_summary]
    keywords = [app_name.lower(), app_name]
    for t in tools_summary:
        keywords.append(t["name"])

    skill_data = {
        "id": f"{_sanitize_name(app_name)}_automation",
        "name": f"{app_name} 自动化",
        "description": f"{app_name} 应用的 AI 已学工具集，包含 {len(tools_summary)} 个自动化操作",
        "keywords": keywords,

        "role": f"{app_name} 操作专家",
        "personality": (
            f"你是 {app_name} 的自动化操作专家。你已经学会了以下操作：\n"
            + "\n".join(f"- {t['name']}: {t['description']}" for t in tools_summary)
        ),
        "speaking_style": "直接执行操作，简洁说明结果",
        "expertise": tool_names,
        "weaknesses": [],

        "rules": [
            {
                "id": "use_learned_tools",
                "content": f"执行 {app_name} 操作时优先使用已学工具，不要重复感知",
                "severity": "must",
            },
            {
                "id": "tool_failure_relearn",
                "content": "工具执行失败时，调用 delete_learned_tool 删除后重新 learn_tool",
                "severity": "must",
            },
            {
                "id": "no_redundant_perception",
                "content": "有已学工具时不要主动调用 understand_screen，被动感知会自动监控",
                "severity": "should",
            },
        ],

        "workflow": [
            {
                "step": 1,
                "name": "意图识别",
                "description": f"识别用户在 {app_name} 中的操作意图",
                "output": "操作意图 + 目标工具名",
            },
            {
                "step": 2,
                "name": "参数提取",
                "description": "从用户输入中提取工具所需参数",
                "output": "参数字典",
            },
            {
                "step": 3,
                "name": "调用工具",
                "description": "直接调用已学工具执行操作",
                "output": "执行结果",
            },
            {
                "step": 4,
                "name": "失败处理",
                "description": "工具失败时删除并重新学习",
                "output": "重新学习的工具",
            },
        ],

        "examples": [
            f"在 {app_name} 中执行操作",
        ],

        "metadata": {
            "learned_tools": tools_summary,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "auto_generated": True,
        },
    }

    return yaml.dump(skill_data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _success_rate(stats: Dict[str, Any]) -> float:
    """计算成功率"""
    total = stats.get("total_runs", 0)
    if total == 0:
        return 0.0
    return round(stats.get("success_count", 0) / total, 2)
