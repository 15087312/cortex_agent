"""
模块综合测试 — 覆盖 context/pool, perception/pool, perception/integration,
values_store, context/controller

运行: python3 -m pytest tests/test_module_architecture.py -v
"""
import os
import tempfile
import time
from pathlib import Path

import pytest

# ============================================================================
# 1. TurnContext + ContextFragment
# ============================================================================

class TestContextFragment:
    def test_create_fragment(self):
        from modules.thinking.context.pool import ContextFragment
        f = ContextFragment("memory", "测试内容", ("large", "supervisor"), "历史记忆", 10)
        assert f.source == "memory"
        assert f.content == "测试内容"
        assert f.target_roles == ("large", "supervisor")
        assert f.section_title == "历史记忆"
        assert f.priority == 10
        assert f.ttl_turns == 0  # default

    def test_fragment_defaults(self):
        from modules.thinking.context.pool import ContextFragment
        f = ContextFragment("test", "hello", ("expert",), "标题")
        assert f.priority == 0
        assert f.ttl_turns == 0


class TestTurnContext:
    def test_empty_pool(self):
        from modules.thinking.context.pool import TurnContext
        tc = TurnContext()
        assert tc.view("large") == ""
        assert tc.view("expert") == ""

    def test_add_and_view_single_role(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("mem", "事件1", ("large",), "历史记忆"))
        view = tc.view("large")
        assert "事件1" in view
        assert "历史记忆" in view

    def test_role_filtering(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("mem", "事件", ("large",), "记忆", 10))
        tc.add(ContextFragment("time", "当前时间", ("large",), "时间", 5))
        # expert 看不到 time 和 memory
        assert tc.view("expert") == ""
        # large 看得到两个
        large_view = tc.view("large")
        assert "事件" in large_view
        assert "当前时间" in large_view

    def test_priority_sorting(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("a", "A", ("large",), "A", 30))
        tc.add(ContextFragment("b", "B", ("large",), "B", 10))
        tc.add(ContextFragment("c", "C", ("large",), "C", 20))
        view = tc.view("large")
        # 按 priority 排序: 10, 20, 30
        assert view.index("B") < view.index("C") < view.index("A")

    def test_dedup_by_content(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("a", "重复内容", ("large",), "A"))
        tc.add(ContextFragment("b", "重复内容", ("large",), "B"))  # same content
        assert len(tc.fragments) == 1

    def test_dedup_different_content_passes(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("a", "内容A", ("large",), "A"))
        tc.add(ContextFragment("b", "内容B", ("large",), "B"))
        assert len(tc.fragments) == 2

    def test_skip_empty_fragment(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("empty", "", ("large",), "空"))
        assert len(tc.fragments) == 0

    def test_same_source_overwrites(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("memory", "旧事件", ("large",), "记忆"))
        tc.add(ContextFragment("memory", "新事件", ("large",), "记忆"))
        assert tc.fragments["memory"].content == "新事件"
        assert len(tc.fragments) == 1

    def test_section_title_format(self):
        from modules.thinking.context.pool import TurnContext, ContextFragment
        tc = TurnContext()
        tc.add(ContextFragment("x", "正文", ("large",), "自定义标题"))
        view = tc.view("large")
        assert "【自定义标题】" in view


# ============================================================================
# 2. PerceptionPool
# ============================================================================

