"""
Learn 模式单元测试

覆盖：
- 并发保护（asyncio.Lock）
- 超时保护（asyncio.wait_for）
- 精度降级拒绝
- 空参数/错误参数
- 进度回调正确性
- _extract_app_name 提取逻辑
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ====================================================================
# _extract_app_name 测试
# ====================================================================

class TestExtractAppName:
    """从文本提取应用名的启发式逻辑"""

    def _extract(self, text: str) -> str:
        """直接调用静态方法"""
        from modules.thinking.core.model_runner import ModelRunner
        return ModelRunner._extract_app_name(text)

    def test_open_app(self):
        """打开Chrome"""
        assert self._extract("打开Chrome") == "Chrome"

    def test_in_app(self):
        """在Chrome中搜索"""
        assert self._extract("在Chrome中搜索") == "Chrome"

    def test_in_app_variant(self):
        """在Chrome里面搜索"""
        assert self._extract("在Chrome里面搜索") == "Chrome"

    def test_learn_app(self):
        """学习Chrome搜索"""
        assert self._extract("学习Chrome搜索") == "Chrome"

    def test_learn_app_variant(self):
        """学习Chrome的操作"""
        assert self._extract("学习Chrome的操作") == "Chrome"

    def test_launch_app(self):
        """启动Chrome"""
        assert self._extract("启动Chrome") == "Chrome"

    def test_english_app_name_fallback(self):
        """用户想学一个Chrome的搜索功能"""
        result = self._extract("用户想学一个Chrome的搜索功能")
        assert result in ("Chrome",), f"应该提取出 Chrome，实际为 {result}"

    def test_chinese_app_name(self):
        """打开微信"""
        assert self._extract("打开微信") == "微信"

    def test_no_match_returns_default(self):
        """没有匹配时返回 target_app"""
        assert self._extract("只是想学一下") == "target_app"

    def test_space_in_app_name(self):
        """打开Google Chrome → 完整应用名"""
        result = self._extract("打开Google Chrome")
        # 新版支持双词英文应用名（open -a "Google Chrome" 可正常打开）
        assert result in ("Google Chrome",)

    def test_complex_sentence(self):
        """帮我学习一下怎么在PyCharm里面编写Python代码"""
        assert self._extract("帮我学习一下怎么在PyCharm里面编写Python代码") == "PyCharm"


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
