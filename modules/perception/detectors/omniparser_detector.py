"""OmniParser UI 结构化检测器 — 二级降级

后端优先级：
1. OmniParser HTTP 服务 (localhost:8000)
2. OmniParser 本地模型 (import omniparser)
无可用后端时不降级到 OCR，返回空结果。

同时实现 PerceptionDetector.detect() 接口，发出 SCREEN_UI 事件。

启动 OmniParser 服务：
  cd OmniParser && python -m omnitool.omniparserserver.omniparserserver \\
    --som_model_path weights/icon_detect/model.pt \\
    --caption_model_name florence2 \\
    --caption_model_path weights/icon_caption_florence \\
    --device cpu \\
    --port 8000
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np

from modules.perception.detectors.base import PerceptionDetector
from modules.perception.events.types import PerceptionEvent, PerceptionEventType
from utils.logger import setup_logger

logger = setup_logger("omniparser_detector")


@dataclass
class UIElement:
    """UI 元素描述"""
    element_id: str = ""       # "e001" 格式
    type: str = "unknown"      # button/input/text/icon/checkbox/link/unknown
    label: str = ""            # 文字内容
    bbox: List[int] = field(default_factory=list)  # [x1, y1, x2, y2] 绝对像素坐标
    center_x: int = 0
    center_y: int = 0
    confidence: float = 0.0
    source: str = ""           # omniparser_http / omniparser_local

    def to_dict(self) -> Dict[str, Any]:
        return {
            "element_id": self.element_id,
            "type": self.type,
            "label": self.label,
            "bbox": self.bbox,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "confidence": self.confidence,
            "source": self.source,
        }


class OmniParserDetector(PerceptionDetector):
    """OmniParser UI 结构化检测器

    二级降级：
    1. GET http://localhost:8000/probe/ → omniparser_http
    2. import omniparser → omniparser_local
    无可用后端时 is_available() 返回 False
    """

    PRECISION_HIGH = "high"

    def __init__(self, api_url: str = "http://localhost:8000"):
        self._api_url = api_url.rstrip("/")
        self._backend: Optional[str] = None
        self._local_parser = None
        self._prev_elements: Dict[str, List[UIElement]] = {}
        self._process: Optional[Any] = None  # 自动启动的子进程
        self._detect_backend()

    def _detect_backend(self) -> None:
        """探测可用后端"""
        # 1. HTTP API（OmniParser server: GET /probe/）
        try:
            import urllib.request
            req = urllib.request.Request(f"{self._api_url}/probe/", method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    self._backend = "omniparser_http"
                    logger.info("OmniParser 后端: HTTP API")
                    return
        except Exception as e:
            logger.debug(f"OmniParser HTTP 探测失败: {e}")

        # 2. 尝试自动启动 OmniParser 服务
        if self._try_auto_start():
            # 等服务器启动后多次重试探测（Florence2 CPU 加载约 30s）
            import time
            for attempt in range(12):
                time.sleep(10)  # 每 10s 探测一次
                try:
                    import urllib.request
                    req = urllib.request.Request(f"{self._api_url}/probe/", method="GET")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        if resp.status == 200:
                            self._backend = "omniparser_http"
                            logger.info("OmniParser 后端: HTTP API（自动启动）")
                            return
                except Exception:
                    logger.debug(f"OmniParser 服务启动中... 尝试 {attempt + 1}/12")
        else:
            logger.debug("自动启动失败")

        # 3. 本地模型
        try:
            import importlib
            importlib.import_module("omniparser")
            self._backend = "omniparser_local"
            logger.info("OmniParser 后端: 本地模型")
            return
        except ImportError as e:
            logger.debug(f"OmniParser 本地模型不可用: {e}")

        # 无可用后端 — 不降级到 OCR，返回不可用
        self._backend = None
        logger.warning("OmniParser 无可用后端（HTTP 服务未运行且无本地模型），UI 检测不可用")

    def _try_auto_start(self) -> bool:
        """尝试自动启动 OmniParser 服务（子进程）

        - 自动下载缺失的权重
        - 设置 PYTHONPATH 使 omnitool 模块可导入
        - 启动后监控进程状态

        Returns:
            True 如果进程已成功启动（不保证 HTTP 已就绪）
        """
        import os
        import subprocess
        import sys
        import time

        # 检查 OmniParser 目录是否存在
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        omniparser_dir = os.path.join(project_root, "OmniParser")
        if not os.path.isdir(omniparser_dir):
            logger.warning("OmniParser 目录不存在，无法自动启动（执行 install.sh 克隆）")
            return False

        # 构建环境变量 — 将 OmniParser 目录加入 PYTHONPATH，使 omnitool 模块可找到
        env = os.environ.copy()
        old_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{omniparser_dir}:{old_pythonpath}" if old_pythonpath else omniparser_dir

        # 自动修复 OmniParser util/utils.py 的 transformers 5.x 兼容性
        utils_path = os.path.join(omniparser_dir, "util", "utils.py")
        if os.path.isfile(utils_path):
            try:
                with open(utils_path) as f:
                    src = f.read()
                modified = False
                # 1) 将多参数 PaddleOCR(...) 替换为兼容的极简调用
                idx = src.find("PaddleOCR(")
                if idx >= 0:
                    depth = 0; end = idx
                    for i in range(idx, len(src)):
                        if src[i] == "(": depth += 1
                        elif src[i] == ")":
                            depth -= 1
                            if depth == 0: end = i + 1; break
                    old_call = src[idx:end]
                    if old_call != "PaddleOCR(lang='en')":
                        src = src[:idx] + "PaddleOCR(lang='en')" + src[end:]
                        modified = True
                # 2) florence2 段：替换 .to(device) 链式调用 + 移除 import torch 冲突
                if "florence2" in src:
                    old = 'trust_remote_code=True).to(device)'
                    new = 'trust_remote_code=True)\n        has_meta = any(p.device.type == "meta" for p in model.parameters())\n        if has_meta:\n            model.to_empty(device="cpu")\n        model = model.to(device)'
                    if old in src:
                        src = src.replace(old, new)
                        modified = True
                if modified:
                    with open(utils_path, "w") as f:
                        f.write(src)
                    logger.info("已自动修复 OmniParser utils.py 兼容性")
            except Exception as e:
                logger.debug(f"OmniParser utils.py 自动修复失败: {e}")

        # 自动修复 omniparserserver.py — reload=True → False（解决 -m 模式下模块引用）
        server_path = os.path.join(omniparser_dir, "omnitool", "omniparserserver", "omniparserserver.py")
        if os.path.isfile(server_path):
            try:
                with open(server_path) as f:
                    src = f.read()
                modified = False
                # 修正 app 引用路径
                if '"omniparserserver:app"' in src:
                    src = src.replace('"omniparserserver:app"', '"omnitool.omniparserserver.omniparserserver:app"')
                    modified = True
                if 'reload=True' in src:
                    src = src.replace('reload=True', 'reload=False')
                    modified = True
                if modified:
                    with open(server_path, "w") as f:
                        f.write(src)
                    logger.info("已自动修复 OmniParser uvicorn 启动参数")
            except Exception as e:
                logger.debug(f"OmniParser 兼容性自动修复失败: {e}")

        # 自动修复 Florence2 + transformers 5.x 兼容性（list→dict _tied_weights_keys + model. 前缀）
        # 定位 HF 缓存中的 Florence2 modeling 文件
        import glob as _glob
        florence_files = _glob.glob(
            os.path.expanduser("~/.cache/huggingface/modules/transformers_modules/microsoft/*_hyphen_*/**/modeling_florence2.py"),
            recursive=True,
        )
        for ff in florence_files:
            try:
                with open(ff) as f:
                    src = f.read()
                # 将 list 格式的 _tied_weights_keys 转为 dict 格式
                import re as _re
                pattern = r'_tied_weights_keys = \[([^\]]+)\]'
                def _to_dict(m):
                    items = [x.strip().strip('"').strip("'") for x in m.group(1).split(',')]
                    items = [x for x in items if x]
                    pairs = ', '.join(f'"{k}": "{k}"' for k in items)
                    return f'_tied_weights_keys = {{{pairs}}}'
                new_src = _re.sub(pattern, _to_dict, src)
                if new_src != src:
                    with open(ff, 'w') as f:
                        f.write(new_src)
                    logger.info(f"已自动修复 Florence2 兼容性: {os.path.basename(ff)}")
            except Exception as e:
                logger.debug(f"Florence2 自动修复失败: {e}")

        # 检查权重，缺失时尝试自动下载
        weights_dir = os.path.join(omniparser_dir, "weights")
        weights_missing = not os.path.isdir(weights_dir) or not os.listdir(weights_dir)

        if weights_missing:
            logger.info("OmniParser 权重未下载，尝试自动下载 (~1.5GB)...")
            try:
                result = subprocess.run(
                    [sys.executable, "scripts/setup_models.py", "omniparser"],
                    cwd=project_root,
                    capture_output=True, text=True, timeout=900,
                )
                if result.returncode == 0:
                    logger.info("OmniParser 权重下载完成")
                else:
                    logger.warning(f"权重下载失败 (rc={result.returncode}): {result.stderr[-300:]}")
            except subprocess.TimeoutExpired:
                logger.warning("权重下载超时（>15min），跳过")
            except Exception as e:
                logger.warning(f"自动下载权重失败: {e}")

        try:
            port = self._api_url.rsplit(":", 1)[-1]
            abs_weights = os.path.join(omniparser_dir, "weights")
            som_model = os.path.join(abs_weights, "icon_detect", "model.pt")
            caption_model = os.path.join(abs_weights, "icon_caption_florence")

            logger.info(f"自动启动 OmniParser 服务 (port={port})...")
            self._process = subprocess.Popen(
                [sys.executable, "-m", "omnitool.omniparserserver.omniparserserver",
                 "--port", str(port), "--device", "cpu",
                 "--som_model_path", som_model,
                 "--caption_model_path", caption_model],
                cwd=omniparser_dir,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # 等待几秒确认进程存活
            time.sleep(3)
            if self._process.poll() is not None:
                # 进程已退出 — 捕获输出
                stdout, stderr = self._process.communicate(timeout=5)
                logger.error(
                    f"OmniParser 服务启动后立即退出 (rc={self._process.returncode})\n"
                    f"  stdout: {(stdout or b'').decode('utf-8', errors='replace')[-500:]}\n"
                    f"  stderr: {(stderr or b'').decode('utf-8', errors='replace')[-500:]}"
                )
                self._process = None
                return False

            logger.info(f"OmniParser 服务已启动 (PID={self._process.pid})")
            return True

        except Exception as e:
            # 捕获子进程本身的异常
            if self._process and self._process.poll() is not None:
                stdout, stderr = self._process.communicate(timeout=3)
                logger.error(f"自动启动异常后子进程输出: {(stderr or b'').decode('utf-8', errors='replace')[-300:]}")
            self._process = None
            logger.warning(f"自动启动 OmniParser 失败: {e}")
            return False

    @property
    def detector_type(self) -> str:
        return "ui"

    def is_available(self) -> bool:
        return self._backend is not None

    @property
    def precision(self) -> str:
        """精度等级：有后端时 high，否则不可用"""
        return self.PRECISION_HIGH if self._backend else "unavailable"

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """实现 PerceptionDetector 接口 — 发出 SCREEN_UI 事件"""
        if roi_image is None or roi_image.size == 0:
            return []

        elements = self.detect_elements(roi_image)
        if not elements:
            return []

        # 与上一次对比
        prev = self._prev_elements.get(roi_name, [])
        self._prev_elements[roi_name] = elements

        if _elements_equal(prev, elements):
            return []

        event = PerceptionEvent(
            event_type=PerceptionEventType.SCREEN_UI,
            source=self._backend or "unknown",
            importance=0.7,
            roi_name=roi_name,
            payload={
                "elements": [e.to_dict() for e in elements],
                "element_count": len(elements),
                "backend": self._backend,
                "changed": True,
            },
        )
        return [event]

    def detect_elements(self, screenshot: Any) -> List[UIElement]:
        """主接口：截图 → UI 元素列表

        Args:
            screenshot: bytes 或 numpy ndarray 格式的截图

        Returns:
            UIElement 列表（无可用后端时返回空列表）
        """
        if not self._backend:
            return []

        # 统一转为 numpy array
        if isinstance(screenshot, bytes):
            import io
            from PIL import Image
            img = Image.open(io.BytesIO(screenshot))
            img_array = np.array(img.convert("RGB"))
        elif isinstance(screenshot, np.ndarray):
            img_array = screenshot
        else:
            logger.warning(f"不支持的截图格式: {type(screenshot)}")
            return []

        if self._backend == "omniparser_http":
            return self._detect_http(img_array)
        elif self._backend == "omniparser_local":
            return self._detect_local(img_array)
        return []

    def _detect_http(self, image: np.ndarray) -> List[UIElement]:
        """通过 HTTP API 调用 OmniParser（POST /parse/）"""
        try:
            import io
            import json
            import base64
            import urllib.request
            import urllib.error
            from PIL import Image

            img = Image.fromarray(image)
            w, h = img.size
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            img_b64 = base64.b64encode(buf.getvalue()).decode()

            payload = json.dumps({"base64_image": img_b64}).encode()
            req = urllib.request.Request(
                f"{self._api_url}/parse/",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode())

            elements = []
            # parsed_content_list: [{"type": "text"|"icon", "bbox": [x1,y1,x2,y2] (0-1), "content": "..."}]
            for i, item in enumerate(result.get("parsed_content_list", [])):
                bbox_ratio = item.get("bbox", [0, 0, 0, 0])
                if not isinstance(bbox_ratio, (list, tuple)) or len(bbox_ratio) < 4:
                    continue  # 跳过无效 bbox
                # 比例坐标 → 绝对像素
                bbox = [
                    int(bbox_ratio[0] * w), int(bbox_ratio[1] * h),
                    int(bbox_ratio[2] * w), int(bbox_ratio[3] * h),
                ]
                cx = (bbox[0] + bbox[2]) // 2
                cy = (bbox[1] + bbox[3]) // 2
                content = item.get("content") or ""
                elem_type = item.get("type", "unknown")
                # 映射类型
                type_map = {"text": "text", "icon": "icon", "button": "button", "input": "input"}
                elem_type = type_map.get(elem_type, elem_type)

                elements.append(UIElement(
                    element_id=f"e{i + 1:03d}",
                    type=elem_type,
                    label=content,
                    bbox=bbox,
                    center_x=cx,
                    center_y=cy,
                    confidence=0.9,
                    source="omniparser_http",
                ))
            return elements
        except urllib.error.HTTPError as e:
            # 服务端错误（500）
            logger.warning(f"OmniParser HTTP {e.code}: {e.reason}")
            return []
        except urllib.error.URLError as e:
            # 网络错误：服务不可用，返回空
            logger.error(f"OmniParser 服务不可用: {e.reason}")
            return []
        except Exception as e:
            logger.warning(f"OmniParser HTTP 调用异常: {e}")
            return []

    def _detect_local(self, image: np.ndarray) -> List[UIElement]:
        """通过本地 OmniParser 模型"""
        try:
            if self._local_parser is None:
                from omniparser import Omniparser
                self._local_parser = Omniparser()

            result = self._local_parser.parse(image)
            elements = []
            for i, item in enumerate(result.get("elements", [])):
                bbox = item.get("bbox", [0, 0, 0, 0])
                cx = (bbox[0] + bbox[2]) // 2 if len(bbox) >= 4 else 0
                cy = (bbox[1] + bbox[3]) // 2 if len(bbox) >= 4 else 0
                elements.append(UIElement(
                    element_id=f"e{i + 1:03d}",
                    type=item.get("type", "unknown"),
                    label=item.get("text", ""),
                    bbox=bbox,
                    center_x=cx,
                    center_y=cy,
                    confidence=item.get("confidence", 0.85),
                    source="omniparser_local",
                ))
            return elements
        except Exception as e:
            logger.warning(f"OmniParser 本地模型调用失败: {e}")
            return []

    def reset(self) -> None:
        """重置状态，清理子进程"""
        self._prev_elements.clear()
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                pass
            self._process = None


def _elements_equal(a: List[UIElement], b: List[UIElement]) -> bool:
    """快速比较两组 UI 元素是否相同"""
    if len(a) != len(b):
        return False
    for ea, eb in zip(a, b):
        if ea.label != eb.label or ea.type != eb.type or ea.bbox != eb.bbox:
            return False
    return True
