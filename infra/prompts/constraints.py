"""
防复读约束规则模块
专门针对小模型的重复输出问题
"""
from typing import List, Set, Optional
import re


class AntiRepetitionConstraints:
    """防复读约束 - 可配置的反复读规则"""

    DEFAULT_STOP_WORDS = {
        "请输出", "信息不足", "无法确定", "请稍后",
        "请注意", "需要更多信息", "无法判断"
    }

    DEFAULT_REPEAT_PATTERNS = [
        r"^请.+[。\n]",  # 以"请"开头的句子
        r".+[。\n]{2,}",  # 连续断句
        r"(.+)\1{2,}",  # 重复词组
    ]

    def __init__(
        self,
        max_repeat_count: int = 2,
        max_output_chars: int = 50,
        stop_words: Optional[Set[str]] = None,
        forbid_patterns: Optional[List[str]] = None
    ):
        self.max_repeat_count = max_repeat_count
        self.max_output_chars = max_output_chars
        self.stop_words = stop_words or self.DEFAULT_STOP_WORDS.copy()
        self.forbid_patterns = forbid_patterns or self.DEFAULT_REPEAT_PATTERNS.copy()
        self._compiled_patterns = [re.compile(p) for p in self.forbid_patterns]

    def check(self, output: str) -> "RepetitionCheckResult":
        """
        检查输出是否违反约束

        Returns:
            RepetitionCheckResult: 包含违规信息和是否通过
        """
        result = RepetitionCheckResult(passed=True)

        output = output.strip()
        if not output:
            result.passed = False
            result.violations.append("空输出")
            return result

        if len(output) > self.max_output_chars:
            result.violations.append(f"输出过长: {len(output)} > {self.max_output_chars}")

        for word in self.stop_words:
            count = output.count(word)
            if count > self.max_repeat_count:
                result.violations.append(f"违禁词重复: '{word}' 出现 {count} 次")
                result.passed = False

        for pattern in self._compiled_patterns:
            if pattern.search(output):
                result.violations.append(f"匹配违禁模式: {pattern.pattern}")
                result.passed = False

        ngrams = self._get_ngrams(output, 3)
        for ngram, count in ngrams.items():
            if count > self.max_repeat_count:
                result.violations.append(f"N-gram 重复: '{ngram}' 出现 {count} 次")
                result.passed = False

        return result

    def _get_ngrams(self, text: str, n: int = 3) -> dict:
        """获取 N-gram 及其出现次数"""
        words = text.split()
        ngrams = {}
        for i in range(len(words) - n + 1):
            ngram = " ".join(words[i:i+n])
            ngrams[ngram] = ngrams.get(ngram, 0) + 1
        return ngrams

    def generate_constraint_text(self) -> str:
        """生成约束文本"""
        return f"""## 防复读约束
- 最大输出长度: {self.max_output_chars} 字符
- 违禁词: {", ".join(list(self.stop_words)[:5])}...
- 禁止模式: 重复句式、连续断句、违禁词"""


class RepetitionCheckResult:
    """复读检查结果"""

    def __init__(self, passed: bool = True):
        self.passed = passed
        self.violations: List[str] = []

    def __bool__(self):
        return self.passed

    def __repr__(self):
        if self.passed:
            return "RepetitionCheckResult(passed=True)"
        return f"RepetitionCheckResult(passed=False, violations={self.violations})"
