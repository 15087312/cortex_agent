"""
MCP Screen Monitor Server — 纯视觉的屏幕分析（Touchpoint 降级方案）

当 Touchpoint（无障碍 API）无法读取 UI 时（游戏/自定义渲染界面），
此 server 通过截图 + OpenCV + EasyOCR 提供纯视觉分析。

作为 MCP stdio server 运行，通过 MCP_SERVERS 配置。
独立脚本，不引入项目其他模块。
"""
import base64
import io
import json
import subprocess
import sys
import traceback

import numpy as np

_available = True
_cv2 = None
_ocr = None


def _init():
    global _cv2, _ocr, _available
    try:
        import cv2
        _cv2 = cv2
    except ImportError:
        _available = False
        return

    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr = RapidOCR()
    except ImportError:
        pass

    _available = True


_init()


def _send(msg: dict):
    line = json.dumps(msg, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _handle_initialize(req: dict):
    _send({
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "screen_monitor", "version": "0.1.0"},
        },
    })


def _handle_list_tools(req: dict):
    tools = [
        {
            "name": "analyze_ui_elements",
            "description": "分析当前屏幕上的 UI 元素，返回按钮、文字、输入框等的位置和标签",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "detect_buttons": {"type": "boolean", "description": "是否检测按钮"},
                    "extract_text": {"type": "boolean", "description": "是否提取文字"},
                    "confidence_threshold": {"type": "number", "description": "置信度阈值 (0-1)"},
                },
            },
        },
        {
            "name": "capture_and_analyze",
            "description": "截取当前屏幕并用视觉分析，返回屏幕内容描述",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "analysis_prompt": {"type": "string", "description": "分析提示词"},
                },
            },
        },
        {
            "name": "extract_text_from_screen",
            "description": "从屏幕截图中提取所有文字",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "region": {
                        "type": "object",
                        "properties": {
                            "x": {"type": "integer"},
                            "y": {"type": "integer"},
                            "width": {"type": "integer"},
                            "height": {"type": "integer"},
                        },
                        "description": "可选，指定区域",
                    },
                },
            },
        },
    ]

    _send({
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {"tools": tools},
    })