class TestPerceptionPool:
    @pytest.fixture
    def pool(self):
        from modules.perception.pool import PerceptionPool
        return PerceptionPool(max_items=10, ttl_seconds=3600)

    def test_empty_snapshot_returns_empty_fragment(self, pool):
        frag = pool.snapshot()
        assert frag.source == "perception"
        # 空池给出「无感知数据」提示（给 orchestrator，避免模型误判系统异常）
        assert frag.content != ""
        assert "无感知数据" in frag.content
        assert frag.target_roles == ("orchestrator",)

    def test_add_and_snapshot(self, pool):
        pool.add("screen.window", "window", "当前窗口: Chrome - GitHub")
        frag = pool.snapshot()
        assert "当前窗口: Chrome - GitHub" in frag.content
        assert "窗口状态" in frag.content

    def test_dedup_duplicate(self, pool):
        pool.add("screen.window", "window", "当前窗口: Chrome - GitHub")
        pool.add("screen.window", "window", "当前窗口: Chrome - GitHub")  # duplicate
        assert len(pool._items) == 1

    def test_skip_empty_description(self, pool):
        pool.add("screen.diff", "screen", "")
        assert len(pool._items) == 0

    def test_ttl_expiry(self, pool):
        pool._ttl = 0.01  # 10ms TTL
        pool.add("screen.window", "window", "即将过期的窗口")
        time.sleep(0.02)
        frag = pool.snapshot()
        # TTL 过期后池为空 → 返回「无感知数据」提示（非空内容）
        assert "无感知数据" in frag.content

    def test_max_items_trim(self, pool):
        for i in range(15):
            pool.add("screen.ocr", "ocr", f"文本行 {i}")
        assert len(pool._items) == 10  # max_items=10

    def test_snapshot_windows_grouped(self, pool):
        pool.add("screen.window", "window", "当前窗口: A - Title")
        pool.add("screen.window", "window", "窗口切换: A → B")
        frag = pool.snapshot()
        assert "A - Title" in frag.content
        assert "A → B" in frag.content
        assert "窗口状态" in frag.content

    def test_snapshot_ocr_grouped_as_text(self, pool):
        pool.add("screen.ocr", "ocr", "OCR: Hello World")
        pool.add("screen.ui", "ui", "UI: button found")
        frag = pool.snapshot()
        assert "Hello World" in frag.content
        assert "button found" in frag.content
        # Both go to 【屏幕文本】
        assert frag.content.count("屏幕文本") == 1

    def test_snapshot_file_grouped(self, pool):
        pool.add("file.change", "file", "文件modified: /tmp/test.py")
        frag = pool.snapshot()
        assert "文件变化" in frag.content
        assert "/tmp/test.py" in frag.content

    def test_screen_diff_skipped(self, pool):
        pool.add("screen.diff", "screen", "屏幕大幅变化 (100%)")
        frag = pool.snapshot()
        assert frag.content == ""  # pixel diffs are excluded

    def test_clear(self, pool):
        pool.add("screen.window", "window", "窗口1")
        pool.clear()
        assert len(pool._items) == 0
        # clear 后空池 → 返回「无感知数据」提示
        assert "无感知数据" in pool.snapshot().content

    def test_snapshot_target_roles(self, pool):
        pool.add("screen.window", "window", "窗口信息")
        frag = pool.snapshot()
        assert frag.target_roles == ("orchestrator",)
        assert frag.priority == 5
        assert frag.ttl_turns == 1


# ============================================================================
# 3. PerceptionIntegrator
# ============================================================================

