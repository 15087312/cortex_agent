"""tool_registry 补充测试：ToolInfo schema / 注册/注销 / 过滤与批量查询"""
from datetime import datetime
from unittest.mock import MagicMock

from infra.tool_manager.tool_registry import (
    ParamSchema,
    ToolInfo,
    ToolRegistry,
    register_tool as module_register_tool,
)


def _cleanup(*names):
    for n in names:
        ToolRegistry.unregister(n)


# ── ParamSchema / ToolInfo ────────────────────────────────────────────────────

def test_param_schema_defaults():
    ps = ParamSchema()
    assert ps.description == ""
    assert ps.type == "string"
    assert ps.required is False


def test_tool_info_post_init_sets_registered_at():
    t = ToolInfo(name="x", func=lambda: None)
    assert t.registered_at


def test_tool_info_registered_at_preserved():
    t = ToolInfo(name="x", func=lambda: None, registered_at="2020-01-01T00:00:00")
    assert t.registered_at == "2020-01-01T00:00:00"


def test_tool_info_source_properties():
    plugin = ToolInfo(name="p", func=lambda: None, source="plugin")
    security = ToolInfo(name="s", func=lambda: None, source="security")
    builtin = ToolInfo(name="b", func=lambda: None, source="builtin")
    assert plugin.is_plugin_tool
    assert not plugin.is_security_tool
    assert security.is_security_tool
    assert builtin.is_builtin_tool
    assert not plugin.is_builtin_tool


def test_tool_info_permissions():
    assert ToolInfo(name="q", func=lambda: None, category="query").permissions == ["read"]
    assert ToolInfo(name="m", func=lambda: None, category="mutation").permissions == ["write"]
    assert ToolInfo(name="a", func=lambda: None, category="admin").permissions == ["admin"]
    assert ToolInfo(name="o", func=lambda: None, category="other").permissions == ["read"]


def test_param_description_and_type():
    t = ToolInfo(name="x", func=lambda: None, params={
        "a": ParamSchema(description="desc a", type="number"),
        "b": "plain desc",
        "c": None,
    })
    assert t._param_description("a") == "desc a"
    assert t._param_description("b") == "plain desc"
    assert t._param_description("c") == ""
    assert t._param_type("a") == "number"
    assert t._param_type("b") == "string"
    assert t._param_type("missing") == "string"


def test_required_params_from_signature():
    def fn(a, b=1, *args, _hidden, **kw):
        pass
    t = ToolInfo(name="x", func=fn)
    assert t._required_params_from_signature() == ["a"]


def test_required_params_signature_error():
    import inspect
    def no_sig(*a, **k):
        pass
    no_sig.__signature__ = None  # inspect.signature → ValueError
    t = ToolInfo(name="x", func=no_sig)
    assert t._required_params_from_signature() == []


def test_infer_type_from_signature():
    def fn(a: int, b: float, c: str, d: bool, e, f: list, g: dict):
        pass
    t = ToolInfo(name="x", func=fn)
    assert t._infer_type_from_signature("a") == "integer"
    assert t._infer_type_from_signature("b") == "number"
    assert t._infer_type_from_signature("c") == "string"
    assert t._infer_type_from_signature("d") == "boolean"
    assert t._infer_type_from_signature("e") is None
    assert t._infer_type_from_signature("f") == "array"
    assert t._infer_type_from_signature("g") == "object"
    assert t._infer_type_from_signature("zz") is None


def test_infer_type_optional():
    def fn(a=None):
        pass
    t = ToolInfo(name="x", func=fn)
    assert t._infer_type_from_signature("a") is None


def test_infer_type_no_func():
    t = ToolInfo(name="x", func=None)
    assert t._infer_type_from_signature("a") is None


def test_infer_type_signature_error():
    def no_sig(*a, **k):
        pass
    no_sig.__signature__ = None
    t = ToolInfo(name="x", func=no_sig)
    assert t._infer_type_from_signature("a") is None


def test_to_json_schema():
    def fn(name: str, count: int, flag: bool = False, value: float = 0.0):
        pass
    t = ToolInfo(name="x", func=fn, params={
        "name": ParamSchema(description="名称", type="string", required=True),
        "count": "数量",
    })
    schema = t.to_json_schema()
    assert schema["type"] == "object"
    assert schema["properties"]["name"]["type"] == "string"
    assert schema["properties"]["name"]["description"] == "名称"
    # count 是纯字符串描述 + int 注解 → 推断为 integer
    assert schema["properties"]["count"]["type"] == "integer"
    assert "required" in schema
    assert "name" in schema["required"]
    assert "count" in schema["required"]


def test_to_json_schema_text_type_normalized():
    t = ToolInfo(name="x", func=lambda: None, params={
        "a": ParamSchema(description="", type="text"),
    })
    assert t.to_json_schema()["properties"]["a"]["type"] == "string"


def test_to_json_schema_no_required():
    def fn(a=1):
        pass
    t = ToolInfo(name="x", func=fn)
    schema = t.to_json_schema()
    assert "required" not in schema


