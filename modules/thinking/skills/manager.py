"""技能管理器 — 加载、匹配技能

技能以 SKILL.md（主格式）或 .yaml（旧格式）存储在 skills/ 目录。
模型通过 list_skills / get_skill_detail 工具查询和阅读。

格式优先级：.yaml > SKILL.md（ID 冲突时 YAML 胜出）
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
        """从 skills/ 目录加载 SKILL.md 和 YAML 技能文件（含 learned/ 子目录）"""
        skills_dir = Path(directory) if directory else _get_skills_dir()

        if not skills_dir.exists():
            logger.warning(f"[技能] 目录不存在: {skills_dir}")
            return 0

        count = 0
        search_dirs = [skills_dir]
        if not directory:
            learned_dir = skills_dir / "learned"
            if learned_dir.exists():
                search_dirs.append(learned_dir)

        for search_dir in search_dirs:
            # SKILL.md — 扫描所有子目录（支持扁平和嵌套，如 gsap-skills/skills/*/SKILL.md）
            seen_ids = set()
            for skill_md_path in sorted(search_dir.rglob("SKILL.md")):
                if skill_md_path.parent == search_dir:
                    continue  # 跳过顶层的 SKILL.md（不存在）
                try:
                    skill = self._load_skill_md(skill_md_path)
                    if skill and skill.id not in seen_ids:
                        self._skills[skill.id] = skill
                        seen_ids.add(skill.id)
                        count += 1
                        logger.debug(f"[技能] 加载 SKILL.md: {skill.id} ({skill.name})")
                except Exception as e:
                    logger.warning(f"[技能] SKILL.md 加载失败 {skill_md_path}: {e}")

            # .yaml fallback（旧格式，向前兼容）
            for glob_pat in ("*.yaml", "*.yml"):
                for file_path in sorted(search_dir.glob(glob_pat)):
                    try:
                        skill = self._load_yaml(file_path)
                        if skill and skill.id not in self._skills:
                            self._skills[skill.id] = skill
                            count += 1
                            logger.debug(f"[技能] 加载 YAML: {skill.id} ({skill.name})")
                    except Exception as e:
                        logger.warning(f"[技能] 加载失败 {file_path.name}: {e}")

        self._loaded = True
        logger.debug(f"[技能] 共加载 {count} 个技能")
        return count

    def reload(self) -> int:
        """重新加载全部技能（增删改后调用）"""
        self._skills = {}
        self._loaded = False
        return self.load_skills()

    def get_skill(self, skill_id: str) -> Optional[Skill]:
        if not self._loaded:
            self.load_skills()
        return self._skills.get(skill_id)

    def list_skills(self) -> List[Skill]:
        if not self._loaded:
            self.load_skills()
        return list(self._skills.values())

    def match_skill(self, user_input: str, role: str = "") -> Optional[Skill]:
        """根据用户输入自动匹配最合适的技能（不区分大小写）

        匹配策略：
          1. trigger.exclude 命中 → 跳过（负向匹配优先）
          2. trigger.include + min_score → 精准匹配
          3. keywords → 宽匹配
        """
        if not self._loaded:
            self.load_skills()
        if not self._skills or not user_input:
            return None

        user_lower = user_input.lower()
        best_skill = None
        best_score = 0

        for skill in self.list_skills_for_role(role):
            if not skill.enabled:
                continue
            # 负向匹配：命中 trigger.exclude 则跳过
            trig = skill.trigger or {}
            exclude = trig.get("exclude") or []
            if any(e.lower() in user_lower for e in exclude if len(e) >= 2):
                continue

            score = 0

            # trigger.include 精准匹配（权重 3）
            include = trig.get("include") or []
            for inc in include:
                if len(inc) >= 2 and inc.lower() in user_lower:
                    score += 3

            # keywords 宽匹配（权重 1）
            for kw in skill.keywords:
                if len(kw) >= 2 and kw.lower() in user_lower:
                    score += 1

            # 技能名称匹配（权重 2）— 用户说"代码审查"直接匹配 code_review
            if skill.name and len(skill.name) >= 2:
                if skill.name.lower() in user_lower:
                    score += 2
            # 技能描述匹配（权重 1）
            if skill.description and len(skill.description) >= 2:
                desc_words = skill.description.lower().split()
                match_count = sum(1 for w in desc_words if len(w) >= 2 and w in user_lower)
                if match_count >= 2:
                    score += 1

            if score > best_score:
                min_score = trig.get("min_score", 1)
                if score >= min_score:
                    best_score = score
                    best_skill = skill

        if best_skill:
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

    # ── 管理支持：CRUD / 启用 / per-role 可见性 ──────────────────────────

    def to_listing(self) -> List[dict]:
        """全部技能的管理列表（含 enabled/path/source）"""
        if not self._loaded:
            self.load_skills()
        return [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "keywords": list(s.keywords),
                "source": s.source,
                "enabled": s.enabled,
                "metadata": s.metadata,
                "tool_rules": s.tool_rules,
                "trigger": s.trigger,
            }
            for s in self._skills.values()
        ]

    def list_skills_for_role(self, role: str = "") -> List[Skill]:
        """返回某角色可见的已启用技能（per-agent 白名单过滤）

        role 无配置或配置含 "*" → 全部 enabled；否则只返回白名单内的。
        """
        skills = [s for s in self.list_skills() if s.enabled]
        if not role:
            return skills
        try:
            from config.settings import settings
            visible = settings.get_role_skills(role)
        except Exception:
            return skills
        if not visible or "*" in visible:
            return skills
        return [s for s in skills if s.id in visible]

    def create_skill(self, skill_id: str, name: str, description: str = "",
                     keywords=None, trigger=None, tool_rules=None) -> "tuple[bool, str]":
        """创建新技能（写 skills/<id>/SKILL.md 并重载）"""
        import re
        import yaml
        skill_id = (skill_id or "").strip()
        if not re.match(r"^[a-z0-9_\-]+$", skill_id):
            return False, "id 仅允许小写字母/数字/下划线/连字符"
        if not name or not name.strip():
            return False, "名称不能为空"
        if skill_id in self._skills:
            return False, f"技能已存在: {skill_id}"
        path = _get_skills_dir() / skill_id / "SKILL.md"
        if path.exists():
            return False, f"技能目录已存在: {skill_id}"
        front = {
            "name": name.strip(),
            "keywords": list(keywords or []),
            "trigger": trigger or {},
            "metadata": {"version": 1, "type": "custom"},
            "enabled": True,
        }
        if tool_rules:
            front["tool_rules"] = tool_rules
        text = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n\n" + (description or "").strip()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except Exception as e:
            return False, f"写入失败: {e}"
        self.reload()
        return True, ""

    def update_skill(self, skill_id: str, name=None, description=None, keywords=None,
                     trigger=None, tool_rules=None, enabled=None) -> "tuple[bool, str]":
        """更新技能（改 frontmatter/正文并重载）。None 表示不修改，"" 表示清空。"""
        import yaml
        skill = self._skills.get(skill_id)
        if not skill:
            return False, f"技能不存在: {skill_id}"
        path = Path(skill.path)
        if not path.exists():
            return False, "技能源文件不存在"
        raw = path.read_text(encoding="utf-8")
        front: dict = {}
        body = raw
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    front = yaml.safe_load(parts[1].strip()) or {}
                except Exception:
                    front = {}
                body = parts[2].strip() if len(parts) == 3 else ""
        if name is not None:
            front["name"] = str(name)
        if keywords is not None:
            front["keywords"] = list(keywords)
        if trigger is not None:
            front["trigger"] = trigger
        if tool_rules is not None:
            if tool_rules:
                front["tool_rules"] = tool_rules
            else:
                front.pop("tool_rules", None)
        if enabled is not None:
            front["enabled"] = bool(enabled)
        if description is not None:
            body = str(description).strip()
        text = "---\n" + yaml.safe_dump(front, allow_unicode=True, sort_keys=False).rstrip() + "\n---\n\n" + body
        try:
            path.write_text(text, encoding="utf-8")
        except Exception as e:
            return False, f"写入失败: {e}"
        self.reload()
        return True, ""

    def set_enabled(self, skill_id: str, enabled: bool) -> "tuple[bool, str]":
        return self.update_skill(skill_id, enabled=enabled)

    def delete_skill(self, skill_id: str) -> "tuple[bool, str]":
        """删除技能（保护 builtin 与 learned 技能文件）"""
        skill = self._skills.get(skill_id)
        if not skill:
            return False, f"技能不存在: {skill_id}"
        if skill.source != "skill_md":
            return False, "仅支持删除 SKILL.md 格式的技能（learned/yaml 受保护）"
        if (skill.metadata or {}).get("type") == "builtin":
            return False, "内置技能不可删除，可先禁用"
        try:
            path = Path(skill.path)
            if path.exists():
                path.unlink()
            parent = path.parent
            try:
                parent.rmdir()
            except OSError:
                pass
        except Exception as e:
            return False, f"删除失败: {e}"
        self.reload()
        return True, ""

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
            source="yaml",
            tool_rules=data.get("tool_rules"),
            trigger=data.get("trigger"),
            metadata=data.get("metadata", {}),
            enabled=bool(data.get("enabled", True)),
            path=str(file_path),
            raw_content=file_path.read_text(encoding="utf-8"),
        )

    def _load_skill_md(self, file_path: Path) -> Optional[Skill]:
        """加载 SKILL.md（YAML front matter + Markdown 正文）"""
        try:
            import yaml
        except ImportError:
            logger.warning("[技能] 需要 pyyaml")
            return None

        content = file_path.read_text(encoding="utf-8")
        if not content.startswith("---"):
            # 无 front matter，整个文件当描述
            skill_id = file_path.parent.name
            return Skill(
                id=skill_id,
                name=skill_id,
                description=content.strip(),
                source="skill_md",
                path=str(file_path),
                raw_content=content,
            )

        # 分离 front matter 和正文
        parts = content.split("---", 2)
        if len(parts) < 3:
            return None

        raw_front = parts[1].strip()
        body = parts[2].strip()

        data = yaml.safe_load(raw_front)
        if not data or not isinstance(data, dict):
            return None

        skill_id = data.get("id", file_path.parent.name)
        body_description = body or ""

        return Skill(
            id=skill_id,
            name=data.get("name") or skill_id,
            description=body_description,
            keywords=data.get("keywords", []),
            source="skill_md",
            tool_rules=data.get("tool_rules"),
            trigger=data.get("trigger"),
            metadata=data.get("metadata", {}),
            enabled=bool(data.get("enabled", True)),
            path=str(file_path),
            raw_content=content,
        )


skill_manager = SkillManager()
