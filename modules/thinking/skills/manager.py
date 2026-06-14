"""技能管理器 — 加载、匹配技能

技能以 YAML 文件存储在 skills/ 目录，每个文件是一个技能说明书。
模型通过 list_skills / get_skill_detail 工具查询和阅读。
"""
from pathlib import Path
from typing import Dict, List, Optional

from utils.logger import setup_logger
from .skill import Skill

logger = setup_logger("skill_manager")

_SKILLS_DIR = None


def _get_skills_dir() -> Path:
    global _SKILLS_DIR
    if _SKILLS_DIR is None:
        _SKILLS_DIR = Path(__file__).parent.parent.parent.parent / "skills"
    return _SKILLS_DIR


class SkillManager:
    """技能管理器 — 加载 & 检索技能说明书"""

    def __init__(self):
        self._skills: Dict[str, Skill] = {}
        self._loaded = False

    def load_skills(self, directory: Optional[str] = None) -> int:
        """从 skills/ 目录加载所有 YAML 技能文件"""
        skills_dir = Path(directory) if directory else _get_skills_dir()

        if not skills_dir.exists():
            logger.warning(f"[技能] 目录不存在: {skills_dir}")
            return 0

        count = 0
        for glob_pat in ("*.yaml", "*.yml"):
            for file_path in sorted(skills_dir.glob(glob_pat)):
                try:
                    skill = self._load_yaml(file_path)
                    if skill:
                        self._skills[skill.id] = skill
                        count += 1
                        logger.info(f"[技能] 加载: {skill.id} ({skill.name})")
                except Exception as e:
                    logger.warning(f"[技能] 加载失败 {file_path.name}: {e}")

        self._loaded = True
        logger.info(f"[技能] 共加载 {count} 个技能")
        return count

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        if not self._loaded:
            self.load_skills()
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Skill]:
        if not self._loaded:
            self.load_skills()
        return list(self._skills.values())

    def match_skill(self, user_input: str) -> Optional[Skill]:
        """根据用户输入自动匹配最合适的技能

        匹配策略：关键词包含匹配（不区分大小写），分数最高者胜。
        阈值：至少命中 1 个关键词。
        """
        if not self._loaded:
            self.load_skills()
        if not self._skills or not user_input:
            return None

        user_lower = user_input.lower()
        best_skill = None
        best_score = 0

        for skill in self._skills.values():
            score = 0
            for kw in skill.keywords:
                if len(kw) >= 2 and kw.lower() in user_lower:
                    score += 1
            if score > best_score:
                best_score = score
                best_skill = skill

        if best_score >= 1 and best_skill:
            logger.info(f"[技能] 自动匹配: {best_skill.id} (score={best_score})")
            return best_skill
        return None

    def search_skills(self, query: str) -> List[Skill]:
        """关键词搜索技能（供 list_skills 工具做模糊搜索）"""
        if not self._loaded:
            self.load_skills()
        if not query:
            return self.list_skills()
        q = query.lower()
        results = []
        for skill in self._skills.values():
            if (q in skill.name.lower() or
                q in skill.description.lower() or
                any(q in kw.lower() for kw in skill.keywords)):
                results.append(skill)
        return results

    def _load_yaml(self, file_path: Path) -> Optional[Skill]:
        try:
            import yaml
        except ImportError:
            logger.warning("[技能] 需要 pyyaml")
            return None
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            return None
        return Skill(
            id=data.get("id", file_path.stem),
            name=data.get("name", ""),
            description=data.get("description", ""),
            keywords=data.get("keywords", []),
            metadata=data.get("metadata", {}),
        )


skill_manager = SkillManager()
