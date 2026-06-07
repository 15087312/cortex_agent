"""
价值观系统修改工具 — 仅大模型可调用

通过检测到价值观对齐差异后，大模型可以调用此工具来修改自身的行为规则。
修改经过质量门控和去重检查，确保只添加有价值的新规则。

权限: admin （仅大模型 "large" 角色可调用，专家/主管无法调用）
"""
from typing import Optional
from infra.tool_manager.tool_registry import ToolRegistry
from utils.logger import setup_logger

logger = setup_logger("value_tools")


def _get_value_system():
    """延迟加载价值观系统"""
    from modules.thinking.evolution.value_system import value_system
    return value_system


@ToolRegistry.register(
    "modify_value_system",
    description="修改 AI 的行为规则（仅大模型可用）。用于自我修正行为偏差或添加新的行为准则。",
    params={
        "action": "操作类型: add_rule / remove_rule / update_rule / cleanup / reset",
        "section": "规则分类 (基本原则 / 行为准则 / 进化记录)，reset 时可省略",
        "rule": "规则内容或更新的规则，对于 remove_rule 和 update_rule 必填",
        "new_rule": "新规则内容，仅 update_rule 时使用",
        "reason": "修改理由，用于审计日志（可选）",
    },
    category="admin",
    tags=["value_system", "evolution", "admin"],
    risk_level="CRITICAL",
    core=True,
)
def modify_value_system(
    action: str,
    section: Optional[str] = None,
    rule: Optional[str] = None,
    new_rule: Optional[str] = None,
    reason: Optional[str] = None,
) -> str:
    """修改价值观系统（仅大模型可调用）

    Args:
        action: 操作类型
            - add_rule: 添加新规则
            - remove_rule: 删除规则
            - update_rule: 更新规则
            - cleanup: 清理垃圾规则
            - reset: 重置为默认值

        section: 规则分类（对 reset 可省略）
            - 基本原则: 基础行为原则
            - 行为准则: 具体的行为规范
            - 进化记录: 自我修改历史

        rule: 规则内容
            - add_rule: 新规则文本
            - remove_rule: 要删除的规则
            - update_rule: 旧规则内容

        new_rule: 新规则内容（仅 update_rule 使用）

        reason: 修改理由，用于审计追踪

    Returns:
        执行结果信息
    """
    action = action.strip().lower()
    value_sys = _get_value_system()

    logger.info(f"[modify_value_system] 操作: {action}, section: {section}, reason: {reason}")

    try:
        if action == "add_rule":
            if not section or not rule:
                return "❌ 错误: add_rule 需要 section 和 rule 参数"

            section = section.strip()
            rule_text = rule.strip()

            # 质量门控检查
            if not value_sys._is_valid_rule(rule_text):
                logger.warning(f"[modify_value_system] 规则被质量门控拦截: {rule_text[:60]}")
                return f"⚠️ 规则未通过质量门控（过于通用、过短或不符合规范）: {rule_text[:60]}"

            # 去重检查
            sections_dict = value_sys.get_values_dict()
            for existing_rule in sections_dict.get(section, []):
                if value_sys._rules_too_similar(rule_text, existing_rule):
                    logger.info(f"[modify_value_system] 规则与已有规则重复")
                    return f"⚠️ 规则与已有规则过于相似，已跳过: {existing_rule[:60]}"

            # 添加规则
            value_sys.add_rule(section, rule_text)
            logger.info(
                f"[modify_value_system] ✅ 规则已添加 "
                f"section={section}, rule={rule_text[:60]}, reason={reason}"
            )
            return (
                f"✅ 规则已成功添加到 [{section}]\n"
                f"规则: {rule_text}\n"
                f"理由: {reason if reason else '（未提供）'}"
            )

        elif action == "remove_rule":
            if not section or not rule:
                return "❌ 错误: remove_rule 需要 section 和 rule 参数"

            section = section.strip()
            rule_text = rule.strip()

            value_sys.remove_rule(section, rule_text)
            logger.info(
                f"[modify_value_system] ✅ 规则已删除 "
                f"section={section}, rule={rule_text[:60]}, reason={reason}"
            )
            return (
                f"✅ 规则已成功从 [{section}] 删除\n"
                f"规则: {rule_text}\n"
                f"理由: {reason if reason else '（未提供）'}"
            )

        elif action == "update_rule":
            if not section or not rule or not new_rule:
                return "❌ 错误: update_rule 需要 section、rule 和 new_rule 参数"

            section = section.strip()
            old_rule_text = rule.strip()
            new_rule_text = new_rule.strip()

            # 新规则质量门控
            if not value_sys._is_valid_rule(new_rule_text):
                return f"⚠️ 新规则未通过质量门控: {new_rule_text[:60]}"

            value_sys.update_rule(section, old_rule_text, new_rule_text)
            logger.info(
                f"[modify_value_system] ✅ 规则已更新 "
                f"section={section}, old={old_rule_text[:60]}, new={new_rule_text[:60]}, "
                f"reason={reason}"
            )
            return (
                f"✅ 规则已成功更新\n"
                f"旧规则: {old_rule_text}\n"
                f"新规则: {new_rule_text}\n"
                f"理由: {reason if reason else '（未提供）'}"
            )

        elif action == "cleanup":
            value_sys.cleanup()
            logger.info(f"[modify_value_system] ✅ 价值观清理完成, reason={reason}")
            return (
                f"✅ 价值观系统已清理，所有垃圾规则已移除\n"
                f"理由: {reason if reason else '（未提供）'}"
            )

        elif action == "reset":
            value_sys.reset_to_default()
            logger.info(f"[modify_value_system] ✅ 价值观已重置为默认值, reason={reason}")
            return (
                f"✅ 价值观已重置为默认值\n"
                f"理由: {reason if reason else '（未提供）'}"
            )

        else:
            return f"❌ 未知操作: {action}，支持的操作: add_rule / remove_rule / update_rule / cleanup / reset"

    except Exception as e:
        logger.error(f"[modify_value_system] 异常: {e}")
        return f"❌ 执行出错: {str(e)}"


