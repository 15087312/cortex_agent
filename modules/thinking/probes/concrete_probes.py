"""
具体探针实现

部署时可用的探针子类，每个实现 _rule_based_detect 做关键字/模式匹配。
"""
import re
from typing import List, Any

from utils.logger import setup_logger
from .probe_base import Probe, ProbePriority, ProbeLayer, ProbeSignal, ProbeResult


class SafetyProbe(Probe):
    """安全/价值观探针 — 检测潜在安全风险或价值观冲突"""

    def __init__(self, model_manager=None):
        super().__init__(
            name="safety",
            model_manager=model_manager,
            priority=ProbePriority.CRITICAL,
        )
        self._safety_keywords = {
            # 高危
            "如何制作武器", "如何制造炸弹", "自杀方法", "自残",
            "毒品制作", "如何杀人", "违法", "犯罪方法",
            # 价值观
            "歧视", "种族主义", "性别歧视", "仇恨言论",
            # 隐私
            "如何窃取", "黑客攻击", "破解密码", "盗取账号",
            # 通用安全
            "safety", "harmful", "dangerous", "illegal", "unsafe",
            "weapon", "bomb", "suicide", "drug",
        }
        self._high_risk_patterns = [
            r"(如何|怎么).*(制作|制造|学习).*(武器|炸弹|毒品|毒药)",
            r"(自杀|自残|结束生命)",
            r"(黑客|破解).*(系统|密码|账号|网站)",
            r"(种族|性别|宗教).*(歧视|攻击|仇恨)",
        ]

    @property
    def prompt(self) -> str:
        return "检测用户输入中是否包含安全风险或价值观冲突内容，需要安全专家介入。"

    def _rule_based_detect(self, context: str) -> str:
        if not context:
            return "触发：否\n置信度：0.0\n触发原因：无输入"

        text = context.lower()

        # 先检查高危正则模式
        for pattern in self._high_risk_patterns:
            if re.search(pattern, text):
                return (
                    "触发：是\n"
                    "置信度：0.95\n"
                    f"触发原因：匹配高危安全模式: {pattern}"
                )

        # 检查关键词
        hits = [kw for kw in self._safety_keywords if kw.lower() in text]
        if hits:
            confidence = min(0.9, 0.5 + len(hits) * 0.15)
            return (
                "触发：是\n"
                f"置信度：{confidence}\n"
                f"触发原因：检测到安全相关关键词: {', '.join(hits[:3])}"
            )

        return (
            "触发：否\n"
            "置信度：0.0\n"
            "触发原因：未检测到安全风险"
        )


class CodeProbe(Probe):
    """代码探针 — 检测编程/技术类请求"""

    def __init__(self, model_manager=None):
        super().__init__(
            name="code",
            model_manager=model_manager,
            priority=ProbePriority.MEDIUM,
        )
        self._code_keywords = {
            "代码", "编程", "写一个", "函数", "bug", "调试",
            "python", "javascript", "java", "golang", "rust",
            "typescript", "sql", "bash", "shell",
            "算法", "数据结构", "api", "接口",
            "refactor", "重构", "测试", "test",
            "deploy", "部署", "git", "docker",
        }
        self._code_patterns = [
            r"```\w*",                              # 代码块标记
            r"(写|实现|创建|编写).*(代码|程序|函数|脚本)",
            r"(修复|解决|查找).*(bug|问题|issue|错误)",
            r"(如何|怎么).*(实现|写|用|调用)",
            r"def\s+\w+\s*\(.*\):",                  # Python 函数定义
            r"function\s+\w+\s*\(.*\)\s*\{",         # JS 函数定义
            r"class\s+\w+",                          # 类定义
            r"import\s+\w+",                         # import 语句
        ]

    @property
    def prompt(self) -> str:
        return "检测用户是否提出编程/技术问题，需要代码专家介入。"

    def _rule_based_detect(self, context: str) -> str:
        if not context:
            return "触发：否\n置信度：0.0\n触发原因：无输入"

        text = context.lower()

        # 检查正则模式
        pattern_hits = 0
        for pattern in self._code_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                pattern_hits += 1

        # 检查关键词
        keyword_hits = [kw for kw in self._code_keywords if kw.lower() in text]

        total_hits = pattern_hits + len(keyword_hits)
        if total_hits >= 3:
            return (
                "触发：是\n"
                f"置信度：{min(0.95, 0.6 + total_hits * 0.08)}\n"
                f"触发原因：检测到明确编程请求（{total_hits} 个匹配）"
            )

        if total_hits == 2:
            return (
                "触发：是\n"
                "置信度：0.65\n"
                "触发原因：检测到可能的编程请求"
            )

        if total_hits == 1:
            return (
                "触发：否\n"
                "置信度：0.35\n"
                "触发原因：存在技术相关词汇，但置信度不足"
            )

        return (
            "触发：否\n"
            "置信度：0.0\n"
            "触发原因：未检测到编程请求"
        )


