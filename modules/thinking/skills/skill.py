"""技能定义 — 技能说明书

Skill 是提示词说明书 + 可选工具权限约束。
模型通过工具查询并阅读技能说明书，自行决定是否激活。

支持两种格式：
  - SKILL.md（主格式，skills/{name}/SKILL.md，带 YAML front matter）
  - .yaml （旧格式，向前兼容，skills/{name}.yaml）

每个技能包含：
  - name: 技能名称
  - description: 技能说明书（核心内容，模型阅读后就知道怎么做）
  - keywords: 匹配关键词
  - trigger: 触发规则 {include, exclude, min_score}（自动建议用）
  - tool_rules: 可选工具权限 {allow_tools, block_tools, block_tags, restrict_to}
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Skill:
    """技能说明书 — 提示词文档 + 可选工具约束"""
    id: str = ""
    name: str = ""
    description: str = ""     # 技能说明书正文（核心内容）
    keywords: List[str] = field(default_factory=list)  # 匹配关键词
    source: str = "yaml"      # "yaml" | "skill_md"，标识来源格式
    tool_rules: Optional[Dict] = None  # 可选工具权限（learned skill 使用）
    trigger: Optional[Dict] = None     # 可选触发规则 {include, exclude, min_score}
    metadata: Dict = field(default_factory=dict)
    enabled: bool = True      # 是否启用（管理端可开关）
    path: str = ""            # 源文件绝对路径（编辑/删除用）
    raw_content: str = ""     # 源文件原始文本（编辑回填）

    def to_prompt_block(self) -> str:
        """生成技能说明书 prompt 块"""
        parts = [f"══════ 技能: {self.name} ══════"]
        if self.description:
            parts.append(self.description)
        parts.append("══════ 技能结束 ══════")
        return "\n\n".join(parts)

    def to_suggestion_block(self) -> str:
        """生成简短的匹配建议 prompt 块"""
        return (
            f"【可激活技能: {self.name}】\n"
            f"{self.description[:200]}"
        )
