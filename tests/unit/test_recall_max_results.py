"""测试 get_recall_max_results 动态检索量功能"""
import pytest
from unittest.mock import MagicMock, patch


def test_get_recall_max_results_low_importance():
    """低重要性输入 → 返回 LOW 检索量"""
    from modules.attention.analyzer import get_recall_max_results

    with patch("modules.attention.analyzer.create_attention_analyzer") as mock_create:
        mock_analyzer = MagicMock()
        mock_result = MagicMock()
        mock_result.importance_score = 0.3  # 低重要性
        mock_analyzer.analyze.return_value = mock_result
        mock_create.return_value = mock_analyzer

        result = get_recall_max_results("今天天气不错")
        assert result == 5, f"期望 5，得到 {result}"


def test_get_recall_max_results_medium_importance():
    """中重要性输入 → 返回 MEDIUM 检索量"""
    from modules.attention.analyzer import get_recall_max_results

    with patch("modules.attention.analyzer.create_attention_analyzer") as mock_create:
        mock_analyzer = MagicMock()
        mock_result = MagicMock()
        mock_result.importance_score = 0.5  # 中重要性
        mock_analyzer.analyze.return_value = mock_result
        mock_create.return_value = mock_analyzer

        result = get_recall_max_results("帮我查一下文档")
        assert result == 10, f"期望 10，得到 {result}"


def test_get_recall_max_results_high_importance():
    """高重要性输入 → 返回 HIGH 检索量"""
    from modules.attention.analyzer import get_recall_max_results

    with patch("modules.attention.analyzer.create_attention_analyzer") as mock_create:
        mock_analyzer = MagicMock()
        mock_result = MagicMock()
        mock_result.importance_score = 0.8  # 高重要性
        mock_analyzer.analyze.return_value = mock_result
        mock_create.return_value = mock_analyzer

        result = get_recall_max_results("紧急故障！系统崩溃！")
        assert result == 20, f"期望 20，得到 {result}"


def test_get_recall_max_results_threshold_boundary():
    """边界值测试：0.4 和 0.7"""
    from modules.attention.analyzer import get_recall_max_results

    # 测试 0.4 边界（应该返回 MEDIUM）
    with patch("modules.attention.analyzer.create_attention_analyzer") as mock_create:
        mock_analyzer = MagicMock()
        mock_result = MagicMock()
        mock_result.importance_score = 0.4
        mock_analyzer.analyze.return_value = mock_result
        mock_create.return_value = mock_analyzer

        result = get_recall_max_results("测试边界")
        assert result == 10, f"0.4 期望 10，得到 {result}"

    # 测试 0.7 边界（应该返回 HIGH）
    with patch("modules.attention.analyzer.create_attention_analyzer") as mock_create:
        mock_analyzer = MagicMock()
        mock_result = MagicMock()
        mock_result.importance_score = 0.7
        mock_analyzer.analyze.return_value = mock_result
        mock_create.return_value = mock_analyzer

        result = get_recall_max_results("测试边界")
        assert result == 20, f"0.7 期望 20，得到 {result}"


def test_get_recall_max_results_exception_fallback():
    """异常时降级到默认值 10"""
    from modules.attention.analyzer import get_recall_max_results

    with patch("modules.attention.analyzer.create_attention_analyzer") as mock_create:
        mock_create.side_effect = Exception("测试异常")

        result = get_recall_max_results("测试")
        assert result == 10, f"异常时应返回默认值 10，得到 {result}"


def test_get_recall_max_results_empty_input():
    """空输入处理"""
    from modules.attention.analyzer import get_recall_max_results

    result = get_recall_max_results("")
    # 空输入应该返回低重要性对应的值
    assert result == 5, f"空输入期望 5，得到 {result}"


def test_real_analyzer_scenarios():
    """真实分析器测试不同场景"""
    from modules.attention.analyzer import get_recall_max_results

    scenarios = [
        # (输入, 期望检索量, 说明)
        ("紧急故障！系统崩溃！", 20, "高紧急"),
        ("立刻修复这个 bug", 20, "高紧急"),
        ("帮我查一下文档", 10, "普通任务"),
        ("实现一个新的功能", 10, "任务"),
        ("今天天气不错", 10, "闲聊（基准分 0.5）"),
        ("？", 10, "问号匹配查询关键词，得 0.55 分"),
        ("", 5, "空输入"),
    ]

    for text, expected, note in scenarios:
        result = get_recall_max_results(text)
        print(f"  '{text}' → {result} (期望 {expected}) [{note}]")
        assert result == expected, f"'{text}' 期望 {expected}，得到 {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