class DeepAnalysisProbe(Probe):
    """深度分析探针 — 检测需要主管/深度模型介入的复杂问题"""

    def __init__(self, model_manager=None):
        super().__init__(
            name="deep_analysis",
            model_manager=model_manager,
            priority=ProbePriority.HIGH,
        )
        self._analysis_keywords = {
            "分析", "对比", "比较", "解释", "为什么",
            "原理", "机制", "本质", "根源",
            "影响", "评估", "预测", "建议",
            "方案", "策略", "规划", "设计",
            "综述", "总结", "归纳", "推导",
        }
        self._complex_patterns = [
            r"(详细|深度|全面|系统).*(分析|解释|说明|讨论)",
            r"(为什么|为何).*(这样|如此|发生|出现)",
            r"(如何).*(实现|解决|优化|改进|提升)",
            r"(影响|作用|效果).*(因素|因子|变量|参数)",
            r"(对比|比较).*(与|和|vs|versus)",
            r"(步骤|流程|过程|阶段).*",
            r"(第一|第二|第三|首先|然后|最后).*",
        ]

    @property
    def prompt(self) -> str:
        return "检测用户是否提出需要深度分析的复杂问题，需要主管模型介入。"

    def _rule_based_detect(self, context: str) -> str:
        if not context:
            return "触发：否\n置信度：0.0\n触发原因：无输入"

        text = context.lower()

        # 检查复杂模式
        pattern_hits = sum(
            1 for p in self._complex_patterns if re.search(p, text)
        )

        # 检查关键词
        keyword_hits = [
            kw for kw in self._analysis_keywords
            if kw.lower() in text
        ]

        total_hits = pattern_hits + len(keyword_hits)
        text_length = len(text)

        # 长文本 + 分析关键词 → 很可能需要深度分析
        if text_length > 200 and total_hits >= 2:
            return (
                "触发：是\n"
                f"置信度：{min(0.9, 0.5 + total_hits * 0.1)}\n"
                "触发原因：长文本包含多个分析关键词，需要深度分析"
            )

        if total_hits >= 3:
            return (
                "触发：是\n"
                f"置信度：{min(0.85, 0.5 + total_hits * 0.1)}\n"
                f"触发原因：检测到强烈分析需求（{total_hits} 个匹配）"
            )

        if total_hits == 2:
            return (
                "触发：否\n"
                "置信度：0.5\n"
                "触发原因：可能需做分析，但置信度中等"
            )

        if total_hits == 1:
            return (
                "触发：否\n"
                "置信度：0.25\n"
                "触发原因：包含分析类词汇，但不足以触发"
            )

        return (
            "触发：否\n"
            "置信度：0.0\n"
            "触发原因：未检测到分析需求"
        )


class SearchProbe(Probe):
    """搜索探针 — 检测需要实时搜索/联网的需求"""

    def __init__(self, model_manager=None):
        super().__init__(
            name="search",
            model_manager=model_manager,
            priority=ProbePriority.MEDIUM,
        )
        self._search_keywords = {
            "搜索", "搜一下", "查一下", "查找", "查询",
            "最新", "今天", "现在", "当前", "实时",
            "天气", "新闻", "股价", "汇率", "油价",
            "search", "find", "look up", "current",
            "weather", "news", "stock", "price",
        }
        self._search_patterns = [
            r"(搜索|查).*(什么|如何|哪里|谁|多少)",
            r"(最新|最近|今天|昨日|本月).*(新闻|消息|动态|情况|价格)",
            r"(天气|气温|温度|湿度|PM).*",
            r"(股票|股价|基金|指数|行情).*",
            r"(美元|人民币|欧元|汇率).*",
        ]

    @property
    def prompt(self) -> str:
        return "检测用户是否需要联网搜索实时信息，需要搜索专家介入。"

    def _rule_based_detect(self, context: str) -> str:
        if not context:
            return "触发：否\n置信度：0.0\n触发原因：无输入"

        text = context.lower()

        # 检查搜索模式
        pattern_hits = sum(
            1 for p in self._search_patterns if re.search(p, text)
        )

        # 检查关键词
        keyword_hits = [
            kw for kw in self._search_keywords
            if kw.lower() in text
        ]

        total_hits = pattern_hits + len(keyword_hits)

        if total_hits >= 3:
            return (
                "触发：是\n"
                f"置信度：{min(0.9, 0.5 + total_hits * 0.1)}\n"
                f"触发原因：检测到强烈搜索意图（{total_hits} 个匹配）"
            )

        if total_hits == 2:
            return (
                "触发：是\n"
                "置信度：0.65\n"
                "触发原因：检测到搜索意图"
            )

        if total_hits == 1:
            return (
                "触发：否\n"
                "置信度：0.3\n"
                "触发原因：存在搜索类词汇，但置信度不足"
            )

        return (
            "触发：否\n"
            "置信度：0.0\n"
            "触发原因：未检测到搜索意图"
        )


def register_concrete_probes(registry=None) -> None:
    """注册所有具体探针到注册器

    在系统启动时调用一次即可。
    """
    if registry is None:
        from .probe_registry import get_probe_registry
        registry = get_probe_registry()

    probes = [
        SafetyProbe(),
        CodeProbe(),
        DeepAnalysisProbe(),
        SearchProbe(),
    ]

    for probe in probes:
        try:
            registry.register(probe)
        except Exception as e:
            logger = setup_logger("probe_registration")
            logger.error(f"注册探针失败 {probe.name}: {e}")