# ── 注册 / 注销 ───────────────────────────────────────────────────────────────

def test_register_decorator_default_name():
    @ToolRegistry.register(description="d")
    def ext_reg_decorated(a):
        return a
    try:
        t = ToolRegistry.get_tool("ext_reg_decorated")
        assert t is not None
        assert t.description == "d"
        assert t.registered_at
    finally:
        ToolRegistry.unregister("ext_reg_decorated")


def test_register_tool_overwrites(monkeypatch):
    ToolRegistry.register_tool(name="ext_overwrite", func=lambda: 1, source="builtin")
    ToolRegistry.register_tool(name="ext_overwrite", func=lambda: 2, source="plugin")
    try:
        assert ToolRegistry.get_tool("ext_overwrite").source == "plugin"
    finally:
        ToolRegistry.unregister("ext_overwrite")


def test_unregister_missing():
    assert ToolRegistry.unregister("ext_does_not_exist") is False


def test_unregister_by_plugin():
    ToolRegistry.register_tool(name="ext_p1_a", func=lambda: None, source="plugin", plugin_name="pluginA")
    ToolRegistry.register_tool(name="ext_p1_b", func=lambda: None, source="plugin", plugin_name="pluginA")
    ToolRegistry.register_tool(name="ext_p1_c", func=lambda: None, source="builtin", plugin_name="")
    try:
        n = ToolRegistry.unregister_by_plugin("pluginA")
        assert n == 2
        assert ToolRegistry.get_tool("ext_p1_a") is None
        assert ToolRegistry.get_tool("ext_p1_c") is not None
        # 不存在的插件 → count 0，不记录日志
        assert ToolRegistry.unregister_by_plugin("nope_plugin") == 0
    finally:
        _cleanup("ext_p1_a", "ext_p1_b", "ext_p1_c")


def test_get_func():
    ToolRegistry.register_tool(name="ext_getfunc", func=lambda: 42)
    try:
        assert ToolRegistry.get_func("ext_getfunc")() == 42
        assert ToolRegistry.get_func("ext_no_func") is None
    finally:
        ToolRegistry.unregister("ext_getfunc")


def test_module_register_tool_function():
    module_register_tool("ext_module_reg", lambda: 1)
    try:
        t = ToolRegistry.get_tool("ext_module_reg")
        assert t is not None and t.source == "dynamic"
    finally:
        ToolRegistry.unregister("ext_module_reg")


# ── 批量查询 ──────────────────────────────────────────────────────────────────

def test_get_tools_by_risk_mutation_tag():
    ToolRegistry.register_tool(name="ext_risk_high", func=lambda: None, risk_level="HIGH")
    ToolRegistry.register_tool(name="ext_mutation", func=lambda: None, category="mutation")
    ToolRegistry.register_tool(name="ext_tagged", func=lambda: None, tags=["ext_tag_x"])
    try:
        assert "ext_risk_high" in ToolRegistry.get_tools_by_risk("HIGH")
        assert "ext_mutation" in ToolRegistry.get_mutation_tools()
        assert "ext_tagged" in ToolRegistry.get_tools_by_tag("ext_tag_x")
    finally:
        _cleanup("ext_risk_high", "ext_mutation", "ext_tagged")


def test_list_tools_source_filter():
    ToolRegistry.register_tool(name="ext_list_plugin", func=lambda: None, source="plugin")
    try:
        listed = ToolRegistry.list_tools(source="plugin")
        assert "ext_list_plugin" in listed
        assert listed["ext_list_plugin"]["source"] == "plugin"
    finally:
        ToolRegistry.unregister("ext_list_plugin")


def test_list_by_source_and_plugins():
    ToolRegistry.register_tool(name="ext_src_plugin", func=lambda: None, source="plugin", plugin_name="pX")
    ToolRegistry.register_tool(name="ext_src_sec", func=lambda: None, source="security")
    try:
        by_src = ToolRegistry.list_by_source()
        assert "ext_src_plugin" in by_src["plugin"]
        assert "pX" in ToolRegistry.get_plugins()
        assert "ext_src_sec" not in by_src  # 未分类来源不进入结果分组
    finally:
        _cleanup("ext_src_plugin", "ext_src_sec")


# ── 过滤 / 白名单 ─────────────────────────────────────────────────────────────

def test_get_filtered_tools_star(monkeypatch):
    monkeypatch.setattr(ToolRegistry, "_load_disabled", lambda: None)
    result = ToolRegistry._get_filtered_tools(["*"])
    assert any(t.name == "calc" for t in result)


def test_get_filtered_tools_whitelist_and_tags(monkeypatch):
    ToolRegistry.register_tool(name="ext_wl_tool", func=lambda: None, tags=["ext_wl_tag"])
    ToolRegistry.register_tool(name="ext_tag_only", func=lambda: None, tags=["ext_wl_tag"])
    monkeypatch.setattr(ToolRegistry, "_load_disabled", lambda: None)
    result = ToolRegistry._get_filtered_tools(["ext_wl_tool", "tag:ext_wl_tag"])
    try:
        names = [t.name for t in result]
        assert "ext_wl_tool" in names
        assert "ext_tag_only" in names  # 仅通过 tag 匹配命中
    finally:
        _cleanup("ext_wl_tool", "ext_tag_only")