def _capture_screen():
    """截图并返回 numpy array (BGR)"""
    from utils.screen_capture import SCREENSHOT_ENABLED
    if not SCREENSHOT_ENABLED:
        return None

    import tempfile
    import os
    try:
        # macOS screencapture 不支持输出到 stdout，使用临时文件
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp_path = tmp.name
        tmp.close()

        result = subprocess.run(
            ["screencapture", "-x", "-C", "-t", "png", tmp_path],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0 or not os.path.exists(tmp_path):
            return None

        img_data = np.frombuffer(open(tmp_path, "rb").read(), dtype=np.uint8)
        os.unlink(tmp_path)
        return _cv2.imdecode(img_data, _cv2.IMREAD_COLOR)
    except Exception:
        return None


def _detect_elements(img, detect_buttons=True, extract_text=True, confidence=0.3):
    """检测屏幕中的 UI 元素"""
    elements = []
    height, width = img.shape[:2]

    # 1. OCR 文字区域检测（应用 CLAHE 增强对比度）
    if extract_text and _ocr is not None:
        try:
            # CLAHE 增强 - 提高低对比度屏幕的 OCR 识别率
            gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
            clahe = _cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)
            enhanced_bgr = _cv2.cvtColor(enhanced, _cv2.COLOR_GRAY2BGR)

            result = _ocr(enhanced_bgr)
            if result:
                items = result[0] if isinstance(result, tuple) else result
                if items:
                    for item in items:
                        if len(item) < 3:
                            continue
                        box, text, score = item[0], item[1], item[2]
                        # score 可能是字符串，统一转 float
                        try:
                            score_f = float(score) if score is not None else 0.0
                        except (ValueError, TypeError):
                            score_f = 0.0
                        if score_f < confidence:
                            continue
                        if text is None:
                            text = ""
                        # box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] 或 [x1,y1,x2,y2]
                        if isinstance(box, list) and len(box) >= 4:
                            if isinstance(box[0], list):
                                xs = [int(p[0]) for p in box]
                                ys = [int(p[1]) for p in box]
                                x1, y1 = min(xs), min(ys)
                                x2, y2 = max(xs), max(ys)
                            else:
                                x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
                            elements.append({
                                "type": "text",
                                "label": str(text)[:120],
                                "bbox": [x1, y1, x2, y2],
                                "center_x": (x1 + x2) // 2,
                                "center_y": (y1 + y2) // 2,
                                "confidence": round(score_f, 2),
                            })
        except Exception:
            pass

    # 2. 按钮检测（基于轮廓分析）
    if detect_buttons:
        gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
        _, binary = _cv2.threshold(gray, 200, 255, _cv2.THRESH_BINARY_INV)
        contours, _ = _cv2.findContours(binary, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            x, y, w, h = _cv2.boundingRect(contour)
            if w < 30 or h < 15 or w > width * 0.8 or h > height * 0.8:
                continue
            aspect_ratio = w / h
            if aspect_ratio < 0.5 or aspect_ratio > 5:
                continue
            elements.append({
                "type": "button",
                "label": "",
                "bbox": [x, y, x + w, y + h],
                "center_x": x + w // 2,
                "center_y": y + h // 2,
                "confidence": 0.5,
            })

    return elements


def _handle_analyze_ui_elements(params: dict):
    img = _capture_screen()
    if img is None:
        return {"content": [{"type": "text", "text": "截图失败"}]}

    elements = _detect_elements(
        img,
        detect_buttons=params.get("detect_buttons", True),
        extract_text=params.get("extract_text", True),
        confidence=params.get("confidence_threshold", 0.5),
    )

    lines = [f"截图大小: {img.shape[1]}x{img.shape[0]}"]
    lines.append(f"检测到 {len(elements)} 个元素:")
    for el in elements[:30]:
        label = el.get("label", "") or f"({el['type']})"
        bbox = el["bbox"]
        lines.append(
            f"  [{el['type']}] \"{label}\" "
            f"位置=({bbox[0]},{bbox[1]})-({bbox[2]},{bbox[3]}) "
            f"置信度={el['confidence']}"
        )

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _handle_capture_and_analyze(params: dict):
    img = _capture_screen()
    if img is None:
        return {"content": [{"type": "text", "text": "截图失败"}]}

    prompt = params.get("analysis_prompt", "描述屏幕内容")

    # 基础图像分析
    height, width = img.shape[:2]
    mean_color = img.mean(axis=(0, 1))
    brightness = mean_color.mean()

    elements = _detect_elements(img)
    text_elements = [e for e in elements if e["type"] == "text"]
    button_like = [e for e in elements if e["type"] == "button"]

    summary = [
        f"屏幕分析 (请求: {prompt})",
        f"分辨率: {width}x{height}",
        f"亮度: {brightness:.0f}/255",
        f"文字区域: {len(text_elements)} 处",
        f"按钮/可交互区域: {len(button_like)} 处",
    ]

    if text_elements:
        summary.append("\n检测到的文字:")
        for el in text_elements[:15]:
            summary.append(f"  \"{el['label']}\"")

    return {"content": [{"type": "text", "text": "\n".join(summary)}]}


def _handle_extract_text(params: dict):
    img = _capture_screen()
    if img is None:
        return {"content": [{"type": "text", "text": "截图失败"}]}

    region = params.get("region")
    if region:
        x, y, w, h = region["x"], region["y"], region["width"], region["height"]
        img = img[y:y + h, x:x + w]

    if _ocr is None:
        return {"content": [{"type": "text", "text": "OCR 引擎不可用，请安装 rapidocr-onnxruntime"}]}

    try:
        # CLAHE 增强
        gray = _cv2.cvtColor(img, _cv2.COLOR_BGR2GRAY)
        clahe = _cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        enhanced_bgr = _cv2.cvtColor(enhanced, _cv2.COLOR_GRAY2BGR)

        result = _ocr(enhanced_bgr)
        texts = []
        if result:
            items = result[0] if isinstance(result, tuple) else result
            if items:
                for item in items:
                    if len(item) < 3:
                        continue
                    text = item[1]
                    if text:
                        texts.append(str(text))
        if texts:
            return {"content": [{"type": "text", "text": "\n".join(texts)}]}
        return {"content": [{"type": "text", "text": "未检测到文字"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"OCR 失败: {e}"}]}


_TOOL_HANDLERS = {
    "analyze_ui_elements": _handle_analyze_ui_elements,
    "capture_and_analyze": _handle_capture_and_analyze,
    "extract_text_from_screen": _handle_extract_text,
}


def _handle_call_tool(req: dict):
    params = req.get("params", {})
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    handler = _TOOL_HANDLERS.get(name)
    if not handler:
        result = {
            "content": [{"type": "text", "text": f"未知工具: {name}"}],
            "isError": True,
        }
    else:
        try:
            result = handler(arguments)
        except Exception as e:
            result = {
                "content": [{"type": "text", "text": f"执行失败: {e}\n{traceback.format_exc()}"}],
                "isError": True,
            }

    _send({
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": result,
    })


def main():
    if not _available:
        error_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32000, "message": "opencv-python 未安装 (pip install opencv-python)"},
        })
        sys.stderr.write(error_msg + "\n")
        sys.stderr.flush()
        return

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = req.get("method", "")
        if method == "initialize":
            _handle_initialize(req)
        elif method == "notifications/initialized":
            pass  # no response needed
        elif method == "tools/list":
            _handle_list_tools(req)
        elif method == "tools/call":
            _handle_call_tool(req)


if __name__ == "__main__":
    main()
