"""ToolBuilder 系统单元测试

覆盖: RecipeEngine, PluginBuilder, ActionPlanner, SkillGenerator,
      OmniParserDetector, screen_capture, toolbuilder tools
"""
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml


# ====================================================================
# RecipeEngine
# ====================================================================

class TestRecipeEngine:
    """RecipeEngine 核心功能测试"""

    def setup_method(self):
        """每个测试前创建临时目录"""
        self._tmp_dir = tempfile.mkdtemp()
        self._original_root = None

    def teardown_method(self):
        """每个测试后清理"""
        shutil.rmtree(self._tmp_dir, ignore_errors=True)
        # 重置全局 root
        import modules.toolbuilder.recipe_engine as mod
        mod._LEARNED_TOOLS_ROOT = None

    def _patch_root(self):
        import modules.toolbuilder.recipe_engine as mod
        mod._LEARNED_TOOLS_ROOT = Path(self._tmp_dir)
        return mod

    def test_sanitize_name(self):
        from modules.toolbuilder.recipe_engine import sanitize_name
        assert sanitize_name("Chrome") == "chrome"
        assert sanitize_name("My Tool!!!") == "my_tool"
        assert sanitize_name("test-123") == "test_123"
        assert sanitize_name("") == "unnamed"
        assert sanitize_name("___") == "unnamed"
        assert sanitize_name("a  b") == "a_b"

    def test_sanitize_name_backward_compat(self):
        from modules.toolbuilder.recipe_engine import _sanitize_name
        assert _sanitize_name("Chrome") == "chrome"

    def test_plugin_dir_name(self):
        from modules.toolbuilder.recipe_engine import _plugin_dir_name
        assert _plugin_dir_name("chrome_search", "Chrome") == "learned_chrome_chrome_search"

    def test_save_and_load(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine

        steps = [{"step_id": 1, "action": "test", "args": {"k": "v"}, "wait_after_ms": 0}]
        params = {"query": {"type": "string", "required": True}}

        path = RecipeEngine.save("my_tool", "MyApp", steps, params, "测试")
        assert path.exists()
        assert (path / "recipe.json").exists()
        assert (path / "meta.json").exists()

        recipe = RecipeEngine.load("my_tool", "MyApp")
        assert recipe is not None
        assert recipe["tool_name"] == "my_tool"
        assert recipe["app_name"] == "MyApp"
        assert len(recipe["steps"]) == 1
        assert "query" in recipe["params"]

    def test_load_search_mode(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine

        RecipeEngine.save("search_tool", "App", [{"step_id": 1, "action": "a", "args": {}, "wait_after_ms": 0}], {})
        recipe = RecipeEngine.load("search_tool")
        assert recipe is not None

    def test_load_not_found(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine
        assert RecipeEngine.load("nonexistent", "App") is None
        assert RecipeEngine.load("nonexistent") is None

    def test_delete(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine

        RecipeEngine.save("del_tool", "App", [], {})
        assert RecipeEngine.delete("del_tool", "App") is True
        assert RecipeEngine.load("del_tool", "App") is None
        assert RecipeEngine.delete("del_tool", "App") is False

    def test_list_all(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine

        RecipeEngine.save("tool_a", "AppA", [], {}, "任务A")
        RecipeEngine.save("tool_b", "AppB", [], {}, "任务B")

        tools = RecipeEngine.list_all()
        assert len(tools) == 2
        names = {t["tool_name"] for t in tools}
        assert "tool_a" in names
        assert "tool_b" in names

    def test_list_all_empty(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine
        assert RecipeEngine.list_all() == []

    def test_update_stats(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine

        RecipeEngine.save("stat_tool", "App", [{"step_id": 1, "action": "a", "args": {}, "wait_after_ms": 0}], {})
        RecipeEngine.update_stats("stat_tool", True, 100.0, "App")
        RecipeEngine.update_stats("stat_tool", False, 200.0, "App")

        recipe = RecipeEngine.load("stat_tool", "App")
        stats = recipe["execution_stats"]
        assert stats["total_runs"] == 2
        assert stats["success_count"] == 1
        assert stats["failure_count"] == 1

    def test_resolve_args(self):
        from modules.toolbuilder.recipe_engine import _resolve_args
        result = _resolve_args(
            {"text": "{query}", "count": 5, "nested": {"val": "{x}"}},
            {"query": "hello", "x": "world"}
        )
        assert result["text"] == "hello"
        assert result["count"] == 5
        assert result["nested"]["val"] == "world"

    def test_resolve_args_missing_key(self):
        from modules.toolbuilder.recipe_engine import _resolve_args
        result = _resolve_args({"text": "{missing}"}, {})
        assert result["text"] == "{missing}"

    def test_resolve_args_list(self):
        from modules.toolbuilder.recipe_engine import _resolve_args
        result = _resolve_args({"keys": ["{mod}", "l"]}, {"mod": "cmd"})
        assert result["keys"] == ["cmd", "l"]

    def test_index_updated(self):
        self._patch_root()
        from modules.toolbuilder.recipe_engine import RecipeEngine

        RecipeEngine.save("idx_tool", "App", [], {}, "desc")
        index_path = Path(self._tmp_dir) / "_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert "App/idx_tool" in index["tools"]

        RecipeEngine.delete("idx_tool", "App")
        index = json.loads(index_path.read_text())
        assert "App/idx_tool" not in index["tools"]


# ====================================================================
# PluginBuilder
# ====================================================================

class TestPluginBuilder:
    """PluginBuilder 测试"""

    def setup_method(self):
        self._tmp_dir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)
        import modules.toolbuilder.recipe_engine as mod
        mod._LEARNED_TOOLS_ROOT = None

    def _patch_root(self):
        import modules.toolbuilder.recipe_engine as mod
        mod._LEARNED_TOOLS_ROOT = Path(self._tmp_dir)

    def test_create_plugin_structure(self):
        self._patch_root()
        from modules.toolbuilder.plugin_builder import PluginBuilder

        steps = [{"step_id": 1, "action": "click", "args": {"x": 100, "y": 200}, "wait_after_ms": 300}]
        params = {"query": {"type": "string", "required": True, "description": "搜索词"}}
        path = PluginBuilder.create_plugin("search", "Chrome", steps, params, "搜索")

        assert path.exists()
        assert (path / "plugin.yaml").exists()
        assert (path / "recipe.json").exists()
        assert (path / "src" / "tool_impl.py").exists()
        assert (path / "src" / "__init__.py").exists()
        assert (path / "meta.json").exists()

    def test_plugin_yaml_format(self):
        self._patch_root()
        from modules.toolbuilder.plugin_builder import PluginBuilder

        path = PluginBuilder.create_plugin("test", "App", [], {"x": {"type": "string", "required": True}})
        with open(path / "plugin.yaml") as f:
            meta = yaml.safe_load(f)

        assert meta["name"] == "learned_app_test"
        assert meta["version"] == "1.0.0"
        assert meta["runtime"]["trust"] == "official"
        assert len(meta["extensions"]) == 1
        assert meta["extensions"][0]["type"] == "tool"
        assert meta["extensions"][0]["name"] == "test"

    def test_tool_impl_content(self):
        self._patch_root()
        from modules.toolbuilder.plugin_builder import PluginBuilder

        path = PluginBuilder.create_plugin("my_tool", "MyApp", [], {"q": {"type": "string"}})
        content = (path / "src" / "tool_impl.py").read_text()
        assert "execute_my_tool" in content
        assert "**kwargs" in content
        assert "RecipeEngine.execute" in content

    def test_delete_plugin(self):
        self._patch_root()
        from modules.toolbuilder.plugin_builder import PluginBuilder

        path = PluginBuilder.create_plugin("del", "App", [], {})
        assert path.exists()
        assert PluginBuilder.delete_plugin("del", "App") is True
        assert not path.exists()


# ====================================================================
# OmniParserDetector
# ====================================================================

class TestOmniParserDetector:
    """OmniParserDetector 测试"""

    def test_ui_element_dataclass(self):
        from modules.perception.detectors.omniparser_detector import UIElement
        elem = UIElement(
            element_id="e001", type="button", label="OK",
            bbox=[10, 20, 100, 50], center_x=55, center_y=35, confidence=0.95
        )
        d = elem.to_dict()
        assert d["element_id"] == "e001"
        assert d["type"] == "button"
        assert d["label"] == "OK"
        assert d["bbox"] == [10, 20, 100, 50]

    def test_backend_fallback(self):
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        det = OmniParserDetector(api_url="http://localhost:19999")
        assert det.backend == "ocr_fallback"
        assert det.is_available()

    @patch("urllib.request.urlopen")
    def test_backend_http(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        det = OmniParserDetector(api_url="http://localhost:8000")
        assert det.backend == "omniparser_http"

    def test_ocr_fallback_elements(self):
        """OCR fallback 应该返回 UIElement 列表"""
        from modules.perception.detectors.omniparser_detector import OmniParserDetector, UIElement
        det = OmniParserDetector(api_url="http://localhost:19999")
        assert det.backend == "ocr_fallback"

        # 创建简单图片
        import numpy as np
        img = np.ones((100, 200, 3), dtype=np.uint8) * 255

        # mock _ocr_extract 返回空（无 OCR 引擎）
        det._ocr_extract = lambda img: ""
        elements = det.detect_elements(img)
        assert elements == []

    def test_detect_perception_event(self):
        """detect() 应该返回 PerceptionEvent 列表"""
        from modules.perception.detectors.omniparser_detector import OmniParserDetector, UIElement
        import numpy as np

        det = OmniParserDetector(api_url="http://localhost:19999")
        img = np.ones((100, 200, 3), dtype=np.uint8) * 255

        # mock detect_elements 返回元素
        det.detect_elements = lambda img: [
            UIElement(element_id="e001", type="text", label="Hello", bbox=[0, 0, 100, 30], center_x=50, center_y=15)
        ]
        events = det.detect(img, "screen")
        assert len(events) == 1
        assert events[0].event_type == "screen.ui"
        assert events[0].payload["element_count"] == 1

    def test_detect_no_change(self):
        """相同元素不产生事件"""
        from modules.perception.detectors.omniparser_detector import OmniParserDetector, UIElement
        import numpy as np

        det = OmniParserDetector(api_url="http://localhost:19999")
        img = np.ones((100, 200, 3), dtype=np.uint8) * 255
        elem = UIElement(element_id="e001", type="text", label="Hi", bbox=[0, 0, 50, 20], center_x=25, center_y=10)

        det.detect_elements = lambda img: [elem]
        events1 = det.detect(img, "screen")
        assert len(events1) == 1

        events2 = det.detect(img, "screen")
        assert len(events2) == 0  # 无变化

    def test_detect_bytes_input(self):
        """detect_elements 接受 bytes 输入"""
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        det = OmniParserDetector(api_url="http://localhost:19999")
        det._ocr_extract = lambda img: ""

        from PIL import Image
        import io
        img = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        elements = det.detect_elements(buf.getvalue())
        assert isinstance(elements, list)

    def test_reset(self):
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        det = OmniParserDetector(api_url="http://localhost:19999")
        det._prev_elements["test"] = ["something"]
        det.reset()
        assert len(det._prev_elements) == 0


# ====================================================================
# SkillGenerator
# ====================================================================

class TestSkillGenerator:
    """SkillGenerator 测试"""

    def setup_method(self):
        self._tmp_dir = tempfile.mkdtemp()
        self._skills_dir = Path(self._tmp_dir) / "skills" / "learned"

    def teardown_method(self):
        shutil.rmtree(self._tmp_dir, ignore_errors=True)
        import modules.toolbuilder.recipe_engine as mod
        mod._LEARNED_TOOLS_ROOT = None

    def _patch_root(self):
        import modules.toolbuilder.recipe_engine as mod
        mod._LEARNED_TOOLS_ROOT = Path(self._tmp_dir) / "plugins"
        (mod._LEARNED_TOOLS_ROOT).mkdir(parents=True, exist_ok=True)
        return mod

    def _patch_skills_dir(self):
        import modules.toolbuilder.skill_generator as mod
        mod._SKILLS_LEARNED_DIR = self._skills_dir
        self._skills_dir.mkdir(parents=True, exist_ok=True)

    def test_generate_or_update(self):
        self._patch_root()
        self._patch_skills_dir()
        from modules.toolbuilder.plugin_builder import PluginBuilder
        from modules.toolbuilder.skill_generator import SkillGenerator

        PluginBuilder.create_plugin("search", "Chrome", [{"step_id": 1, "action": "a", "args": {}, "wait_after_ms": 0}], {"q": {"type": "string"}})
        path = SkillGenerator.generate_or_update("Chrome")
        assert path is not None
        assert path.exists()

        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["id"] == "chrome_automation"
        assert len(data["rules"]) > 0
        assert len(data["workflow"]) > 0
        assert len(data["metadata"]["learned_tools"]) == 1

    def test_generate_no_tools(self):
        self._patch_root()
        self._patch_skills_dir()
        from modules.toolbuilder.skill_generator import SkillGenerator
        assert SkillGenerator.generate_or_update("EmptyApp") is None

    def test_remove_tool(self):
        self._patch_root()
        self._patch_skills_dir()
        from modules.toolbuilder.plugin_builder import PluginBuilder
        from modules.toolbuilder.skill_generator import SkillGenerator

        PluginBuilder.create_plugin("tool1", "App", [], {})
        SkillGenerator.generate_or_update("App")
        path = SkillGenerator.remove_tool("App", "tool1")
        assert path is None  # 无剩余工具，skill 删除


# ====================================================================
# ActionPlanner
# ====================================================================


# ====================================================================
# 去除的模块：ActionPlanner（模型自编排替代）
# 去除的模块：learn_mode.py（save_recipe 替代管线）
# ====================================================================


# ====================================================================
# screen_capture
# ====================================================================

class TestScreenCapture:
    """screen_capture 工具测试"""

    def test_capture_screen_no_backend(self):
        """无后端时返回 None"""
        from utils.screen_capture import capture_screen
        with patch("utils.screen_capture._try_mss", return_value=None), \
             patch("utils.screen_capture._try_imagegrab", return_value=None), \
             patch("sys.platform", "linux"):
            result = capture_screen()
            assert result is None

    def test_capture_screen_with_mss(self):
        """mss 后端成功"""
        from PIL import Image
        fake_img = Image.new("RGB", (1920, 1080), "blue")
        from utils.screen_capture import capture_screen
        with patch("utils.screen_capture._try_mss", return_value=fake_img):
            result = capture_screen(max_width=1280)
            assert result is not None
            assert len(result) > 0

    def test_capture_screen_resize(self):
        """大图应被缩放"""
        from PIL import Image
        import io, base64
        fake_img = Image.new("RGB", (2560, 1440), "red")
        from utils.screen_capture import capture_screen
        with patch("utils.screen_capture._try_mss", return_value=fake_img):
            result = capture_screen(max_width=1280)
            img_data = base64.b64decode(result)
            img = Image.open(io.BytesIO(img_data))
            assert img.width == 1280


# ====================================================================
# Toolbuilder tools (integration)
# ====================================================================

class TestToolbuilderTools:
    """Toolbuilder 工具注册测试"""

    def test_tools_registered(self):
        """4 个工具都已注册"""
        from infra.tool_manager.tool_registry import ToolRegistry
        for name in ["save_recipe", "delete_learned_tool", "list_learned_tools",
                      "execute_tool_recipe"]:
            assert ToolRegistry.get_func(name) is not None, f"{name} not registered"

    @pytest.mark.asyncio
    async def test_list_learned_tools_empty(self):
        """空目录返回空列表"""
        import modules.toolbuilder.recipe_engine as mod
        import tempfile
        tmp = tempfile.mkdtemp()
        old = mod._LEARNED_TOOLS_ROOT
        mod._LEARNED_TOOLS_ROOT = Path(tmp)

        from infra.tool_manager.tool_registry import ToolRegistry
        func = ToolRegistry.get_func("list_learned_tools")
        result = await func()
        assert result["status"] == "success"
        assert result["count"] == 0

        mod._LEARNED_TOOLS_ROOT = old
        shutil.rmtree(tmp)

    async def test_delete_nonexistent(self):
        """删除不存在的工具返回错误"""
        import modules.toolbuilder.recipe_engine as mod
        import tempfile
        tmp = tempfile.mkdtemp()
        old = mod._LEARNED_TOOLS_ROOT
        mod._LEARNED_TOOLS_ROOT = Path(tmp)

        from infra.tool_manager.tool_registry import ToolRegistry
        func = ToolRegistry.get_func("delete_learned_tool")
        result = await func(tool_name="nonexistent", app_name="App")
        assert result["status"] == "error"

        mod._LEARNED_TOOLS_ROOT = old
        shutil.rmtree(tmp)

    @pytest.mark.asyncio
    async def test_execute_nonexistent(self):
        """执行不存在的 recipe 返回错误"""
        import modules.toolbuilder.recipe_engine as mod
        import tempfile
        tmp = tempfile.mkdtemp()
        old = mod._LEARNED_TOOLS_ROOT
        mod._LEARNED_TOOLS_ROOT = Path(tmp)

        from infra.tool_manager.tool_registry import ToolRegistry
        func = ToolRegistry.get_func("execute_tool_recipe")
        result = await func(tool_name="nonexistent", params_json="{}")
        assert result["status"] == "error"

        mod._LEARNED_TOOLS_ROOT = old
        shutil.rmtree(tmp)


# ====================================================================
# SecurityGate 风险分类
# ====================================================================

class TestSecurityGateClassification:
    """安全门风险分类测试"""

    def test_delete_learned_tool_medium(self):
        from infra.tool_manager.tool_registry import ToolRegistry
        medium_risk = ToolRegistry.get_tools_by_risk("MEDIUM")
        assert "delete_learned_tool" in medium_risk
        assert "execute_tool_recipe" in medium_risk

    def test_mutation_tools(self):
        from infra.tool_manager.tool_registry import ToolRegistry
        mutation = ToolRegistry.get_mutation_tools()
        for name in ["delete_learned_tool", "execute_tool_recipe"]:
            assert name in mutation, f"{name} not in mutation tools"


# ====================================================================
# SkillManager learned 目录扫描
# ====================================================================

class TestSkillManagerLearned:
    """SkillManager 扫描 skills/learned/ 测试"""

    def test_load_skills_includes_learned_dir(self):
        """load_skills 应该扫描 skills/learned/ 子目录"""
        import inspect
        from modules.thinking.skills.manager import SkillManager
        src = inspect.getsource(SkillManager.load_skills)
        assert "learned" in src
