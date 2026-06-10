"""
规范违反检测 — 感知系统的实时规范检测器

在感知系统中实时检测输出是否违反价值观和操作规范，
生成感知事件供大模型调整行为。

【架构】
  输出 → 规范检查 → ChangeEvent → AttentionPool → 系统提示词

【职责】
  ✅ 读取最新输出
  ✅ 与 core_values.txt 规则对比
  ✅ 生成违反事件
  ❌ 不触发回调或快速思考
"""
import threading
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from utils.logger import setup_logger

logger = setup_logger("rule_compliance_perception")


@dataclass
class ComplianceViolation:
    """规范违反事件"""
    violation_type: str  # "value" 或 "guideline"
    rule_category: str  # 规则分类（基本原则、行为准则等）
    violated_rule: str  # 违反的具体规则
    violation_details: str  # 违反的具体表现
    severity: str  # "low" / "medium" / "high"
    timestamp: float = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.time()

    def to_perception_event(self):
        """转换为感知事件"""
        from modules.perception import ChangeEvent

        icon_map = {
            "high": "🚨",
            "medium": "⚠️",
            "low": "💡",
        }
        icon = icon_map.get(self.severity, "📋")

        event = ChangeEvent(
            change_type="created",
            target_type="compliance_violation",
            target=f"{icon} {self.rule_category}",
            details={
                "violation_type": self.violation_type,
                "rule": self.violated_rule,
                "details": self.violation_details,
                "severity": self.severity,
            }
        )
        return event


class RuleCompliancePerception:
    """规范违反检测器

    实时监测输出是否违反核心价值观和操作规范。
    不触发任何回调，仅生成感知事件。
    """

    def __init__(self):
        import threading
        self.logger = setup_logger("rule_compliance")
        self._last_checked_content: Optional[str] = None
        self._cache_violations: List[ComplianceViolation] = []
        self._cache_lock = threading.Lock()

    def detect_violations(self, content: str) -> List[ComplianceViolation]:
        """检测输出中的规范违反

        Args:
            content: 要检查的输出内容

        Returns:
            违反事件列表
        """
        # 避免重复检查相同内容（线程安全）
        with self._cache_lock:
            if content == self._last_checked_content and self._cache_violations:
                return list(self._cache_violations)

        self._last_checked_content = content
        violations = []

        try:
            # 加载价值观规则
            from modules.thinking.evolution.value_system import value_system
            values_dict = value_system.get_values_dict()
        except Exception as e:
            self.logger.debug(f"加载规则失败: {e}")
            return []

        # 检查价值观规则
        for section, rules in values_dict.items():
            if section == "进化记录":
                continue

            for rule in rules:
                violation = self._check_rule_violation(content, section, rule)
                if violation:
                    violations.append(violation)

        self._cache_violations = violations
        self._last_checked_content = content
        return violations

    def _check_rule_violation(self, content: str, category: str, rule: str) -> Optional[ComplianceViolation]:
        """检查是否违反某条规则

        分析规则文本，提取关键模式，与输出对比。

        Args:
            content: 输出内容
            category: 规则分类
            rule: 规则文本

        Returns:
            违反事件，如无则返回None
        """
        if not content or not rule:
            return None

        content_lower = content.lower()

        # 检查禁止类规则 ("不要", "禁止", "避免")
        if any(marker in rule for marker in ["不要", "禁止", "避免"]):
            violations_detail = self._detect_negative_rule_violation(
                content_lower, rule
            )
            if violations_detail:
                return ComplianceViolation(
                    violation_type="value",
                    rule_category=category,
                    violated_rule=rule,
                    violation_details=violations_detail,
                    severity="medium",
                )

        # 检查要求类规则 ("要", "应该", "必须")
        if any(marker in rule for marker in ["要", "应该", "必须"]):
            violations_detail = self._detect_positive_rule_violation(
                content_lower, rule
            )
            if violations_detail:
                return ComplianceViolation(
                    violation_type="value",
                    rule_category=category,
                    violated_rule=rule,
                    violation_details=violations_detail,
                    severity="medium",
                )

        return None

    def _detect_negative_rule_violation(self, content_lower: str, rule: str) -> Optional[str]:
        """检查禁止类规则的违反

        例：
          规则："不要机械地说我是AI"
          检查：content 中是否有 "作为AI" "我是AI" 等表达
        """
        # 关键词匹配：规则中的关键词在内容中应该不出现
        negative_patterns = {
            "机械": ["作为ai", "作为一个ai", "作为助手", "我是ai"],
            "冗长": ["首先", "其次", "最后", "总之"],
            "重复": ["重复说", "再说一遍", "我刚才说"],
            "编造": ["我不知道", "无法确定", "不确定"],
        }

        for keyword, patterns in negative_patterns.items():
            if keyword in rule.lower():
                for pattern in patterns:
                    if pattern in content_lower:
                        return f"输出包含「{pattern}」，违反规则：{rule[:50]}"

        return None

    def _detect_positive_rule_violation(self, content_lower: str, rule: str) -> Optional[str]:
        """检查要求类规则的违反

        例：
          规则："要简洁有力"
          检查：content 是否过长/堆砌词汇
        """
        # 简单启发式检查：过长的输出
        if "简洁" in rule.lower() or "简短" in rule.lower():
            # 检查是否过于冗长（多个句子、过多段落）
            sentences = [s for s in content_lower.split('。') if s.strip()]
            if len(sentences) > 10:
                return f"输出过长（{len(sentences)}个句子），违反规则：{rule[:50]}"

        return None

    def generate_perception_events(self, violations: List[ComplianceViolation]) -> List:
        """将违反事件转换为感知事件

        Args:
            violations: 违反事件列表

        Returns:
            感知事件列表
        """
        return [v.to_perception_event() for v in violations]


# 单例实例
_compliance_perception: Optional[RuleCompliancePerception] = None
_compliance_perception_lock = threading.Lock()


def get_rule_compliance_perception() -> RuleCompliancePerception:
    """获取规范违反检测器单例"""
    global _compliance_perception
    if _compliance_perception is None:
        with _compliance_perception_lock:
            if _compliance_perception is None:
                _compliance_perception = RuleCompliancePerception()
    return _compliance_perception
