"""插件构建器 — 为已学工具生成完整的插件包

生成的插件包结构：
  data/plugins/learned_tools/<app_name>/<tool_name>/
    ├── plugin.yaml      # 标准插件元数据
    ├── recipe.json       # 动作序列（RecipeEngine 使用）
    └── src/
        └── tool_impl.py  # 工具实现（入口函数）

完全兼容现有 PluginLoader，无需修改加载逻辑。
"""
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

from utils.logger import setup_logger
from .recipe_engine import RecipeEngine, _sanitize_name

logger = setup_logger("plugin_builder")


class PluginBuilder:
    """已学工具插件构建器"""

    @staticmethod
    def create_plugin(
        tool_name: str,
        app_name: str,
        steps: List[Dict[str, Any]],
        params_schema: Dict[str, Any],
        task_description: str = "",
    ) -> Path:
        """生成完整的插件包

        Args:
            tool_name: 工具名（如 chrome_search）
            app_name: 应用名（如 Chrome）
            steps: 动作序列
            params_schema: 参数 schema，如 {"query": {"type": "string", "required": true}}
            task_description: 任务描述

        Returns:
            插件目录路径
        """
        plugin_name = f"learned_{_sanitize_name(app_name)}_{_sanitize_name(tool_name)}"

        # 1. 保存 recipe.json 和 meta.json
        plugin_dir = RecipeEngine.save(
            tool_name, app_name, steps, params_schema, task_description
        )

        # 2. 生成 plugin.yaml
        plugin_yaml = _build_plugin_yaml(
            plugin_name, tool_name, app_name, params_schema, task_description
        )
        (plugin_dir / "plugin.yaml").write_text(
            plugin_yaml, encoding="utf-8"
        )

        # 3. 生成 src/tool_impl.py
        src_dir = plugin_dir / "src"
        src_dir.mkdir(exist_ok=True)
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        (src_dir / "tool_impl.py").write_text(
            _build_tool_impl(tool_name, app_name, params_schema),
            encoding="utf-8",
        )

        logger.info(f"插件包已生成: {plugin_dir}")
        return plugin_dir

    @staticmethod
    def delete_plugin(tool_name: str, app_name: str = "") -> bool:
        """删除整个插件目录"""
        return RecipeEngine.delete(tool_name, app_name)


def _build_plugin_yaml(
    plugin_name: str,
    tool_name: str,
    app_name: str,
    params_schema: Dict[str, Any],
    task_description: str,
) -> str:
    """生成 plugin.yaml 内容"""
    # 构建参数声明
    params_decl = {}
    for param_name, param_info in params_schema.items():
        if isinstance(param_info, dict):
            params_decl[param_name] = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", f"参数 {param_name}"),
                "required": param_info.get("required", True),
            }
        else:
            params_decl[param_name] = {
                "type": "string",
                "description": f"参数 {param_name}",
                "required": True,
            }

    metadata = {
        "name": plugin_name,
        "version": "1.0.0",
        "description": task_description or f"AI learned tool: {tool_name} for {app_name}",
        "author": "AI",
        "license": "MIT",
        "extensions": [
            {
                "type": "tool",
                "name": tool_name,
                "entry": f"src.tool_impl:execute_{_sanitize_name(tool_name)}",
                "description": task_description or f"执行已学工具 {tool_name}",
                "params": params_decl,
            }
        ],
        "permissions": [{"compute": True}],
        "runtime": {
            "mode": "in_process",
            "trust": "official",
        },
        "metadata": {
            "app_name": app_name,
            "recipe_path": "recipe.json",
            "learned_at": datetime.now(timezone.utc).isoformat(),
            "is_learned_tool": True,
        },
    }

    return yaml.dump(metadata, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _build_tool_impl(
    tool_name: str,
    app_name: str,
    params_schema: Dict[str, Any],
) -> str:
    """生成 src/tool_impl.py 内容"""
    safe_name = _sanitize_name(tool_name)

    return f'''"""自动生成的已学工具实现: {tool_name} (app: {app_name})"""
from modules.toolbuilder.recipe_engine import RecipeEngine


def execute_{safe_name}(**kwargs) -> dict:
    """
    执行已学工具 {tool_name} (app: {app_name})

    参数: {list(params_schema.keys())}
    """
    return RecipeEngine.execute("{tool_name}", kwargs, "{app_name}")
'''
