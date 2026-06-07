"""
探针模板系统

主管探针和专家探针的模板定义
这些模板描述了何时调用对应的模型
"""
from typing import Dict, List, Any
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ProbeTemplate:
    """探针模板"""
    name: str
    target_model: str  # 主管模型或专家模型
    trigger_conditions: List[str]  # 触发条件关键词
    trigger_patterns: List[str]  # 正则匹配模式
    min_confidence: float  # 最低置信度
    description: str  # 描述
    priority: int  # 优先级
    ttl_seconds: int = 1800  # 生命周期30分钟


# 主管模型探针模板
MANAGER_PROBE_TEMPLATES = [
    ProbeTemplate(
        name="manager_deep_analysis",
        target_model="medium_model",
        trigger_conditions=[
            "详细解释", "深度分析", "全面分析", "系统分析",
            "为什么", "如何实现", "原理是什么", "机制分析"
        ],
        trigger_patterns=[
            r"详细.*",
            r"深度.*",
            r"为什么.*",
            r"如何.*原理",
            r".*机制.*",
        ],
        min_confidence=0.6,
        description="需要深度分析时触发主管模型",
        priority=3,
    ),
    ProbeTemplate(
        name="manager_multi_step",
        target_model="medium_model",
        trigger_conditions=[
            "首先", "然后", "接着", "第一步", "第二阶段",
            "多步骤", "分步", "流程", "步骤"
        ],
        trigger_patterns=[
            r"首先.*然后",
            r"第一.*第二.*",
            r"多步骤.*",
        ],
        min_confidence=0.5,
        description="多步骤任务时触发主管模型",
        priority=2,
    ),
    ProbeTemplate(
        name="manager_multi_expert",
        target_model="medium_model",
        trigger_conditions=[
            "需要调用多个", "协同处理", "综合分析",
            "复杂任务", "需要多个专家"
        ],
        trigger_patterns=[
            r"多个.*专家",
            r"协同.*",
            r"复杂.*任务",
        ],
        min_confidence=0.7,
        description="需要多个专家协同时触发主管模型",
        priority=4,
    ),
    ProbeTemplate(
        name="manager_expert_dispatch",
        target_model="medium_model",
        trigger_conditions=[
            "调用主管", "启动分析", "请分析",
            "需要调度", "专家调度"
        ],
        trigger_patterns=[
            r"调用主管",
            r"启动.*分析",
            r"请.*分析",
        ],
        min_confidence=0.8,
        description="显式要求调用主管模型",
        priority=5,
    ),
]


# 专家模型探针模板
EXPERT_PROBE_TEMPLATES = [
    ProbeTemplate(
        name="expert_calc",
        target_model="small_model",
        trigger_conditions=[
            "计算", "等于多少", "加减乘除", "数学",
            "运算", "求解", "方程"
        ],
        trigger_patterns=[
            r".*\+.*",
            r".*-.*",
            r".*\*.*",
            r".*/.*",
            r"计算.*",
            r"等于.*",
        ],
        min_confidence=0.6,
        description="计算任务时触发计算专家",
        priority=2,
    ),
    ProbeTemplate(
        name="expert_code",
        target_model="small_model",
        trigger_conditions=[
            "代码", "编程", "python", "函数", "bug",
            "调试", "写代码", "程序"
        ],
        trigger_patterns=[
            r".*python.*",
            r".*代码.*",
            r".*函数.*",
            r".*bug.*",
        ],
        min_confidence=0.6,
        description="编程任务时触发代码专家",
        priority=3,
    ),
    ProbeTemplate(
        name="expert_search",
        target_model="small_model",
        trigger_conditions=[
            "搜索", "查找", "查询", "搜索一下",
            "找一下", "网上", "最新"
        ],
        trigger_patterns=[
            r"搜索.*",
            r"查找.*",
            r"查询.*",
            r"找一下.*",
        ],
        min_confidence=0.7,
        description="搜索任务时触发搜索专家",
        priority=3,
    ),
    ProbeTemplate(
        name="expert_analysis",
        target_model="small_model",
        trigger_conditions=[
            "分析", "数据分析", "总结", "归纳",
            "统计", "对比"
        ],
        trigger_patterns=[
            r".*分析.*",
            r".*总结.*",
            r".*统计.*",
        ],
        min_confidence=0.5,
        description="分析任务时触发分析专家",
        priority=2,
    ),
    ProbeTemplate(
        name="expert_creative",
        target_model="small_model",
        trigger_conditions=[
            "创作", "写诗", "写文章", "创意",
            "灵感", "构思"
        ],
        trigger_patterns=[
            r"创作.*",
            r"写.*诗",
            r"创意.*",
        ],
        min_confidence=0.5,
        description="创意任务时触发创意专家",
        priority=2,
    ),
]


def get_all_templates() -> Dict[str, List[ProbeTemplate]]:
    """获取所有探针模板"""
    return {
        "manager": MANAGER_PROBE_TEMPLATES,
        "expert": EXPERT_PROBE_TEMPLATES,
    }


def get_manager_templates() -> List[ProbeTemplate]:
    """获取主管模型探针模板"""
    return MANAGER_PROBE_TEMPLATES.copy()


def get_expert_templates() -> List[ProbeTemplate]:
    """获取专家模型探针模板"""
    return EXPERT_PROBE_TEMPLATES.copy()