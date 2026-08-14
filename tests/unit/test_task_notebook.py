"""TaskNotebook 测试（此前 0% 覆盖）：任务记事本"""
from modules.memory.utils.task_notebook import TaskNotebook


def test_init_and_content():
    nb = TaskNotebook(session_id="s1")
    assert "任务刚开始" in nb.content  # 空时显示默认提示
    nb.update("第一步")
    assert "第一步" in nb.content
    assert nb.is_finished is False


def test_update_replaces_last():
    nb = TaskNotebook()
    nb.update("第一步")
    nb.update("第二步")
    assert "第一步" not in nb.content
    assert "第二步" in nb.content


def test_append_dedup_and_limit():
    nb = TaskNotebook()
    nb.append("a")
    nb.append("a")  # 去重
    assert nb.get_status()["entries"] == 1
    nb.append("b", is_finished=True)
    assert nb.get_status()["entries"] == 2
    assert nb.is_finished is True


def test_append_empty_ignored():
    nb = TaskNotebook()
    nb.append("")
    nb.append("   ")
    assert nb.get_status()["entries"] == 0


def test_clear():
    nb = TaskNotebook()
    nb.append("a")
    nb.clear()
    assert "任务刚开始" in nb.content
    assert nb.get_status()["entries"] == 0


def test_append_truncates_max_entries():
    nb = TaskNotebook()
    for i in range(25):
        nb.append(f"条目{i}")
    status = nb.get_status()
    assert status["entries"] == 20
    assert "条目24" in status["content"]
    assert "条目0" not in status["content"]
