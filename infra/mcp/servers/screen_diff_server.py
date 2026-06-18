"""
MCP Screen Diff Server — 像素级帧差检测

作为 MCP stdio server 运行，通过 MCP_SERVERS 配置启动。
独立子进程，维护帧缓冲，持续检测屏幕像素变化。

暴露工具:
- check_screen_changes: 截图 + 帧差分析，返回变化面积和区域
- capture_screenshot: 截取当前屏幕并返回 base64

依赖:
- screencapture (macOS 内置)
- opencv-python (可选，降级到 numpy)
"""
import base64
import io
import json
import subprocess
import sys
import tempfile
import os
import traceback
import time

import numpy as np

_available = True
_cv2 = None

try:
    import cv2
    _cv2 = cv2
except ImportError:
    _available = False


def _send(msg: dict):
    line = json.dumps(msg, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


# ── 帧缓冲 ──

_prev_frame: np.ndarray | None = None
_frame_count: int = 0


def _capture_screen() -> np.ndarray | None:
    """截图并返回 numpy array (BGR)"""
    try:
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
        if _cv2:
            return _cv2.imdecode(img_data, _cv2.IMREAD_COLOR)
        else:
            # numpy-only: 用 PIL 兜底
            from PIL import Image
            buf = io.BytesIO(img_data.tobytes())
            pil_img = Image.open(io.BytesIO(open(tmp_path, "rb").read()))
            return np.array(pil_img)[:, :, ::-1]  # RGB → BGR
    except Exception:
        return None


def _compute_frame_diff(current: np.ndarray, prev: np.ndarray) -> dict:
    """计算两帧之间的像素差异

    Returns:
        {has_changed, change_ratio, changed_regions, width, height}
    """
    if current.shape != prev.shape:
        return {
            "has_changed": True,
            "change_ratio": 1.0,
            "changed_regions": [],
            "width": current.shape[1],
            "height": current.shape[0],
        }

    # 转灰度
    if len(current.shape) == 3:
        if _cv2:
            gray_curr = _cv2.cvtColor(current, _cv2.COLOR_BGR2GRAY)
            gray_prev = _cv2.cvtColor(prev, _cv2.COLOR_BGR2GRAY)
        else:
            gray_curr = current[:, :, 1].astype(np.int16)
            gray_prev = prev[:, :, 1].astype(np.int16)
    else:
        gray_curr = current.astype(np.int16) if _cv2 is None else current
        gray_prev = prev.astype(np.int16) if _cv2 is None else prev

    total_pixels = current.shape[0] * current.shape[1]

    if _cv2:
        # 高斯模糊去噪
        blurred_curr = _cv2.GaussianBlur(gray_curr, (5, 5), 0)
        blurred_prev = _cv2.GaussianBlur(gray_prev, (5, 5), 0)
        diff = _cv2.absdiff(blurred_curr, blurred_prev)
        _, thresh = _cv2.threshold(diff, 25, 255, _cv2.THRESH_BINARY)

        # 形态学去噪
        kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (3, 3))
        thresh = _cv2.morphologyEx(thresh, _cv2.MORPH_CLOSE, kernel)
        thresh = _cv2.morphologyEx(thresh, _cv2.MORPH_OPEN, kernel)

        changed_pixels = _cv2.countNonZero(thresh)
        change_ratio = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        # 提取变化区域
        regions = []
        contours, _ = _cv2.findContours(thresh, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = _cv2.contourArea(contour)
            if area >= 200:
                x, y, w, h = _cv2.boundingRect(contour)
                regions.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h)})

        return {
            "has_changed": change_ratio >= 0.01,
            "change_ratio": round(change_ratio, 4),
            "changed_regions": regions[:20],
            "width": current.shape[1],
            "height": current.shape[0],
        }
    else:
        # numpy 降级
        diff = np.abs(gray_curr - gray_prev)
        thresh = (diff > 25).astype(np.uint8)
        changed_pixels = np.count_nonzero(thresh)
        change_ratio = changed_pixels / total_pixels if total_pixels > 0 else 0.0

        regions = []
        if change_ratio >= 0.01:
            ys, xs = np.where(thresh > 0)
            if len(ys) > 0:
                regions.append({
                    "x": int(xs.min()), "y": int(ys.min()),
                    "w": int(xs.max() - xs.min()),
                    "h": int(ys.max() - ys.min()),
                })

        return {
            "has_changed": change_ratio >= 0.01,
            "change_ratio": round(change_ratio, 4),
            "changed_regions": regions,
            "width": current.shape[1],
            "height": current.shape[0],
        }


