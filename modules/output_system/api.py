"""
输出 API - 文字、语音、键鼠控制、UI交互

提供：
1. 文字/语音输出
2. 鼠标键盘控制
3. UI元素检测和交互
4. 屏幕操作
"""
import asyncio
from fastapi import APIRouter, Body, Query, Path
from fastapi.responses import JSONResponse
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

from api.errors import AppError, ErrorCode
from modules.output_system import OutputSystem
from modules.output_system.input_controller import InputController, input_controller
from modules.output_system.ui_interactor import UIInteractor, ui_interactor

router = APIRouter(prefix="/output", tags=["输出"])


# ========== 请求模型 ==========

class MouseMoveRequest(BaseModel):
    x: int
    y: int
    duration: float = 0.3


class MouseClickRequest(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    button: str = "left"
    clicks: int = 1


class MouseDragRequest(BaseModel):
    start_x: int
    start_y: int
    end_x: int
    end_y: int
    duration: float = 0.5


class KeyboardPressRequest(BaseModel):
    key: str


class KeyboardTypeRequest(BaseModel):
    text: str
    interval: float = 0.05


class HotkeyRequest(BaseModel):
    keys: List[str]


class UIClickRequest(BaseModel):
    x: Optional[int] = None
    y: Optional[int] = None
    element_type: Optional[str] = None
    element_text: Optional[str] = None
    random_offset: bool = True
    offset_range: int = 5


# ========== 文字/语音输出 ==========

@router.post("/text")
async def text_output(text: str = Body(..., embed=True)):
    """文字输出接口"""
    try:
        output_system = OutputSystem()
        result = output_system.output_text(text, stream=False)
        return {"success": True, "data": {"output": text}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/speech")
async def speech_output(text: str = Body(..., embed=True)):
    """语音输出接口"""
    try:
        output_system = OutputSystem()
        output_system.output_text(text)
        return {"success": True, "data": {"audio_url": None, "text": text, "message": "语音合成功能需接入TTS服务（如Azure TTS、Edge TTS）"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


# ========== 鼠标控制 ==========

@router.post("/mouse/move")
async def mouse_move(request: MouseMoveRequest):
    """移动鼠标到指定位置"""
    try:
        success = input_controller.move_to(request.x, request.y, request.duration)
        return {
            "success": success,
            "data": {"x": request.x, "y": request.y, "action": "move_to"}
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/mouse/click")
async def mouse_click(request: MouseClickRequest):
    """点击鼠标"""
    try:
        success = input_controller.click(request.x, request.y, button=request.button, clicks=request.clicks)
        pos_x = request.x if request.x is not None else 0
        pos_y = request.y if request.y is not None else 0
        return {
            "success": success,
            "data": {"x": pos_x, "y": pos_y, "action": f"click_{request.button}_{request.clicks}x"}
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/mouse/double-click")
async def mouse_double_click(x: int = Query(None), y: int = Query(None)):
    """双击"""
    try:
        success = input_controller.double_click(x, y)
        return {"success": success, "data": {"x": x or 0, "y": y or 0, "action": "double_click"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/mouse/right-click")
async def mouse_right_click(x: int = Query(None), y: int = Query(None)):
    """右键点击"""
    try:
        success = input_controller.right_click(x, y)
        return {"success": success, "data": {"x": x or 0, "y": y or 0, "action": "right_click"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/mouse/scroll")
async def mouse_scroll(
    clicks: int = Body(...),
    x: int = Query(None),
    y: int = Query(None)
):
    """滚动鼠标"""
    try:
        success = input_controller.scroll(clicks, x, y)
        return {"success": success, "data": {"clicks": clicks}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/mouse/drag")
async def mouse_drag(request: MouseDragRequest):
    """拖拽操作"""
    try:
        success = input_controller.drag(request.start_x, request.start_y, request.end_x, request.end_y, request.duration)
        return {"success": success, "data": {"x": request.end_x, "y": request.end_y, "action": "drag"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.get("/mouse/position")
async def get_mouse_position():
    """获取当前鼠标位置"""
    try:
        x, y = input_controller.get_current_position()
        return {"success": True, "data": {"x": x, "y": y}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


# ========== 键盘控制 ==========

@router.post("/keyboard/press")
async def keyboard_press(request: KeyboardPressRequest):
    """按下按键"""
    try:
        success = input_controller.press(request.key)
        return {"success": success, "data": {"key": request.key, "action": "press"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/keyboard/type")
async def keyboard_type(request: KeyboardTypeRequest):
    """输入文本"""
    try:
        success = input_controller.typewrite(request.text, request.interval)
        return {"success": success, "data": {"text": request.text, "length": len(request.text)}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/keyboard/hotkey")
async def keyboard_hotkey(request: HotkeyRequest):
    """组合键"""
    try:
        success = input_controller.hotkey(*request.keys)
        return {"success": success, "data": {"keys": request.keys}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/keyboard/key-down")
async def keyboard_key_down(key: str = Body(..., embed=True)):
    """按住按键"""
    try:
        success = input_controller.key_down(key)
        return {"success": success, "data": {"key": key}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/keyboard/key-up")
async def keyboard_key_up(key: str = Body(..., embed=True)):
    """释放按键"""
    try:
        success = input_controller.key_up(key)
        return {"success": success, "data": {"key": key}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


# ========== UI交互 ==========

@router.post("/ui/screenshot")
async def ui_screenshot(
    x: int = Query(None),
    y: int = Query(None),
    width: int = Query(None),
    height: int = Query(None)
):
    """截图"""
    try:
        region = (x, y, width, height) if all(v is not None for v in [x, y, width, height]) else None
        screenshot = ui_interactor.capture_screen(region)
        
        if screenshot:
            import base64
            return {"success": True, "data": base64.b64encode(screenshot).decode(), "format": "base64_png"}
        return {"success": False, "message": "截图失败"}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/ui/detect")
async def ui_detect_elements(
    element_types: str = Query(None, description="逗号分隔: button,input,icon")
):
    """检测UI元素"""
    try:
        types = element_types.split(",") if element_types else None
        elements = await ui_interactor.detect_ui_elements(element_types=types)
        
        return {
            "success": True,
            "data": {
                "elements": [
                    {
                        "type": e.element_type,
                        "text": e.text,
                        "bounds": {"x": e.x, "y": e.y, "width": e.width, "height": e.height},
                        "center": {"x": e.center_x, "y": e.center_y},
                        "confidence": e.confidence
                    }
                    for e in elements
                ],
                "count": len(elements)
            }
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/ui/click")
async def ui_click(request: UIClickRequest):
    """点击UI元素或指定位置"""
    try:
        if request.element_text:
            result = ui_interactor.find_and_click(request.element_text, request.element_type)
        elif request.x is not None and request.y is not None:
            result = ui_interactor.click_at_position(request.x, request.y, request.random_offset, request.offset_range)
        else:
            pos = input_controller.get_current_position()
            result = ui_interactor.click_at_position(pos[0], pos[1], request.random_offset, request.offset_range)
        
        return {
            "success": result.success,
            "data": {"x": result.x, "y": result.y, "element": result.element, "mode": result.mode},
            "message": result.message if result.message else None
        }
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/ui/hover")
async def ui_hover(x: int = Body(...), y: int = Body(...)):
    """悬停到指定位置"""
    try:
        success = input_controller.move_to(x, y, duration=0.3)
        return {"success": success, "data": {"x": x, "y": y}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


@router.post("/ui/type")
async def ui_type(
    text: str = Body(...),
    x: int = Query(None),
    y: int = Query(None)
):
    """在指定位置输入文本"""
    try:
        if x is not None and y is not None:
            input_controller.click(x, y)
            await asyncio.sleep(0.1)
        
        success = input_controller.typewrite(text)
        return {"success": success, "data": {"text": text}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "输出系统错误")


# ========== 控制 ==========

@router.post("/controller/pause")
async def pause_controller():
    """暂停控制器"""
    input_controller.pause()
    return {"success": True, "data": {"message": "控制器已暂停"}}


@router.post("/controller/resume")
async def resume_controller():
    """恢复控制器"""
    input_controller.resume()
    return {"success": True, "data": {"message": "控制器已恢复"}}


@router.post("/controller/mode")
async def set_controller_mode(mode: str = Body(..., embed=True)):
    """切换控制器类型（已废弃，使用基础设施配置）"""
    raise AppError(ErrorCode.BAD_REQUEST, "模式切换功能已移除，请在基础设施层配置控制器")


# ========== 状态 ==========

@router.get("/status")
async def get_status():
    """获取输出模块状态"""
    controller_status = input_controller.get_status()
    
    return {
        "success": True,
        "data": {
            "module": "output",
            "status": "healthy",
            "capabilities": ["text", "speech", "mouse_control", "keyboard_control", "ui_interaction", "screen_capture"],
            "controller": controller_status
        }
    }