class TestPerceptionIntegrator:
    def test_singleton(self):
        from modules.perception.integration import get_perception_integrator
        pi1 = get_perception_integrator()
        pi2 = get_perception_integrator()
        assert pi1 is pi2

    def test_has_pool(self):
        from modules.perception.integration import get_perception_integrator
        pi = get_perception_integrator()
        assert pi.pool is not None
        assert hasattr(pi.pool, 'add')
        assert hasattr(pi.pool, 'snapshot')

    def test_format_window(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.window", {
            "app_name": "Chrome", "window_title": "GitHub PR",
            "prev_app": "Terminal", "prev_window": "bash",
        })
        assert "Chrome" in desc
        assert "Terminal" in desc

    def test_format_window_current(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.window", {
            "app_name": "Chrome", "window_title": "GitHub",
            "prev_app": "", "prev_window": "",
        })
        assert "当前窗口" in desc

    def test_format_ocr(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.ocr", {
            "new_lines": ["line1", "line2"],
            "roi_name": "区域A",
        })
        assert "line1" in desc
        assert "区域A" in desc

    def test_format_ocr_text_fallback(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.ocr", {
            "text": "raw text here",
            "new_lines": ["line1"],
        })
        assert "line1" in desc

    def test_format_ui(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.ui", {
            "description": "2 buttons found",
            "element_count": 3,
        })
        assert "2 buttons found" in desc

    def test_format_file_change(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("file.change", {
            "change": "modified",
            "path": "/tmp/test.py",
        })
        assert "modified" in desc
        assert "/tmp/test.py" in desc

    def test_format_speech(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("speech.detected", {
            "text": "你好科特",
        })
        assert "你好科特" in desc

    def test_format_screen_diff(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.diff", {
            "change_ratio": 0.5,
        })
        assert "大幅变化" in desc or "50%" in desc

    def test_format_unknown(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("unknown.type", {
            "description": "something happened",
        })
        assert "something happened" in desc or desc == ""

    def test_format_empty_payload(self):
        from modules.perception.integration import PerceptionIntegrator
        desc = PerceptionIntegrator._format_description("screen.window", {})
        assert desc == ""  # 无 app_name 不上报


# ============================================================================
# 4. ValueSystem
# ============================================================================

class TestValueSystem:
    @pytest.fixture
    def vs(self):
        from config.values_store import ValueSystem
        d = tempfile.mkdtemp()
        path = os.path.join(d, "core_values.txt")
        vs = ValueSystem(values_file=path)
        yield vs
        for f in os.listdir(d):
            os.unlink(os.path.join(d, f))
        os.rmdir(d)

    def test_load_defaults(self, vs):
        content = vs.load()
        assert "基本原则" in content
        assert "行为准则" in content

    def test_add_rule(self, vs):
        vs.add_rule("基本原则", "测试新规则：体验优先")
        content = vs.load()
        assert "测试新规则" in content

    def test_remove_rule(self, vs):
        # 添加一条，确认它存在，再删除
        vs.add_rule("行为准则", "这是可删除的测试规则")
        assert "这是可删除的测试规则" in vs.load()
        vs.remove_rule("行为准则", "这是可删除的测试规则")
        assert "这是可删除的测试规则" not in vs.load()

    def test_remove_rule_not_first_in_section(self, vs):
        # 在已有多条规则的 section 中删除非第一条
        vs.add_rule("行为准则", "规则一长期有效的内容")
        vs.add_rule("行为准则", "规则二短期试行的策略")
        vs.add_rule("行为准则", "规则三临时执行的方案")
        vs.remove_rule("行为准则", "规则一长期有效的内容")
        content = vs.load()
        assert "规则一长期有效的内容" not in content
        assert "规则二短期试行的策略" in content
        assert "规则三临时执行的方案" in content

    def test_update_rule(self, vs):
        vs.add_rule("行为准则", "旧规则文本内容较长")
        vs.update_rule("行为准则", "旧规则文本内容较长", "新规则替换文本内容")
        content = vs.load()
        assert "旧规则文本内容较长" not in content
        assert "新规则替换文本内容" in content

    def test_quality_gate_too_short(self, vs):
        len_before = len(vs.load())
        vs.add_rule("行为准则", "短")  # < 8 chars
        assert len(vs.load()) == len_before

    def test_quality_gate_generic(self, vs):
        len_before = len(vs.load())
        vs.add_rule("行为准则", "无需修改当前的规则")
        assert len(vs.load()) == len_before

    def test_quality_gate_valid_rule(self, vs):
        vs.add_rule("基本原则", "这是一个足够长的有效规则文本")
        assert "足够长的有效规则" in vs.load()

    def test_dedup_similar_rules(self, vs):
        vs.add_rule("行为准则", "回复要简洁有用不废话")
        vs.add_rule("行为准则", "回复要简洁有用不废")  # ~93% Jaccard
        count = vs.load().count("简洁有用")
        assert count <= 1  # 去重应该阻止第二条

    def test_get_values_dict(self, vs):
        vs.add_rule("基本原则", "第一条原则文本内容")
        vs.add_rule("行为准则", "第二条准则文本内容")
        d = vs.get_values_dict()
        assert "基本原则" in d
        assert "第一条原则文本内容" in d["基本原则"]
        assert "第二条准则文本内容" in d["行为准则"]

    def test_reset_to_default(self, vs):
        vs.add_rule("行为准则", "自定义规则123456")
        vs.reset_to_default()
        assert "自定义规则123456" not in vs.load()

    def test_save_is_persistent(self, vs):
        from config.values_store import ValueSystem
        vs.add_rule("基本原则", "持久化测试规则文本内容")
        path = str(vs.values_file)
        vs2 = ValueSystem(values_file=path)
        assert "持久化测试规则文本内容" in vs2.load()


# ============================================================================
# 5. ContextController
# ============================================================================

class TestContextController:
    @pytest.fixture
    def ctrl(self):
        from modules.thinking.context.controller import ContextController
        c = ContextController()
        c.set_mode("edit")
        yield c
        c.clear()

    def test_default_mode(self):
        from modules.thinking.context.controller import ContextController
        assert ContextController().mode == "edit"

    def test_set_mode_valid(self, ctrl):
        ctrl.set_mode("plan")
        assert ctrl.mode == "plan"

    def test_set_mode_invalid_ignored(self, ctrl):
        ctrl.set_mode("invalid_mode")
        assert ctrl.mode == "edit"

    def test_build_time_context(self, ctrl):
        text = ctrl.build_time_context(user_name="张三")
        assert "当前时间" in text
        assert "张三" in text

    def test_build_time_context_with_elapsed(self, ctrl):
        text = ctrl.build_time_context(user_name="李四", last_msg_time=time.time() - 120)
        assert "李四" in text
        assert "分钟前说过话" in text

    def test_clear_resets_hashes(self, ctrl):
        ctrl._injected_hashes.add("abc")
        ctrl.clear()
        assert len(ctrl._injected_hashes) == 0

    def test_singleton(self):
        from modules.thinking.context.controller import get_context_controller
        c1 = get_context_controller()
        c2 = get_context_controller()
        assert c1 is c2