# ── MCP handler ──


def _handle_initialize(req: dict):
    _send({
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "screen_diff", "version": "0.1.0"},
        },
    })


def _handle_list_tools(req: dict):
    tools = [
        {
            "name": "check_screen_changes",
            "description": "截取当前屏幕并与上一帧比较，检测像素级变化，返回变化比例和区域",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "capture_screenshot",
            "description": "截取当前屏幕并返回 base64 编码的 PNG 图像",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "get_stats",
            "description": "获取帧差检测统计信息（总帧数、变化检测次数等）",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    _send({
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {"tools": tools},
    })


def _handle_check_screen_changes(_params: dict) -> dict:
    global _prev_frame, _frame_count

    frame = _capture_screen()
    if frame is None:
        return {"content": [{"type": "text", "text": json.dumps({"error": "截图失败"})}]}

    _frame_count += 1

    if _prev_frame is None:
        # 首帧，无上一帧（返回变化比例 100%）
        _prev_frame = frame
        result_data = {
            "changed": True,
            "change_ratio": 1.0,
            "regions": [],
            "width": frame.shape[1],
            "height": frame.shape[0],
            "frame_count": _frame_count,
        }
        return {"content": [{"type": "text", "text": json.dumps(result_data)}]}

    diff_result = _compute_frame_diff(frame, _prev_frame)
    _prev_frame = frame

    result_data = {
        "changed": diff_result["has_changed"],
        "change_ratio": diff_result["change_ratio"],
        "regions": diff_result["changed_regions"],
        "width": diff_result["width"],
        "height": diff_result["height"],
        "frame_count": _frame_count,
    }
    return {"content": [{"type": "text", "text": json.dumps(result_data)}]}





def _handle_capture_screenshot(_params: dict) -> dict:
    frame = _capture_screen()
    if frame is None:
        return {"content": [{"type": "text", "text": "截图失败"}]}

    if _cv2:
        _, buffer = _cv2.imencode(".png", frame)
        b64 = base64.b64encode(buffer.tobytes()).decode("utf-8")
    else:
        # numpy 降级: PIL 编码
        from PIL import Image
        rgb = frame[:, :, ::-1]  # BGR → RGB
        pil_img = Image.fromarray(rgb)
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "mime_type": "image/png",
                "data": b64,
                "width": frame.shape[1],
                "height": frame.shape[0],
            }),
        }]
    }


def _handle_get_stats(_params: dict) -> dict:
    global _frame_count, _prev_frame
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({
                "frame_count": _frame_count,
                "has_previous_frame": _prev_frame is not None,
            }),
        }]
    }


_TOOL_HANDLERS = {
    "check_screen_changes": _handle_check_screen_changes,
    "capture_screenshot": _handle_capture_screenshot,
    "get_stats": _handle_get_stats,
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

    # 心跳日志（2分钟一次）
    _log_heartbeat_interval = 120.0
    _last_heartbeat_log = time.time()

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
            pass
        elif method == "tools/list":
            _handle_list_tools(req)
        elif method == "tools/call":
            _handle_call_tool(req)

        # 心跳日志（不依赖 stderr 事件）
        now = time.time()
        if now - _last_heartbeat_log >= _log_heartbeat_interval:
            _last_heartbeat_log = now
            pass  # 日志由主进程通过 MCP get_stats 查看


if __name__ == "__main__":
    main()
