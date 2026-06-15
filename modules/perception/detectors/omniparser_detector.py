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
    """OmniParser UI 结构化检测器（单例）

    二级降级：
    1. GET http://localhost:8000/probe/ → omniparser_http
    2. import omniparser → omniparser_local
    无可用后端时 is_available() 返回 False

    后端探测使用惰性初始化（lazy initialization），
    仅在首次调用 is_available() / detect() / detect_elements() 时执行。
    避免在 __init__ 中阻塞 120+ 秒。

    全局单例，所有调用方共享同一实例。
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    PRECISION_HIGH = "high"
    _AUTO_START_PROBE_RETRIES = 3
    _AUTO_START_PROBE_INTERVAL = 5  # 秒

    def __init__(self, api_url: str = "http://localhost:8000"):
        if hasattr(self, '_init_done'):
            return
        self._api_url = api_url.rstrip("/")
        self._backend: Optional[str] = None
        self._backend_initialized = False  # 惰性初始化标记
        self._local_parser = None
        self._prev_elements: Dict[str, List[UIElement]] = {}
        self._process: Optional[Any] = None  # 自动启动的子进程
        self._log_dir: Optional[str] = None  # 子进程日志目录
        self._init_done = True

    def _ensure_backend(self) -> None:
        """惰性初始化：首次调用时探测可用后端

        线程安全：只执行一次，后续调用直接返回。
        """
        if self._backend_initialized:
            return
        self._backend_initialized = True
        self._detect_backend()

    @staticmethod
    def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
        """TCP 端口检测 — 最可靠的服务器存活判断

        比 HTTP GET /probe/ 更可靠，因为不依赖特定路由是否存在。
        OmniParser 的 uvicorn 在模型加载完成后才开始监听端口，
        所以端口开放 = 模型已就绪。
        """
        import socket
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (OSError, socket.timeout):
            return False

    def _detect_backend(self) -> None:
        """探测可用后端（仅在 _ensure_backend 中调用一次）"""
        host, port_str = self._api_url.rsplit(":", 1)
        host = host.replace("http://", "").replace("https://", "")
        port = int(port_str)

        # 1. TCP 端口检测 — 服务器是否已在运行
        if self._port_open(host, port, timeout=3):
            self._backend = "omniparser_http"
            logger.info(f"OmniParser 后端: HTTP API ({host}:{port})")
            return

        # 2. 尝试自动启动 OmniParser 服务（仅当端口未被占用）
        if self._try_auto_start():
            import time
            # 等待端口开放（Florence2 MPS 加载约 10-20s）
            for attempt in range(self._AUTO_START_PROBE_RETRIES):
                time.sleep(self._AUTO_START_PROBE_INTERVAL)
                if self._port_open(host, port, timeout=3):
                    self._backend = "omniparser_http"
                    logger.info(f"OmniParser 后端: HTTP API（自动启动, attempt={attempt + 1}）")
                    return
                logger.debug(f"OmniParser 服务启动中... 尝试 {attempt + 1}/{self._AUTO_START_PROBE_RETRIES}")
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

        使用 omniparser_launcher.py 作为包装，在子进程中运行时
        应用 PaddleOCR/transformers 兼容性 monkey-patch，
        不修改 OmniParser 源码或 HF 缓存文件。

        - 自动下载缺失的权重
        - 启动后监控进程状态
        - 子进程 stderr 写入日志文件供调试

        Returns:
            True 如果进程已成功启动（不保证 HTTP 已就绪）
        """
        import os
        import subprocess
        import sys
        import time
        import tempfile

        # 检查 OmniParser 目录是否存在
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        omniparser_dir = os.path.join(project_root, "OmniParser")
        if not os.path.isdir(omniparser_dir):
            logger.warning("OmniParser 目录不存在，无法自动启动（执行 install.sh 克隆）")
            return False

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

        # 定位 launcher 脚本（与当前文件同目录）
        launcher_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "omniparser_launcher.py")

        try:
            port = self._api_url.rsplit(":", 1)[-1]

            # 创建日志文件用于捕获子进程 stderr
            log_dir = os.path.join(tempfile.gettempdir(), "omniparser_logs")
            os.makedirs(log_dir, exist_ok=True)
            self._log_dir = log_dir
            stderr_path = os.path.join(log_dir, f"omniparser_port{port}.log")
            stderr_file = open(stderr_path, "w")

            logger.info(f"自动启动 OmniParser 服务 (port={port}, log={stderr_path})...")
            # OmniParser 需要在 omni 环境运行（torch 2.6、transformers 4.49）
            _python = "/opt/anaconda3/envs/omni/bin/python"
            if not os.path.isfile(_python):
                _python = sys.executable  # 兜底用当前环境
            self._process = subprocess.Popen(
                [_python, launcher_path, omniparser_dir, str(port)],
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
            )
            # 释放文件句柄，让子进程持有
            stderr_file.close()

            # 等待几秒确认进程存活
            time.sleep(3)
            if self._process.poll() is not None:
                # 进程已退出，读取日志
                try:
                    with open(stderr_path) as f:
                        stderr_text = f.read()
                    logger.error(
                        f"OmniParser 服务启动后立即退出 (rc={self._process.returncode})\n"
                        f"  stderr: {stderr_text[-500:]}"
                    )
                except Exception:
                    logger.error(
                        f"OmniParser 服务启动后立即退出 (rc={self._process.returncode})"
                    )
                self._process = None
                return False

            logger.info(f"OmniParser 服务已启动 (PID={self._process.pid}, log={stderr_path})")
            return True

        except Exception as e:
            logger.warning(f"自动启动 OmniParser 失败: {e}")
            self._process = None
            return False

    @property
    def detector_type(self) -> str:
        return "ui"

    def is_available(self) -> bool:
        self._ensure_backend()
        return self._backend is not None

    @property
    def precision(self) -> str:
        """精度等级：有后端时 high，否则不可用"""
        self._ensure_backend()
        return self.PRECISION_HIGH if self._backend else "unavailable"

    @property
    def backend(self) -> Optional[str]:
        self._ensure_backend()
        return self._backend

    def detect(
        self,
        roi_image: np.ndarray,
        roi_name: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[PerceptionEvent]:
        """实现 PerceptionDetector 接口 — 发出 SCREEN_UI 事件"""
        self._ensure_backend()
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
        self._ensure_backend()
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
            # 限制最大边长，避免 Retina 屏高分辨率导致 PaddleOCR 过慢
            max_dim = 1600
            if max(w, h) > max_dim:
                ratio = max_dim / max(w, h)
                img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
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
            with urllib.request.urlopen(req, timeout=300) as resp:
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

    def __del__(self):
        """析构时清理子进程，防止孤儿进程"""
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            self._process = None

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
