"""chat_light 测试（此前 16% 覆盖）：话题切换判断、黑板获取"""
from modules.thinking.chat_light.continuous_thinker import ContinuousThinker


def test_is_new_topic_overlap():
    history = [{"role": "user", "content": "写排序算法"}]
    assert ContinuousThinker._is_new_topic("写排序算法", history) is False  # 完全相同 → 同话题
    assert ContinuousThinker._is_new_topic("今天天气怎么样", history) is True  # 新话题


def test_is_new_topic_empty_history():
    assert ContinuousThinker._is_new_topic("你好", []) is False
    assert ContinuousThinker._is_new_topic("你好", [{"role": "assistant", "content": "回复"}]) is False


def test_is_new_topic_no_overlap_words():
    history = [{"role": "user", "content": "a b c"}]
    # 无中文词 → 返回 False（无法判断）
    assert ContinuousThinker._is_new_topic("x y z", history) is False
