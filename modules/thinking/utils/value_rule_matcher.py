"""
价值观规则匹配工具
"""
import re
from typing import Dict, Any, List, Tuple, Optional


class ValueRule:
    """价值观规则"""
    
    def __init__(
        self,
        name: str,
        description: str,
        keywords: List[str] = None,
        patterns: List[str] = None,
        weight: float = 1.0,
        priority: int = 1
    ):
        self.name = name
        self.description = description
        self.keywords = keywords or []
        self.patterns = patterns or []
        self.weight = weight
        self.priority = priority
    
    def matches(self, text: str) -> bool:
        """检查文本是否匹配此规则"""
        text_lower = text.lower()
        
        for keyword in self.keywords:
            if keyword.lower() in text_lower:
                return True
        
        for pattern in self.patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return True
        
        return False


class ValueRuleMatcher:
    """
    价值观规则匹配器
    
    核心功能：
    - 定义价值观规则
    - 检查文本是否符合价值观
    - 生成违规警告
    """

    def __init__(self):
        self.rules: Dict[str, ValueRule] = {}
        self._load_default_rules()

    def _load_default_rules(self):
        """加载默认价值观规则"""
        
        self.rules = {
            "safety": ValueRule(
                name="安全优先",
                description="任何行动必须确保人身安全和系统安全",
                keywords=["安全", "危险", "保护", "伤害"],
                patterns=[r"小心", r"注意安全"],
                weight=1.0,
                priority=10
            ),
            
            "honesty": ValueRule(
                name="诚信为本",
                description="必须诚实，不欺骗、不造假、不隐瞒",
                keywords=["诚实", "真实", "虚假", "欺骗"],
                patterns=[r"说实话", r"真的"],
                weight=0.9,
                priority=9
            ),
            
            "privacy": ValueRule(
                name="保护隐私",
                description="不泄露用户隐私信息",
                keywords=["隐私", "秘密", "泄露", "暴露"],
                patterns=[r"保密", r"不说"],
                weight=0.9,
                priority=9
            ),
            
            "fairness": ValueRule(
                name="公平公正",
                description="不歧视、不偏见、客观中立",
                keywords=["公平", "歧视", "偏见", "歧视"],
                weight=0.8,
                priority=7
            ),
            
            "helpfulness": ValueRule(
                name="积极助人",
                description="主动帮助用户解决问题",
                keywords=["帮助", "解决", "协助"],
                patterns=[r"帮你", r"帮你"],
                weight=0.8,
                priority=6
            ),
            
            "responsibility": ValueRule(
                name="负责任",
                description="对自己的言行负责，不推卸责任",
                keywords=["责任", "负责", "承担"],
                weight=0.8,
                priority=6
            ),
            
            "legality": ValueRule(
                name="合法合规",
                description="不违反法律法规",
                keywords=["违法", "非法", "犯罪", "法规"],
                patterns=[r"合法", r"合规"],
                weight=1.0,
                priority=10
            ),
            
            "ethics": ValueRule(
                name="道德伦理",
                description="遵守基本道德伦理规范",
                keywords=["道德", "伦理", "善良"],
                weight=0.9,
                priority=8
            ),
        }

    def add_rule(self, rule: ValueRule):
        """添加规则"""
        self.rules[rule.name] = rule

    def remove_rule(self, rule_name: str) -> bool:
        """移除规则"""
        if rule_name in self.rules:
            del self.rules[rule_name]
            return True
        return False

    def match(self, text: str) -> List[ValueRule]:
        """匹配所有触发的规则"""
        matched = []
        for rule in self.rules.values():
            if rule.matches(text):
                matched.append(rule)
        return matched

    def check_violation(self, text: str) -> Dict[str, Any]:
        """
        检查文本是否违反价值观
        
        Returns:
            {
                "violated": bool,
                "warnings": ["违规描述"],
                "rules": [触发的规则]
            }
        """
        matched = self.match(text)
        
        if not matched:
            return {"violated": False, "warnings": [], "rules": []}
        
        warnings = [
            f"⚠️ 可能违反价值观：{rule.description}"
            for rule in matched
            if rule.weight >= 0.8
        ]
        
        return {
            "violated": len(warnings) > 0,
            "warnings": warnings,
            "rules": [{"name": r.name, "weight": r.weight} for r in matched]
        }

    def get_active_rules(self) -> List[Dict[str, Any]]:
        """获取所有活跃规则"""
        return [
            {
                "name": r.name,
                "description": r.description,
                "weight": r.weight,
                "priority": r.priority
            }
            for r in sorted(self.rules.values(), key=lambda x: x.priority, reverse=True)
        ]


value_rule_matcher = ValueRuleMatcher()