def test_get_filtered_tools_disabled_filter(monkeypatch):
    ToolRegistry.register_tool(name="ext_disabled", func=lambda: None, source="builtin")
    try:
        disabled_orig = ToolRegistry._disabled_tools
        loaded_orig = ToolRegistry._disabled_loaded
        monkeypatch.setattr(ToolRegistry, "_disabled_tools", {"ext_disabled"})
        monkeypatch.setattr(ToolRegistry, "_disabled_loaded", True)
        names = [t.name for t in ToolRegistry._get_filtered_tools(["ext_disabled"])]
        assert "ext_disabled" not in names
        monkeypatch.setattr(ToolRegistry, "_disabled_tools", disabled_orig)
        monkeypatch.setattr(ToolRegistry, "_disabled_loaded", loaded_orig)
    finally:
        ToolRegistry.unregister("ext_disabled")


def test_get_tools_for_api_sort_and_core():
    ToolRegistry.register_tool(name="ext_api_hi", func=lambda: None, description="d", priority=5, core=True)
    ToolRegistry.register_tool(name="ext_api_lo", func=lambda: None, description="d", priority=-5, core=False)
    try:
        all_tools = ToolRegistry.get_tools_for_api()
        names = [t["function"]["name"] for t in all_tools]
        assert names.index("ext_api_hi") < names.index("ext_api_lo")
        # sort_by_priority=False → 保持注册顺序不排序（无副作用即可）
        ToolRegistry.get_tools_for_api(sort_by_priority=False)
        core = ToolRegistry.get_core_tools_for_api()
        core_names = [t["function"]["name"] for t in core]
        assert "ext_api_hi" in core_names
        assert "ext_api_lo" not in core_names
        ToolRegistry.get_core_tools_for_api(sort_by_priority=False)
        non_core = ToolRegistry.list_non_core_tools()
        non_core_names = [t["name"] for t in non_core]
        assert "ext_api_lo" in non_core_names
    finally:
        _cleanup("ext_api_hi", "ext_api_lo")


def test_clear_dynamic():
    ToolRegistry.register_tool(name="ext_dyn_1", func=lambda: None, source="dynamic")
    ToolRegistry.register_tool(name="ext_dyn_2", func=lambda: None, source="dynamic")
    ToolRegistry.register_tool(name="ext_dyn_builtin", func=lambda: None, source="builtin")
    try:
        n = ToolRegistry.clear_dynamic()
        assert n >= 2
        assert ToolRegistry.get_tool("ext_dyn_builtin") is not None
    finally:
        ToolRegistry.unregister("ext_dyn_builtin")


# ── 禁用 / 启用 ───────────────────────────────────────────────────────────────

def test_set_tool_enabled_missing():
    ok, msg = ToolRegistry.set_tool_enabled("ext_nope", False)
    assert ok is False
    assert "工具不存在" in msg


def test_set_tool_enabled_security_denied():
    ToolRegistry.register_tool(name="ext_sec", func=lambda: None, source="security")
    try:
        ok, msg = ToolRegistry.set_tool_enabled("ext_sec", False)
        assert ok is False
        assert "安全工具不可禁用" in msg
    finally:
        ToolRegistry.unregister("ext_sec")


def test_set_tool_enabled_persist_fail(monkeypatch, tmp_path):
    ToolRegistry.register_tool(name="ext_toggle", func=lambda: None, source="builtin")
    try:
        path = tmp_path / "tool_settings.json"
        monkeypatch.setattr(ToolRegistry, "_settings_path", lambda: path)
        monkeypatch.setattr(ToolRegistry, "_disabled_loaded", True)
        monkeypatch.setattr(ToolRegistry, "_disabled_tools", set())
        from unittest.mock import patch as _patch
        with _patch.object(path.__class__, "write_text", side_effect=OSError("disk full")):
            ok, msg = ToolRegistry.set_tool_enabled("ext_toggle", False)
        assert ok is False
        assert "持久化失败" in msg
    finally:
        ToolRegistry.unregister("ext_toggle")


def test_is_tool_enabled(monkeypatch):
    monkeypatch.setattr(ToolRegistry, "_disabled_tools", {"ext_disabled"})
    monkeypatch.setattr(ToolRegistry, "_disabled_loaded", True)
    assert ToolRegistry.is_tool_enabled("ext_disabled") is False
    assert ToolRegistry.is_tool_enabled("other") is True


def test_load_disabled_exception(monkeypatch, tmp_path):
    path = tmp_path / "tool_settings.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(ToolRegistry, "_settings_path", lambda: path)
    monkeypatch.setattr(ToolRegistry, "_disabled_loaded", False)
    ToolRegistry._load_disabled()
    # 解析失败 → 保留空集合，且 loaded 置 True 不再重试
    assert ToolRegistry._disabled_loaded is True
