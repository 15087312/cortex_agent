"""
MCP OCR Server — 通过 MCP 协议提供 OCR 能力

纯独立脚本，仅依赖 PaddleOCR / Pillow / numpy。
不引入项目其他模块。

用法:
  python infra/mcp/servers/ocr_server.py
"""
import json
import sys
import traceback
from pathlib import Path


def _init_ocr():
    """独立初始化 OCR 引擎（不依赖项目模块）"""
    try:
        from paddleocr import PaddleOCR
        engine = PaddleOCR(lang="ch")
        return ("paddle", engine)
    except ImportError:
        pass

    try:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
        return ("rapid", engine)
    except ImportError:
        pass

    return (None, None)


_engine_type, _engine = _init_ocr()
_available = _engine is not None


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
            "serverInfo": {"name": "ocr-server", "version": "1.0.0"},
            "capabilities": {"tools": {}},
        },
    })


def _handle_tools_list(req: dict):
    _send({
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "result": {
            "tools": [
                {
                    "name": "ocr_image",
                    "description": "识别图片中的文字，返回纯文本结果",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "imagePath": {
                                "type": "string",
                                "description": "图片文件路径（PNG/JPG）",
                            },
                        },
                        "required": ["imagePath"],
                    },
                },
            ],
        },
    })


def _handle_tools_call(req: dict):
    params = req.get("params", {})
    name = params.get("name", "")
    arguments = params.get("arguments", {})

    if name == "ocr_image":
        image_path = arguments.get("imagePath", "")
        if not image_path:
            _send(_error(req, "缺少 imagePath 参数"))
            return

        try:
            from PIL import Image
            import numpy as np

            img = Image.open(image_path)
            img_np = np.array(img)

            if img_np.ndim == 3 and img_np.shape[2] == 4:
                img_np = img_np[:, :, :3]

            if _engine_type == "paddle":
                result = _engine.ocr(img_np)
                if result and result[0]:
                    texts = result[0].get("rec_texts", [])
                    text = "\n".join(t for t in texts if t)
                else:
                    text = ""
            elif _engine_type == "rapid":
                result, _ = _engine(img_np)
                if result:
                    text = "\n".join(item[1] for item in result if len(item) > 1)
                else:
                    text = ""
            else:
                text = ""

            _send({
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "result": {
                    "content": [{"type": "text", "text": text or "(未识别到文字)"}],
                },
            })
        except Exception as e:
            _send(_error(req, f"OCR 失败: {traceback.format_exc()}"))
    else:
        _send(_error(req, f"未知工具: {name}"))


def _error(req: dict, msg: str):
    return {
        "jsonrpc": "2.0",
        "id": req.get("id"),
        "error": {"code": -32000, "message": msg},
    }


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            method = req.get("method", "")
            if method == "initialize":
                _handle_initialize(req)
            elif method == "tools/list":
                _handle_tools_list(req)
            elif method == "tools/call":
                _handle_tools_call(req)
            elif method == "notifications/initialized":
                pass
            else:
                pass
        except json.JSONDecodeError:
            continue
        except Exception:
            _send(_error({"id": None}, traceback.format_exc()))


if __name__ == "__main__":
    main()
