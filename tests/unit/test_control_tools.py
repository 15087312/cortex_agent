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
    assert ct.QUERY_DELEGATION_TOOL["function"]["name"] in names
    assert ct.RESUME_DELEGATION_TOOL["function"]["name"] in names


def test_get_control_tools_supervisor_has_delegation_tools():
    """主管可查看/继续委托（有委托能力时）"""
    ctrl = get_tool_permission_controller()
    tools = ctrl.get_control_tools(tier="supervisor", mode="edit", delegation_available=True)
    names = [t["function"]["name"] for t in tools]
    assert ct.QUERY_DELEGATION_TOOL["function"]["name"] in names
    assert ct.RESUME_DELEGATION_TOOL["function"]["name"] in names


def test_get_control_tools_expert_no_delegation_tools():
    """专家无委托工具（含查看/继续）"""
    ctrl = get_tool_permission_controller()
    tools = ctrl.get_control_tools(tier="expert", mode="edit", delegation_available=False)
    names = [t["function"]["name"] for t in tools]
    assert ct.QUERY_DELEGATION_TOOL["function"]["name"] not in names
    assert ct.RESUME_DELEGATION_TOOL["function"]["name"] not in names


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


def test_delegate_task_requires_wait_seconds():
    """委托必须指定下级思考超时：wait_seconds 为必填参数"""
    props = ct.DELEGATE_TASK_TOOL["function"]["parameters"]
    required = props["required"]
    assert "wait_seconds" in required
    assert "role" in required and "task" in required
    desc = props["properties"]["wait_seconds"]["description"]
    assert "超时" in desc


# ── inspect_delegation：查看委托链具体执行过程（总指挥/主管）──

def test_inspect_delegation_registered_for_large_supervisor():
    ctrl = get_tool_permission_controller()
    for tier in ("large", "supervisor"):
        tools = ctrl.get_control_tools(tier=tier, mode="edit", delegation_available=True)
        names = [t["function"]["name"] for t in tools]
        assert "inspect_delegation" in names, f"{tier} 应可调用 inspect_delegation"


def test_inspect_delegation_not_for_expert():
    ctrl = get_tool_permission_controller()
    tools = ctrl.get_control_tools(tier="expert", mode="edit", delegation_available=True)
    names = [t["function"]["name"] for t in tools]
    assert "inspect_delegation" not in names, "专家不应有 inspect_delegation（无委托权限）"


def test_inspect_delegation_schema_requires_id():
    props = ct.INSPECT_DELEGATION_TOOL["function"]["parameters"]
    assert "delegation_id" in props["required"]
    assert "limit" in props["properties"]
    assert "max_len" in props["properties"]