@ToolRegistry.register(
    "get_current_values",
    description="查看当前的 AI 行为规则（任何角色可调用）",
    params={
        "format": "输出格式: full / compact / sections (默认: compact)",
    },
    category="query",
    tags=["value_system", "query"],
    risk_level="LOW",
    core=True,
)
def get_current_values(format: str = "compact") -> str:
    """查看当前的价值观规则

    Args:
        format: 输出格式
            - full: 完整内容
            - compact: 精简版本
            - sections: 按分类列出

    Returns:
        价值观内容
    """
    value_sys = _get_value_system()
    format = format.strip().lower()

    try:
        if format == "full":
            content = value_sys.load()
            return f"【完整价值观文本】\n{content}"

        elif format == "compact":
            compact = value_sys.build_compact_context()
            return compact if compact else "（暂无规则）"

        elif format == "sections":
            sections_dict = value_sys.get_values_dict()
            if not sections_dict:
                return "（暂无规则）"

            lines = ["【价值观规则分类】"]
            for section, rules in sections_dict.items():
                if section == "进化记录":
                    continue
                if rules:
                    lines.append(f"\n[{section}]")
                    for rule in rules:
                        lines.append(f"  • {rule}")
            return "\n".join(lines)

        else:
            return f"❌ 未知格式: {format}，支持: full / compact / sections"

    except Exception as e:
        logger.error(f"[get_current_values] 异常: {e}")
        return f"❌ 查询出错: {str(e)}"


@ToolRegistry.register(
    "get_evolution_log",
    description="查看价值观系统的修改历史（审计用）",
    params={
        "limit": "显示最近 N 条记录（默认: 20）",
    },
    category="query",
    tags=["value_system", "audit"],
    risk_level="LOW",
    core=False,
)
def get_evolution_log(limit: int = 20) -> str:
    """查看价值观修改历史

    Args:
        limit: 显示最近 N 条记录

    Returns:
        修改历史
    """
    value_sys = _get_value_system()

    try:
        log = value_sys.get_evolution_log(limit=limit)
        if not log:
            return "（暂无修改历史）"

        lines = ["【价值观修改历史】"]
        for entry in log:
            timestamp = entry.get("ctime", "？")
            action = entry.get("action", "？")
            details = entry.get("details", {})
            lines.append(f"\n{timestamp}")
            lines.append(f"  操作: {action}")
            if action == "add_rule":
                lines.append(f"  分类: {details.get('section')}")
                lines.append(f"  规则: {details.get('rule')}")
            elif action == "remove_rule":
                lines.append(f"  分类: {details.get('section')}")
                lines.append(f"  规则: {details.get('rule')}")
            elif action == "update_rule":
                lines.append(f"  分类: {details.get('section')}")
                lines.append(f"  旧规则: {details.get('old')}")
                lines.append(f"  新规则: {details.get('new')}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"[get_evolution_log] 异常: {e}")
        return f"❌ 查询出错: {str(e)}"
