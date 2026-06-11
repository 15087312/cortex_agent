"""
Learn 模式 — AI 自动学习 UI 操作并生成插件

被 model_runner 调用，在整个学习过程中通过 progress_callback
向 TUI 推送实时进度。

设计原则：
- fail-closed: 任何步骤失败不会留下脏状态（无回滚但不会继续破坏）
- 超时保护: 整体管线有超时，不会永久挂起
- 避免阻塞: 耗时操作使用 asyncio.to_thread 而非 time.sleep
"""
import asyncio
import json
import base64
from typing import Any, Dict, List, Optional, Callable

from utils.logger import setup_logger

logger = setup_logger("learn_mode")

# 进度事件类型
EVENT_START = "learn_start"
EVENT_OPEN_APP = "learn_open_app"
EVENT_SCREENSHOT = "learn_screenshot"
EVENT_ELEMENT_DETECT = "learn_element_detect"
EVENT_PLANNING = "learn_planning"
EVENT_EXECUTING = "learn_executing"
EVENT_EXEC_STEP = "learn_exec_step"
EVENT_GENERATING = "learn_generating"
EVENT_DONE = "learn_done"
EVENT_ERROR = "learn_error"

# 管线超时（秒）
_PIPELINE_TIMEOUT = 120
# 并发锁
_learn_lock = asyncio.Lock()


async def run_learn_pipeline(
    app_name: str,
    tool_name: str,
    task_description: str,
    params_hint: str = "{}",
    progress_callback: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    user_hint: str = "",
) -> Dict[str, Any]:
    """运行学习管线，返回学习结果。

    Args:
        app_name: 应用名（如 Chrome）
        tool_name: 工具名（如 chrome_search）
        task_description: 任务描述
        params_hint: 参数 schema JSON
        progress_callback: 进度回调 fn(event_type, data)
        user_hint: 用户自定义提示文本

    Returns:
        学习结果 dict，status 为 "success" 或 "error"
    """
    # 并发保护：同一时间只能运行一个学习管线
    if _learn_lock.locked():
        return {"status": "error", "message": "已有学习任务正在执行，请等待完成后再试"}

    async with _learn_lock:
        return await _run_pipeline_impl(
            app_name, tool_name, task_description, params_hint,
            progress_callback, user_hint,
        )


