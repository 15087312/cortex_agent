"""Recipe 引擎 — 磁盘 I/O、变量替换、顺序执行、统计更新

Recipe 是已学工具的核心数据结构：
- recipe.json: 动作序列 + 参数 schema + 执行统计
- 由 RecipeEngine 负责读写和执行
"""
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logger import setup_logger

logger = setup_logger("recipe_engine")

# 已学工具存储根目录
_LEARNED_TOOLS_ROOT = None

# 文件操作锁 — 保护 recipe.json / _index.json 的读-改-写
_file_lock = threading.Lock()

# Recipe 允许的动作白名单 — 只允许 UI 交互类动作，禁止系统操作
_RECIPE_ALLOWED_ACTIONS = frozenset({
    "mouse_click", "mouse_double_click", "mouse_right_click",
    "mouse_move", "mouse_drag", "mouse_scroll",
    "keyboard_type", "keyboard_press", "keyboard_hotkey",
    "keyboard_release",
    "wait", "sleep",
})


def _get_learned_tools_root() -> Path:
    """插件根目录：data/plugins/

    已学工具作为标准插件存放在 data/plugins/learned_<app>_<tool>/ 下，
    与 PluginLoader 的单层扫描兼容。
    """
    global _LEARNED_TOOLS_ROOT
    if _LEARNED_TOOLS_ROOT is None:
        project_root = Path(__file__).parent.parent.parent
        _LEARNED_TOOLS_ROOT = project_root / "data" / "plugins"
    return _LEARNED_TOOLS_ROOT


def _plugin_dir_name(tool_name: str, app_name: str) -> str:
    """生成插件目录名：learned_<app>_<tool>"""
    return f"learned_{_sanitize_name(app_name)}_{_sanitize_name(tool_name)}"


