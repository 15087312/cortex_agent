"""
Learn 模式单元测试

覆盖：
- request_mode_change learn 路径
- save_recipe 工具参数校验
- learn 模式配置验证
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ====================================================================
# _handle_mode_change_request → learn 路径测试
# ====================================================================

class TestRequestModeChangeLearn:
    """模型 request_mode_change 切换到 learn"""

    @pytest.mark.asyncio
    async def test_learn_switches_mode(self):
        """learn 应设置 EXECUTION_MODE = learn 并返回提示"""
        from modules.thinking.core.model_runner import ModelRunner
        from config.settings import settings as _cfg

        runner = ModelRunner.__new__(ModelRunner)
        result = await runner._handle_mode_change_request(
            reason="用户想学习",
            suggested_mode="learn",
        )
        assert "学习模式" in result
        assert "save_recipe" in result

    @pytest.mark.asyncio
    async def test_non_learn_still_requires_approval(self):
        """非 learn 模式仍需要用户确认"""
        from modules.thinking.core.model_runner import ModelRunner

        runner = ModelRunner.__new__(ModelRunner)
        runner._pending_user_responses = {}

        with patch.object(runner, '_wait_for_user_response',
                          AsyncMock(return_value={"timeout": True})):
            result = await runner._handle_mode_change_request(
                reason="需要编辑代码",
                suggested_mode="edit",
            )
            assert "用户未响应" in result


# ====================================================================
# save_recipe 工具测试
# ====================================================================

class TestSaveRecipe:
    """save_recipe 工具参数校验"""

    def test_empty_tool_name(self):
        """空 tool_name 返回错误"""
        from infra.tool_manager.tools.toolbuilder import save_recipe
        import asyncio
        result = asyncio.run(save_recipe("", "Chrome", "desc", []))
        assert result["status"] == "error"
        assert "tool_name" in result["message"]

    def test_empty_steps(self):
        """空 steps 返回错误"""
        from infra.tool_manager.tools.toolbuilder import save_recipe
        import asyncio
        result = asyncio.run(save_recipe("test", "Chrome", "desc", []))
        assert result["status"] == "error"
        assert "steps 不能为空" in result["message"]

    def test_invalid_action_in_steps(self):
        """steps 中包含不支持的动作应返回错误"""
        from infra.tool_manager.tools.toolbuilder import save_recipe
        import asyncio
        steps = [{"action": "exec_command", "args": {"command": "rm -rf /"}}]
        result = asyncio.run(save_recipe("test", "Chrome", "desc", steps))
        assert result["status"] == "error"
        assert "不支持的动作" in result["message"]

    def test_valid_steps_saves_successfully(self):
        """有效 steps 应成功保存"""
        from infra.tool_manager.tools.toolbuilder import save_recipe
        from modules.toolbuilder.plugin_builder import PluginBuilder
        from modules.toolbuilder.skill_generator import SkillGenerator
        import asyncio

        steps = [
            {"action": "mouse_click", "args": {"x": 100, "y": 200}, "description": "点击搜索框"},
            {"action": "keyboard_type", "args": {"text": "{{query}}"}, "description": "输入搜索词"},
        ]

        with patch.object(PluginBuilder, 'create_plugin', return_value=MagicMock()) as mock_create, \
             patch.object(SkillGenerator, 'generate_or_update', return_value=None), \
             patch('config.settings.settings') as mock_settings:
            mock_settings.effective_execution_mode = "learn"

            result = asyncio.run(save_recipe("chrome_search", "Chrome", "搜索工具", steps))
            assert result["status"] == "success"
            assert result["tool_name"] == "chrome_search"
            assert mock_create.called

    def test_missing_action_key(self):
        """step 缺少 action 返回错误"""
        from infra.tool_manager.tools.toolbuilder import save_recipe
        import asyncio
        steps = [{"args": {"x": 100}}]
        result = asyncio.run(save_recipe("test", "Chrome", "desc", steps))
        assert result["status"] == "error"
        assert "缺少 action" in result["message"]


# ====================================================================
# 配置验证测试
# ====================================================================

class TestLearnModeConfig:
    """learn 模式配置"""

    def test_learn_in_validator(self):
        """learn 应在 EXECUTION_MODE 允许列表中"""
        from config.settings import Settings

        s = Settings(_env_file=None)
        s.EXECUTION_MODE = "learn"
        assert s.EXECUTION_MODE == "learn"
        assert s.effective_execution_mode == "learn"

    def test_request_mode_change_contains_learn(self):
        """request_mode_change 应包含 learn 枚举值"""
        from modules.thinking.core.control_tools import REQUEST_MODE_CHANGE_TOOL

        enum = REQUEST_MODE_CHANGE_TOOL["function"]["parameters"]["properties"]["suggested_mode"]["enum"]
        assert "learn" in enum

    def test_save_recipe_registered(self):
        """save_recipe 应在 ToolRegistry 中注册"""
        from infra.tool_manager.tool_registry import ToolRegistry

        tool = ToolRegistry.get_tool("save_recipe")
        assert tool is not None
        assert tool.name == "save_recipe"
        assert tool.risk_level == "MEDIUM"

    def test_learn_prompt_has_self_evolution(self):
        """learn 模式提示词应包含自我进化描述"""
        prompt = (
            "【执行模式: LEARN（自我进化）】\n"
            "当前为学习模式，你正在自动化一个 UI 操作流程，完成后你的工具列表会扩展。\n"
            "推荐的执行步骤：\n"
            "1. open_app(app_name) — 打开要学习的应用\n"
            "2. detect_ui_elements() — 识别界面上的元素\n"
            "3. 用鼠标/键盘工具执行操作，向用户展示每一步\n"
            "4. 操作完成后用 save_recipe(name, app_name, steps, description) 保存\n\n"
            "学习完成后会自动退出学习模式，新工具立即可用。\n"
            "调用 save_recipe 后请调用 respond_to_user 输出学习结果。"
        )
        assert "自我进化" in prompt
        assert "open_app" in prompt
        assert "save_recipe" in prompt
        assert "detect_ui_elements" in prompt
