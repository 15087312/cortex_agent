"""thinking/context/manager 测试（此前 21% 覆盖）：外部引导与委托状态格式化"""
from modules.thinking.context.manager import ContextManager


def test_build_external_guidance_empty():
    assert ContextManager.build_external_guidance([], []) == ""


def test_build_external_guidance_persistent_only():
    out = ContextManager.build_external_guidance(["简报A"], [])
    assert "[系统简报 #1]" in out
    assert "简报A" in out


def test_build_external_guidance_transient_only():
    out = ContextManager.build_external_guidance([], ["本轮提示"])
    assert "[本轮提示 #1]" in out
    assert "本轮提示" in out


def test_build_external_guidance_limit():
    out = ContextManager.build_external_guidance(list(range(10)), list(range(5)))
    assert "9" in out          # 保留持久简报最后一条内容
    assert "4" in out          # 保留本轮提示最后一条内容
    assert "#1]\n0" not in out  # 最早的持久简报被裁剪
    assert "#1]\n1" not in out  # 最早的本轮提示被裁剪


def test_build_delegation_status_empty():
    assert ContextManager.build_delegation_status({}) == ""


def test_build_delegation_status_pending():
    d = {"a": {"status": "pending", "round": 1, "role": "expert", "task": "重构模块"}, "b": {"status": "done", "round": 2, "role": "expert", "task": "测试"}}
    out = ContextManager.build_delegation_status(d)
    assert "当前委托状态" in out
    assert "等待专家回复" in out


def test_build_delegation_status_all_done():
    d = {"a": {"status": "done", "round": 1, "role": "expert", "task": "完成"}}
    out = ContextManager.build_delegation_status(d)
    assert "当前委托状态" in out
    assert "等待专家回复" not in out