class RecipeEngine:
    """Recipe 执行引擎"""

    @staticmethod
    def get_recipe_path(tool_name: str, app_name: str) -> Path:
        """获取 recipe.json 的完整路径"""
        return _get_learned_tools_root() / _plugin_dir_name(tool_name, app_name) / "recipe.json"

    @staticmethod
    def load(tool_name: str, app_name: str = "") -> Optional[Dict[str, Any]]:
        """加载 recipe.json

        如果提供了 app_name，直接定位；否则在所有 app 目录下搜索。
        """
        if app_name:
            path = RecipeEngine.get_recipe_path(tool_name, app_name)
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            return None

        # 搜索模式：匹配 learned_*_{tool_name} 目录
        root = _get_learned_tools_root()
        if not root.exists():
            return None
        safe_tool = _sanitize_name(tool_name)
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith("learned_") and d.name.endswith(f"_{safe_tool}"):
                recipe_path = d / "recipe.json"
                if recipe_path.exists():
                    return json.loads(recipe_path.read_text(encoding="utf-8"))
        return None

    @staticmethod
    def save(
        tool_name: str,
        app_name: str,
        steps: List[Dict[str, Any]],
        params_schema: Dict[str, Any],
        task_description: str = "",
    ) -> Path:
        """保存 recipe.json 和 meta.json，返回插件目录路径"""
        plugin_dir = _get_learned_tools_root() / _plugin_dir_name(tool_name, app_name)
        plugin_dir.mkdir(parents=True, exist_ok=True)

        recipe = {
            "schema_version": "1.0",
            "tool_name": tool_name,
            "app_name": app_name,
            "params": params_schema,
            "steps": steps,
            "execution_stats": {
                "total_runs": 0,
                "success_count": 0,
                "failure_count": 0,
                "avg_duration_ms": 0,
            },
        }
        (plugin_dir / "recipe.json").write_text(
            json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        meta = {
            "tool_name": tool_name,
            "app_name": app_name,
            "task_description": task_description,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "step_count": len(steps),
            "params": list(params_schema.keys()),
        }
        (plugin_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 更新索引
        RecipeEngine._update_index(app_name, tool_name, task_description, "add")

        logger.info(f"Recipe 已保存: {app_name}/{tool_name} ({len(steps)} 步)")
        return plugin_dir

    @staticmethod
    def delete(tool_name: str, app_name: str = "") -> bool:
        """删除 recipe 相关文件（由 PluginBuilder 处理完整目录删除）"""
        import shutil

        if app_name:
            plugin_dir = _get_learned_tools_root() / _plugin_dir_name(tool_name, app_name)
            if plugin_dir.exists():
                shutil.rmtree(plugin_dir)
                RecipeEngine._update_index(app_name, tool_name, "", "remove")
                logger.info(f"Recipe 已删除: {app_name}/{tool_name}")
                return True
            return False

        # 搜索模式：匹配 learned_*_{tool_name} 目录
        root = _get_learned_tools_root()
        if not root.exists():
            return False
        safe_tool = _sanitize_name(tool_name)
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            if d.name.startswith("learned_") and d.name.endswith(f"_{safe_tool}"):
                shutil.rmtree(d)
                # 从目录名反推 app_name
                parts = d.name[len("learned_"):]
                app = parts.rsplit(f"_{safe_tool}", 1)[0]
                RecipeEngine._update_index(app, tool_name, "", "remove")
                logger.info(f"Recipe 已删除: {d.name}")
                return True
        return False

    @staticmethod
    def execute(tool_name: str, params: Dict[str, Any], app_name: str = "") -> Dict[str, Any]:
        """执行 recipe：读取 → 变量替换 → 按序调用动作

        Args:
            tool_name: 工具名
            params: 实参字典
            app_name: 应用名（可选，不提供则搜索）

        Returns:
            {status, steps_executed, duration_ms, message}
        """
        recipe = RecipeEngine.load(tool_name, app_name)
        if not recipe:
            return {
                "status": "error",
                "steps_executed": 0,
                "duration_ms": 0,
                "message": f"未找到工具 {tool_name} 的 recipe",
            }

        from infra.tool_manager.tool_registry import ToolRegistry

        steps = recipe.get("steps", [])
        start_ms = _now_ms()
        executed = 0

        for step in steps:
            action = step.get("action", "")
            raw_args = step.get("args", {})
            wait_after_ms = step.get("wait_after_ms", 300)

            # 变量替换：str 类型值做 format_map，非 str 跳过
            resolved_args = _resolve_args(raw_args, params)

            # 检测未填占位符
            unfilled = _find_unfilled_placeholders(resolved_args)
            if unfilled:
                msg = f"步骤 {step.get('step_id', executed + 1)}: 参数 {unfilled} 未提供"
                RecipeEngine.update_stats(tool_name, False, _now_ms() - start_ms, app_name)
                return {
                    "status": "error",
                    "error_type": "missing_params",
                    "steps_executed": executed,
                    "failed_step": step.get("step_id", executed + 1),
                    "duration_ms": _now_ms() - start_ms,
                    "message": msg,
                }

            # 白名单校验：只允许 UI 交互类动作
            if action not in _RECIPE_ALLOWED_ACTIONS:
                msg = f"步骤 {step.get('step_id', executed + 1)}: 动作 {action} 不在允许列表中"
                RecipeEngine.update_stats(tool_name, False, _now_ms() - start_ms, app_name)
                return {
                    "status": "error",
                    "error_type": "action_forbidden",
                    "steps_executed": executed,
                    "failed_step": step.get("step_id", executed + 1),
                    "duration_ms": _now_ms() - start_ms,
                    "message": msg,
                }

            func = ToolRegistry.get_func(action)
            if func is None:
                msg = f"步骤 {step.get('step_id', executed + 1)}: 动作 {action} 未注册"
                RecipeEngine.update_stats(tool_name, False, _now_ms() - start_ms, app_name)
                return {
                    "status": "error",
                    "error_type": "action_not_found",
                    "steps_executed": executed,
                    "failed_step": step.get("step_id", executed + 1),
                    "duration_ms": _now_ms() - start_ms,
                    "message": msg,
                }

            try:
                result = func(**resolved_args)
                if isinstance(result, dict) and result.get("status") == "error":
                    RecipeEngine.update_stats(tool_name, False, _now_ms() - start_ms, app_name)
                    return {
                        "status": "error",
                        "error_type": "action_failed",
                        "steps_executed": executed,
                        "failed_step": step.get("step_id", executed + 1),
                        "duration_ms": _now_ms() - start_ms,
                        "message": f"步骤 {step.get('step_id', executed + 1)} 执行失败: {result.get('message', '')}",
                    }
            except Exception as e:
                RecipeEngine.update_stats(tool_name, False, _now_ms() - start_ms, app_name)
                return {
                    "status": "error",
                    "error_type": "action_exception",
                    "steps_executed": executed,
                    "failed_step": step.get("step_id", executed + 1),
                    "duration_ms": _now_ms() - start_ms,
                    "message": f"步骤 {step.get('step_id', executed + 1)} 异常: {e}",
                }

            executed += 1
            if wait_after_ms > 0:
                time.sleep(wait_after_ms / 1000)

        total_ms = _now_ms() - start_ms
        RecipeEngine.update_stats(tool_name, True, total_ms, app_name)
        return {
            "status": "success",
            "steps_executed": executed,
            "duration_ms": total_ms,
            "message": f"工具 {tool_name} 执行完成，共 {executed} 步",
        }

    @staticmethod
    def update_stats(
        tool_name: str,
        success: bool,
        duration_ms: float,
        app_name: str = "",
    ) -> None:
        """更新 recipe.json 中的执行统计（线程安全）"""
        with _file_lock:
            recipe = RecipeEngine.load(tool_name, app_name)
            if not recipe:
                return

            stats = recipe.get("execution_stats", {})
            total = stats.get("total_runs", 0) + 1
            success_count = stats.get("success_count", 0) + (1 if success else 0)
            failure_count = stats.get("failure_count", 0) + (0 if success else 1)
            prev_avg = stats.get("avg_duration_ms", 0)
            avg_ms = (prev_avg * (total - 1) + duration_ms) / total

            recipe["execution_stats"] = {
                "total_runs": total,
                "success_count": success_count,
                "failure_count": failure_count,
                "avg_duration_ms": round(avg_ms, 1),
                "last_run_at": datetime.now(timezone.utc).isoformat(),
                "last_success": success,
            }

            # 回写文件
            if app_name:
                path = RecipeEngine.get_recipe_path(tool_name, app_name)
            else:
                path = None
                root = _get_learned_tools_root()
                if root.exists():
                    safe_tool = sanitize_name(tool_name)
                    for d in sorted(root.iterdir()):
                        if not d.is_dir():
                            continue
                        if d.name.startswith("learned_") and d.name.endswith(f"_{safe_tool}"):
                            candidate = d / "recipe.json"
                            if candidate.exists():
                                path = candidate
                                break
            if path and path.exists():
                path.write_text(
                    json.dumps(recipe, ensure_ascii=False, indent=2), encoding="utf-8"
                )

    @staticmethod
    def list_all() -> List[Dict[str, Any]]:
        """列出所有已学工具"""
        root = _get_learned_tools_root()
        if not root.exists():
            return []

        tools = []
        for d in sorted(root.iterdir()):
            if not d.is_dir():
                continue
            if not d.name.startswith("learned_"):
                continue
            recipe_path = d / "recipe.json"
            meta_path = d / "meta.json"
            if recipe_path.exists():
                recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
                meta = {}
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                tools.append({
                    "tool_name": recipe.get("tool_name", ""),
                    "app_name": recipe.get("app_name", ""),
                    "task_description": meta.get("task_description", ""),
                    "step_count": len(recipe.get("steps", [])),
                    "params": list(recipe.get("params", {}).keys()),
                    "stats": recipe.get("execution_stats", {}),
                })
        return tools

    @staticmethod
    def _update_index(app_name: str, tool_name: str, description: str, action: str) -> None:
        """更新 _index.json 索引文件（线程安全）"""
        with _file_lock:
            index_path = _get_learned_tools_root() / "_index.json"
            index = {}
            if index_path.exists():
                try:
                    index = json.loads(index_path.read_text(encoding="utf-8"))
                except Exception as e:
                    logger.warning(f"_index.json 损坏，重建: {e}")
                    index = {}

            tools = index.get("tools", {})
            key = f"{app_name}/{tool_name}"

            if action == "add":
                tools[key] = {
                    "tool_name": tool_name,
                    "app_name": app_name,
                    "description": description,
                    "added_at": datetime.now(timezone.utc).isoformat(),
                }
            elif action == "remove":
                tools.pop(key, None)

            index["tools"] = tools
            index["updated_at"] = datetime.now(timezone.utc).isoformat()

            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def sanitize_name(name: str) -> str:
    """将名称转为安全的目录名（小写、下划线）"""
    import re
    safe = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_").lower()
    return re.sub(r"_+", "_", safe) or "unnamed"


# 向后兼容别名
_sanitize_name = sanitize_name


def _resolve_args(raw_args: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
    """变量替换：str 类型值做 format_map，非 str 跳过"""
    resolved = {}
    for key, value in raw_args.items():
        if isinstance(value, str):
            try:
                resolved[key] = value.format_map(params)
            except (KeyError, ValueError, AttributeError):
                resolved[key] = value
        elif isinstance(value, dict):
            resolved[key] = _resolve_args(value, params)
        elif isinstance(value, list):
            resolved[key] = [
                _resolve_item(item, params) for item in value
            ]
        else:
            resolved[key] = value
    return resolved


def _resolve_item(item: Any, params: Dict[str, Any]) -> Any:
    """替换列表中的字符串项"""
    if isinstance(item, str):
        try:
            return item.format_map(params)
        except (KeyError, ValueError, AttributeError):
            return item
    return item


def _now_ms() -> float:
    return time.time() * 1000


def _find_unfilled_placeholders(args: Dict[str, Any]) -> List[str]:
    """检测参数中残留的 {xxx} 占位符"""
    import re
    unfilled = []

    def _scan(value: Any) -> None:
        if isinstance(value, str):
            found = re.findall(r"\{(\w+)\}", value)
            unfilled.extend(found)
        elif isinstance(value, dict):
            for v in value.values():
                _scan(v)
        elif isinstance(value, list):
            for v in value:
                _scan(v)

    _scan(args)
    return list(set(unfilled))
