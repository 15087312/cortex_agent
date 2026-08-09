"""控制工具测试（此前 0% 覆盖）：工具定义 + 权限可见性"""
from modules.thinking.core import control_tools as ct
from modules.security_system.tool_permission_controller import get_tool_permission_controller


def test_control_tool_definitions():
    """每个控制工具都有 name 与 description"""
    tools = [
        ct.CONTINUE_THINKING_TOOL, ct.DELEGATE_TASK_TOOL, ct.STOP_TASK_TOOL,
        ct.CREATE_SUPERVISOR_TOOL, ct.RESPOND_TO_USER_TOOL, ct.REQUEST_SKILL_TOOL,
        ct.STOP_SKILL_TOOL, ct.LIST_SKILLS_TOOL, ct.QUERY_TOOL_DETAILS_TOOL,
        ct.REQUEST_MODE_CHANGE_TOOL, ct.SET_MEMORY_FOCUS_TOOL, ct.ASK_USER_INTENT_TOOL,
    ]
    for t in tools:
        assert t["function"]["name"]
        assert t["function"]["description"]


def test_get_control_tools_large():
    ctrl = get_tool_permission_controller()
    tools = ctrl.get_control_tools(tier="large", mode="edit", delegation_available=True)
    names = [t["function"]["name"] for t in tools]
    assert ct.DELEGATE_TASK_TOOL["function"]["name"] in names
    assert ct.REQUEST_SKILL_TOOL["function"]["name"] in names
    assert ct.LIST_SKILLS_TOOL["function"]["name"] in names
    assert ct.STOP_SKILL_TOOL["function"]["name"] in names
    assert ct.REQUEST_MODE_CHANGE_TOOL["function"]["name"] in names


def test_get_control_tools_supervisor_no_skill():
    ctrl = get_tool_permission_controller()
    tools = ctrl.get_control_tools(tier="supervisor", mode="edit", delegation_available=True)
    # 主管有委托，但无技能/模式切换控制工具
    names = [t["function"]["name"] for t in tools]
    assert ct.DELEGATE_TASK_TOOL["function"]["name"] in names
    assert ct.REQUEST_SKILL_TOOL["function"]["name"] not in names
    assert ct.REQUEST_MODE_CHANGE_TOOL["function"]["name"] not in names


def test_get_control_tools_expert():
    ctrl = get_tool_permission_controller()
    tools = ctrl.get_control_tools(tier="expert", mode="edit", delegation_available=False)
    # 专家无委托，只有基础
    names = [t["function"]["name"] for t in tools]
    assert ct.DELEGATE_TASK_TOOL["function"]["name"] not in names
