"""技能定义 — 技能说明书

Skill 是纯提示词说明书，不控制身份、不控制工具权限、不控制记忆。
模型通过工具查询并阅读技能说明书，自行决定是否遵循。

每个 YAML 文件 = 一个技能，包含：
  - name: 技能名称
  - description: 技能说明书（核心内容，模型阅读后就知道怎么做）
  - keywords: 触发关键词（用于自动匹配）
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Skill:
    """技能说明书 — 纯提示词文档"""
    id: str = ""
    name: str = ""
    description: str = ""     # 技能说明书正文（核心内容）
    keywords: List[str] = field(default_factory=list)  # 匹配关键词
    metadata: Dict = field(default_factory=dict)

    def to_prompt_block(self) -> str:
        """生成技能说明书 prompt 块"""
        parts = [f"══════ 技能: {self.name} ══════"]
        if self.description:
            parts.append(self.description)
        parts.append(f"══════ 技能结束 ══════")
        return "\n\n".join(parts)

    def to_suggestion_block(self) -> str:
        """生成简短的匹配建议 prompt 块"""
        return (
            f"【可激活技能: {self.name}】\n"
            f"{self.description[:200]}"
        )
