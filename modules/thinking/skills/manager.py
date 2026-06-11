"""技能管理器 — 加载、匹配、检索技能

技能存储在 skills/ 目录下，每个 YAML 文件是一个技能定义。
"""
import os
from pathlib import Path
from typing import Dict, List, Optional
from utils.logger import setup_logger
from .skill import Skill, SkillRule, ToolRules, WorkflowStep

logger = setup_logger("skill_manager")

# 技能文件目录（项目根目录下的 skills/）
_SKILLS_DIR = None


def _get_skills_dir() -> Path:
    """获取技能文件目录"""
    global _SKILLS_DIR
    if _SKILLS_DIR is None:
        # 从项目根目录查找
        project_root = Path(__file__).parent.parent.parent.parent
        _SKILLS_DIR = project_root / "skills"
    return _SKILLS_DIR


class SkillManager:
    """技能管理器 — 单例，管理所有技能的加载和检索"""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._loaded = False

    def load_skills(self, directory: Optional[str] = None) -> int:
        """从目录加载所有 YAML 技能文件

        Args:
            directory: 技能文件目录，默认为项目根目录下的 skills/

        Returns:
            加载的技能数量
        """
        if directory:
            skills_dir = Path(directory)
        else:
            skills_dir = _get_skills_dir()

        if not skills_dir.exists():
            logger.warning(f"[技能] 目录不存在: {skills_dir}")
            return 0

        count = 0
        for file_path in sorted(skills_dir.glob("*.yaml")):
            try:
                skill = self._load_yaml(file_path)
                if skill:
                    self._skills[skill.id] = skill
                    count += 1
                    logger.info(f"[技能] 加载: {skill.id} ({skill.name})")
            except Exception as e:
                logger.warning(f"[技能] 加载失败 {file_path.name}: {e}")

        for file_path in sorted(skills_dir.glob("*.yml")):
            try:
                skill = self._load_yaml(file_path)
                if skill:
                    self._skills[skill.id] = skill
                    count += 1
                    logger.info(f"[技能] 加载: {skill.id} ({skill.name})")
            except Exception as e:
                logger.warning(f"[技能] 加载失败 {file_path.name}: {e}")

        # 扫描 skills/learned/ 子目录（已学工具自动生成的 Skill）
        learned_dir = skills_dir / "learned"
        if learned_dir.exists():
            for file_path in sorted(learned_dir.glob("*.yaml")):
                try:
                    skill = self._load_yaml(file_path)
                    if skill:
                        self._skills[skill.id] = skill
                        count += 1
                        logger.info(f"[技能] 加载 learned: {skill.id} ({skill.name})")
                except Exception as e:
                    logger.warning(f"[技能] 加载失败 {file_path.name}: {e}")

        self._loaded = True
        logger.info(f"[技能] 共加载 {count} 个技能")
        return count

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        """按 ID 获取技能"""
        if not self._loaded:
            self.load_skills()
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Skill]:
        """列出所有已加载的技能"""
        if not self._loaded:
            self.load_skills()
        return list(self._skills.values())

    def match_skill(self, user_input: str) -> Optional[Skill]:
        """根据用户输入自动匹配最合适的技能

        匹配策略：
        - 关键词匹配（权重 3，要求关键词 ≥ 2 字符）
        - 角色名/技能名匹配（权重 2）
        - 描述关键词匹配（权重 1，取前 5 个词，要求 ≥ 2 字符）
        - 激活阈值：至少命中 2 个关键词（score ≥ 6）
        """
        if not self._loaded:
            self.load_skills()

        if not self._skills:
            return None

        best_skill = None
        best_score = 0

        user_lower = user_input.lower()

        for skill in self._skills.values():
            score = 0
            keyword_hits = 0

            # 关键词匹配（权重最高，要求 ≥ 2 字符避免单字误触发）
            for kw in skill.keywords:
                if len(kw) >= 2 and kw.lower() in user_lower:
                    score += 3
                    keyword_hits += 1

            # 角色名匹配
            if skill.role and len(skill.role) >= 2 and skill.role.lower() in user_lower:
                score += 2

            # 技能名匹配
            if skill.name and len(skill.name) >= 2 and skill.name.lower() in user_lower:
                score += 2

            # 描述关键词匹配（取前5个词）
            if skill.description:
                desc_words = skill.description[:100].split()
                for word in desc_words[:5]:
                    if len(word) >= 2 and word.lower() in user_lower:
                        score += 1

            if score > best_score:
                best_score = score
                best_skill = skill

        # 单关键词命中即可激活（关键词已要求 ≥ 2 字符，误触发率低）
        if best_score >= 3 and best_skill:
            logger.info(
                f"[技能] 自动匹配: {best_skill.id} "
                f"(score={best_score}, input={user_input[:30]}...)"
            )
            return best_skill

        return None

    def _load_yaml(self, file_path: Path) -> Optional[Skill]:
        """从 YAML 文件加载单个技能"""
        try:
            import yaml
        except ImportError:
            logger.warning("[技能] 需要安装 pyyaml: pip install pyyaml")
            return None

        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or not isinstance(data, dict):
            return None

        # 解析规章
        rules = []
        for r in data.get("rules", []):
            if isinstance(r, str):
                rules.append(SkillRule(content=r, severity="must"))
            elif isinstance(r, dict):
                rules.append(SkillRule(
                    id=r.get("id", ""),
                    content=r.get("content", ""),
                    severity=r.get("severity", "must"),
                ))

        # 解析流程
        workflow = []
        for w in data.get("workflow", []):
            if isinstance(w, dict):
                workflow.append(WorkflowStep(
                    step=w.get("step", 0),
                    name=w.get("name", ""),
                    description=w.get("description", ""),
                    output=w.get("output", ""),
                ))

        # 解析工具范围
        tool_rules = None
        tr = data.get("tool_rules")
        if tr and isinstance(tr, dict):
            tool_rules = ToolRules(
                allow_tools=tr.get("allow_tools", []),
                allow_tags=tr.get("allow_tags", []),
                allow_categories=tr.get("allow_categories", []),
                allow_core_only=tr.get("allow_core_only", False),
                block_tools=tr.get("block_tools", []),
                block_tags=tr.get("block_tags", []),
                block_categories=tr.get("block_categories", []),
            )

        return Skill(
            id=data.get("id", file_path.stem),
            name=data.get("name", ""),
            description=data.get("description", ""),
            keywords=data.get("keywords", []),
            role=data.get("role", ""),
            personality=data.get("personality", ""),
            speaking_style=data.get("speaking_style", ""),
            expertise=data.get("expertise", []),
            weaknesses=data.get("weaknesses", []),
            rules=rules,
            workflow=workflow,
            tool_rules=tool_rules,
            examples=data.get("examples", []),
            metadata=data.get("metadata", {}),
        )


# 全局单例
skill_manager = SkillManager()