async def _run_pipeline_impl(
    app_name: str,
    tool_name: str,
    task_description: str,
    params_hint: str,
    progress_callback: Optional[Callable],
    user_hint: str,
) -> Dict[str, Any]:
    """实际管线实现（在锁内执行）"""
    try:
        return await asyncio.wait_for(
            _pipeline_steps(
                app_name, tool_name, task_description, params_hint,
                progress_callback, user_hint,
            ),
            timeout=_PIPELINE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        _emit(progress_callback, EVENT_ERROR, {"message": "学习超时"})
        return {"status": "error", "message": f"学习超时（超过 {_PIPELINE_TIMEOUT}s）"}
    except Exception as e:
        logger.error(f"[Learn] 管线意外异常: {e}")
        _emit(progress_callback, EVENT_ERROR, {"message": f"管线异常: {e}"})
        return {"status": "error", "message": f"管线异常: {e}"}


async def _pipeline_steps(
    app_name: str,
    tool_name: str,
    task_description: str,
    params_hint: str,
    progress_callback: Optional[Callable],
    user_hint: str,
) -> Dict[str, Any]:
    """管线各步骤"""
    _emit(progress_callback, EVENT_START, {
        "app_name": app_name,
        "tool_name": tool_name,
        "task_description": task_description,
        "user_hint": user_hint,
    })

    # 1. 打开应用
    _emit(progress_callback, EVENT_OPEN_APP, {"app_name": app_name, "status": "opening"})
    try:
        from infra.tool_manager.tool_registry import ToolRegistry
        open_func = ToolRegistry.get_func("open_app")
        if open_func:
            result = await asyncio.to_thread(open_func, app_identifier=app_name)
            if isinstance(result, dict) and result.get("status") == "error":
                _emit(progress_callback, EVENT_ERROR, {"message": f"打开应用失败: {result.get('message')}"})
                return {"status": "error", "message": f"打开应用失败: {result.get('message')}"}
        await asyncio.sleep(1.5)
        _emit(progress_callback, EVENT_OPEN_APP, {"app_name": app_name, "status": "done"})
    except Exception as e:
        _emit(progress_callback, EVENT_ERROR, {"message": f"打开应用异常: {e}"})
        return {"status": "error", "message": f"打开应用异常: {e}"}

    # 2. 截图
    _emit(progress_callback, EVENT_SCREENSHOT, {"status": "capturing"})
    try:
        screenshot = await _capture_current_screen_async()
    except Exception as e:
        _emit(progress_callback, EVENT_ERROR, {"message": f"截图失败: {e}"})
        return {"status": "error", "message": f"截图失败: {e}"}
    if not screenshot:
        _emit(progress_callback, EVENT_ERROR, {"message": "截图失败: 返回为空"})
        return {"status": "error", "message": "截图失败: 返回为空"}
    _emit(progress_callback, EVENT_SCREENSHOT, {"status": "done", "size": len(screenshot)})

    # 3. OmniParser 元素检测
    _emit(progress_callback, EVENT_ELEMENT_DETECT, {"status": "detecting"})
    try:
        image_bytes = base64.b64decode(screenshot)
    except Exception as e:
        _emit(progress_callback, EVENT_ELEMENT_DETECT, {"status": "error", "error": f"base64 解码失败: {e}"})
        return {"status": "error", "message": f"截图数据解码失败: {e}"}

    try:
        from modules.perception.detectors.omniparser_detector import OmniParserDetector
        detector = OmniParserDetector()
        elements = await asyncio.to_thread(detector.detect_elements, image_bytes)
        _emit(progress_callback, EVENT_ELEMENT_DETECT, {
            "status": "done",
            "count": len(elements),
            "backend": detector.backend,
            "precision": detector.precision,
        })
        if not elements:
            _emit(progress_callback, EVENT_ERROR, {"message": "未检测到 UI 元素"})
            return {"status": "error", "message": "未检测到 UI 元素"}
        # 精度降级检测：OCR-only 模式下元素坐标不可靠
        if detector.precision == getattr(OmniParserDetector, "PRECISION_LOW", "low"):
            _emit(progress_callback, EVENT_ERROR, {
                "message": "UI 检测精度不足（OCR-only），无法准确定位元素。请部署 OmniParser 服务。"
            })
            return {
                "status": "error",
                "message": "UI 检测精度不足（OCR-only），无法准确定位元素。请部署 OmniParser 服务。",
            }
    except Exception as e:
        _emit(progress_callback, EVENT_ELEMENT_DETECT, {"status": "error", "error": str(e)})
        _emit(progress_callback, EVENT_ERROR, {"message": f"元素检测失败: {e}"})
        return {"status": "error", "message": f"元素检测失败: {e}"}

    # 4. AI 规划动作序列
    _emit(progress_callback, EVENT_PLANNING, {"status": "planning"})
    try:
        params_schema = json.loads(params_hint) if params_hint else {}
    except json.JSONDecodeError:
        params_schema = {}
    try:
        from modules.toolbuilder.action_planner import ActionPlanner
        planner = ActionPlanner()
        steps = await planner.plan(task_description, elements, params_schema)
        if not steps:
            _emit(progress_callback, EVENT_ERROR, {"message": "AI 动作规划失败"})
            return {"status": "error", "message": "AI 动作规划失败"}
        _emit(progress_callback, EVENT_PLANNING, {
            "status": "done",
            "steps": steps,
            "count": len(steps),
        })
    except Exception as e:
        _emit(progress_callback, EVENT_PLANNING, {"status": "error", "error": str(e)})
        _emit(progress_callback, EVENT_ERROR, {"message": f"动作规划异常: {e}"})
        return {"status": "error", "message": f"动作规划异常: {e}"}

    # 5. 执行录制（实际控制鼠标键盘）
    _emit(progress_callback, EVENT_EXECUTING, {"total": len(steps), "current": 0})
    from modules.toolbuilder.recipe_engine import _RECIPE_ALLOWED_ACTIONS
    executed_steps = 0
    for step in steps:
        action = step.get("action", "")
        args = step.get("args", {})
        wait_ms = step.get("wait_after_ms", 300)

        if action not in _RECIPE_ALLOWED_ACTIONS:
            _emit(progress_callback, EVENT_ERROR, {
                "message": f"动作 {action} 不在允许列表中",
                "step": step.get("step_id"),
            })
            return {
                "status": "error",
                "message": f"步骤 {step.get('step_id')}: 动作 {action} 不在允许列表中",
                "steps_executed": executed_steps,
            }

        from infra.tool_manager.tool_registry import ToolRegistry

        # ── 语义动作：通过标签定位，不依赖坐标 ──
        if action in ("click_element", "double_click_element", "right_click_element", "type_into"):
            label = args.get("label", "")
            if not label:
                return {"status": "error", "message": f"{action} 需要 label 参数", "steps_executed": executed_steps}
            elem = await _async_find_element(label)
            if not elem:
                return {"status": "error", "message": f"未找到元素「{label}」", "steps_executed": executed_steps}
            cx, cy = elem["center_x"], elem["center_y"]
            if action == "click_element":
                await asyncio.to_thread(ToolRegistry.get_func("mouse_click"), x=cx, y=cy)
            elif action == "double_click_element":
                await asyncio.to_thread(ToolRegistry.get_func("mouse_click"), x=cx, y=cy, clicks=2)
            elif action == "right_click_element":
                await asyncio.to_thread(ToolRegistry.get_func("mouse_click"), x=cx, y=cy, button="right")
            elif action == "type_into":
                await asyncio.to_thread(ToolRegistry.get_func("mouse_click"), x=cx, y=cy)
                await asyncio.sleep(0.3)
                await asyncio.to_thread(ToolRegistry.get_func("keyboard_type"), text=args.get("text", ""))
            executed_steps += 1
            if wait_ms > 0:
                await asyncio.sleep(wait_ms / 1000)
            continue

        func = ToolRegistry.get_func(action)
        if func is None:
            _emit(progress_callback, EVENT_ERROR, {
                "message": f"动作 {action} 未注册",
                "step": step.get("step_id"),
            })
            return {
                "status": "error",
                "message": f"步骤 {step.get('step_id')}: 动作 {action} 未注册",
                "steps_executed": executed_steps,
            }

        try:
            _emit(progress_callback, EVENT_EXEC_STEP, {
                "current": executed_steps + 1,
                "total": len(steps),
                "action": action,
                "args": args,
                "status": "executing",
                "label": step.get("description", action),
            })
            result = await asyncio.to_thread(func, **args)
            if isinstance(result, dict) and result.get("status") == "error":
                _emit(progress_callback, EVENT_EXEC_STEP, {
                    "current": executed_steps + 1,
                    "total": len(steps),
                    "action": action,
                    "status": "error",
                    "error": result.get("message"),
                })
                return {
                    "status": "error",
                    "message": f"步骤 {executed_steps + 1} 执行失败: {result.get('message')}",
                    "steps_executed": executed_steps,
                }
        except Exception as e:
            _emit(progress_callback, EVENT_EXEC_STEP, {
                "current": executed_steps + 1,
                "total": len(steps),
                "action": action,
                "status": "error",
                "error": str(e),
            })
            return {
                "status": "error",
                "message": f"步骤 {executed_steps + 1} 异常: {e}",
                "steps_executed": executed_steps,
            }

        executed_steps += 1
        _emit(progress_callback, EVENT_EXEC_STEP, {
            "current": executed_steps,
            "total": len(steps),
            "action": action,
            "status": "done",
        })
        if wait_ms > 0:
            await asyncio.sleep(wait_ms / 1000)

    # 6. 生成插件包
    _emit(progress_callback, EVENT_GENERATING, {"status": "generating_plugin"})
    try:
        from modules.toolbuilder.plugin_builder import PluginBuilder
        plugin_path = PluginBuilder.create_plugin(
            tool_name, app_name, steps, params_schema, task_description
        )
        _emit(progress_callback, EVENT_GENERATING, {"status": "plugin_done", "path": str(plugin_path)})
    except Exception as e:
        _emit(progress_callback, EVENT_GENERATING, {"status": "error", "error": str(e)})
        _emit(progress_callback, EVENT_ERROR, {"message": f"插件生成失败: {e}"})
        return {"status": "error", "message": f"插件生成失败: {e}"}

    # 7. 插件系统已移除，跳过热加载

    # 8. 更新 Skill YAML
    try:
        from modules.toolbuilder.skill_generator import SkillGenerator
        await asyncio.to_thread(SkillGenerator.generate_or_update, app_name)
        _emit(progress_callback, EVENT_GENERATING, {"status": "skill_done"})
    except Exception as e:
        logger.warning(f"Skill 生成失败（非致命）: {e}")

    _emit(progress_callback, EVENT_DONE, {
        "tool_name": tool_name,
        "app_name": app_name,
        "plugin_path": str(plugin_path),
        "steps_count": len(steps),
    })

    return {
        "status": "success",
        "tool_name": tool_name,
        "app_name": app_name,
        "plugin_path": str(plugin_path),
        "steps_count": len(steps),
        "message": f"工具 {tool_name} 学习完成，共 {len(steps)} 步",
    }


async def _capture_current_screen_async() -> Optional[str]:
    """异步截取当前屏幕"""
    return await asyncio.to_thread(_capture_screen_sync)


async def _async_find_element(label: str) -> Optional[Dict[str, Any]]:
    """通过标签在当前屏幕查找 UI 元素"""
    try:
        from infra.tool_manager.tools.perception_tools import detect_ui_elements
        result = await detect_ui_elements()
        if not result.get("success"):
            return None
        label_lower = label.lower().strip()
        elements = result.get("elements", [])
        for elem in elements:
            if elem.get("label", "").lower().strip() == label_lower:
                return elem
        for elem in elements:
            if label_lower in elem.get("label", "").lower():
                return elem
        return None
    except Exception as e:
        logger.debug(f"元素查找失败: {e}")
        return None


def _capture_screen_sync() -> Optional[str]:
    """同步截取当前屏幕，返回 base64 编码的 PNG"""
    try:
        from utils.screen_capture import capture_screen_base64
        return capture_screen_base64()
    except Exception as e:
        logger.error(f"截图失败: {e}")
        raise  # 让调用方看到具体错误


def _emit(callback: Optional[Callable], event: str, data: Dict[str, Any]):
    """安全地调用进度回调"""
    if callback:
        try:
            callback(event, data)
        except Exception as e:
            logger.debug(f"进度回调异常（非致命）: {e}")
