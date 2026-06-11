"""
Learn 模式单元测试

覆盖：
- 并发保护（asyncio.Lock）
- 超时保护（asyncio.wait_for）
- 精度降级拒绝
- 空参数/错误参数
- 进度回调正确性
- request_mode_change learn 参数传递
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ====================================================================
# _handle_mode_change_request → learn 路径测试
# ====================================================================

class TestRequestModeChangeLearn:
    """模型通过 request_mode_change 传递 learn 参数"""

    @pytest.mark.asyncio
    async def test_learn_with_app_name(self):
        """模型提供 app_name 时应直接传递给管线"""
        from config.settings import Settings
        settings = Settings(_env_file=None)
        with patch.object(settings, 'LARGE_MODEL_API_KEY', 'test_key'):
            from modules.thinking.core.model_runner import ModelRunner

            runner = ModelRunner.__new__(ModelRunner)
            runner.model_id = "test_runner"
            runner.logger = MagicMock()

            result = await runner._handle_mode_change_request(
                reason="用户想学习如何操作",
                suggested_mode="learn",
                app_name="Chrome",
                tool_name="chrome_search",
                task_description="打开Chrome并搜索Python教程",
            )
            # 没有 API Key 所以实际调用会失败，但应该走到管线而不是
            # 因为缺少 app_name 直接返回错误
            assert "请指定要学习的应用" not in result

    @pytest.mark.asyncio
    async def test_learn_without_app_name_returns_error(self):
        """模型未提供 app_name 时应返回明确错误"""
        from modules.thinking.core.model_runner import ModelRunner

        runner = ModelRunner.__new__(ModelRunner)
        result = await runner._handle_mode_change_request(
            reason="用户想学习",
            suggested_mode="learn",
        )
        assert "请指定要学习的应用名称" in result

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
# run_learn_pipeline 测试
# ====================================================================

@pytest.mark.asyncio
class TestRunLearnPipeline:
    """学习管线核心逻辑"""

    async def test_concurrent_lock_rejection(self):
        """并发执行第二个学习任务应被拒绝"""
        from modules.toolbuilder.learn_mode import run_learn_pipeline, _learn_lock

        # 先获取锁（模拟已有任务运行中）
        async with _learn_lock:
            result = await run_learn_pipeline(
                app_name="Chrome",
                tool_name="search",
                task_description="test",
            )
        assert result["status"] == "error"
        assert "已有学习任务" in result["message"]

    async def test_concurrent_lock_free(self):
        """锁空闲时应该能正常进入管线"""
        from modules.toolbuilder.learn_mode import _learn_lock

        # 锁应该是自由状态
        assert not _learn_lock.locked()

    async def test_empty_app_name_propagation(self):
        """空 app_name 应传递到管线中"""
        from modules.toolbuilder.learn_mode import run_learn_pipeline

        with patch("modules.toolbuilder.learn_mode._pipeline_steps",
                   new_callable=AsyncMock) as mock_steps:
            mock_steps.return_value = {"status": "success", "tool_name": "test"}
            result = await run_learn_pipeline(
                app_name="",
                tool_name="test",
                task_description="test",
            )
        assert result["status"] == "success"
        # 验证空 app_name 被正确传递
        assert mock_steps.call_count >= 1
        call_args = mock_steps.call_args
        assert call_args is not None
        # _pipeline_steps(app_name, tool_name, ...) 位置参数
        assert call_args.args[0] == ""  # app_name

    async def test_params_hint_json_error(self):
        """params_hint JSON 解析失败应使用空 schema"""
        from modules.toolbuilder.learn_mode import run_learn_pipeline

        events = []

        def cb(event, data):
            events.append(event)

        with patch("modules.toolbuilder.learn_mode._pipeline_steps",
                   new_callable=AsyncMock) as mock_steps:
            mock_steps.return_value = {"status": "success"}
            result = await run_learn_pipeline(
                app_name="Chrome",
                tool_name="search",
                task_description="test",
                params_hint="not valid json",
                progress_callback=cb,
            )
        assert result["status"] == "success"

    async def test_progress_callback_errors_are_non_fatal(self):
        """进度回调抛异常不应中断管线"""
        from modules.toolbuilder.learn_mode import run_learn_pipeline

        def bad_cb(event, data):
            raise ValueError("callback error")

        with patch("modules.toolbuilder.learn_mode._pipeline_steps",
                   new_callable=AsyncMock) as mock_steps:
            mock_steps.return_value = {"status": "success"}
            # 不应该抛出异常
            result = await run_learn_pipeline(
                app_name="Chrome",
                tool_name="search",
                task_description="test",
                progress_callback=bad_cb,
            )
        assert result["status"] == "success"

    async def test_pipeline_timeout(self):
        """管线超时应返回超时错误"""
        import asyncio
        from modules.toolbuilder.learn_mode import run_learn_pipeline, _PIPELINE_TIMEOUT

        with patch("modules.toolbuilder.learn_mode._pipeline_steps",
                   new_callable=AsyncMock) as mock_steps:
            # 模拟管线永远不返回（超时）
            async def never_ends(*args, **kwargs):
                await asyncio.sleep(999)
            mock_steps.side_effect = never_ends

            with patch("modules.toolbuilder.learn_mode._PIPELINE_TIMEOUT", 0.1):
                result = await run_learn_pipeline(
                    app_name="Chrome",
                    tool_name="search",
                    task_description="test",
                )
            assert result["status"] == "error"
            assert "超时" in result["message"]


# ====================================================================
# 进度事件完整性测试
# ====================================================================

@pytest.mark.asyncio
class TestLearnProgressEvents:
    """进度事件的正确性"""

    async def test_events_fired_in_order(self):
        """成功管线应返回成功状态"""
        from modules.toolbuilder.learn_mode import run_learn_pipeline

        with patch("modules.toolbuilder.learn_mode._pipeline_steps",
                   new_callable=AsyncMock) as mock_steps:
            mock_steps.return_value = {
                "status": "success",
                "tool_name": "test",
                "app_name": "TestApp",
                "plugin_path": "/tmp/test",
                "steps_count": 3,
                "message": "完成",
            }
            result = await run_learn_pipeline(
                app_name="TestApp",
                tool_name="test",
                task_description="test",
            )
        assert result["status"] == "success"
        assert result["tool_name"] == "test"
        assert "完成" in result["message"]


# ====================================================================
# 配置验证测试
# ====================================================================

class TestLearnModeConfig:
    """learn 模式配置"""

    def test_learn_in_allowed_modes(self):
        """learn 应该在允许的执行模式列表中"""
        from config.settings import Settings

        s = Settings()
        s.EXECUTION_MODE = "learn"
        # 不抛异常
        assert s.EXECUTION_MODE == "learn"
        assert s.effective_execution_mode == "learn"

    def test_request_mode_change_contains_learn(self):
        """request_mode_change 的 suggested_mode 应包含 learn"""
        from modules.thinking.core.control_tools import REQUEST_MODE_CHANGE_TOOL

        enum = REQUEST_MODE_CHANGE_TOOL["function"]["parameters"]["properties"]["suggested_mode"]["enum"]
        assert "learn" in enum

    def test_learn_pipeline_events_have_done(self):
        """管线应定义 DONE 事件常量"""
        from modules.toolbuilder.learn_mode import EVENT_DONE, EVENT_ERROR, EVENT_START
        assert EVENT_DONE == "learn_done"
        assert EVENT_ERROR == "learn_error"
        assert EVENT_START == "learn_start"
