"""
图像分析核心 - 支持本地多模态模型 + 云API
支持：Qwen-VL (MLX/transformers) / LLaVA / GPT-4V / UI检测

平台适配:
  - macOS (Apple Silicon): 优先 mlx-vlm (4-bit量化, ~4GB), 回退 transformers+mps
  - Windows/Linux (CUDA):   transformers + CUDA
  - CPU 兜底:              transformers + float32
"""
import base64
import io
import sys
import tempfile
import time
import json
import random
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw
from utils.logger import setup_logger

logger = setup_logger("image_analyzer")

# 平台检测
_IS_APPLE_SILICON = sys.platform == "darwin" and hasattr(__import__("platform"), "machine") and __import__("platform").machine() == "arm64"

# 全局模型缓存（避免重复加载）
_MODEL_CACHE = {
    "mlx_vlm": None,
    "qwen_vl": None,
    "llava": None,
}


class ImageAnalyzer:
    """图像分析器 - 单例模式避免重复初始化"""

    _instance: 'ImageAnalyzer' = None  # 单例实例

    def __new__(cls, model_type: str = "auto", local_model: str = None):
        """单例工厂 — 返回已有实例，参数不匹配时记录警告"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        else:
            # 已有实例但参数不同 → 记录警告（不阻止，调用方自行决定是否接受）
            if model_type != cls._instance._init_model_type:
                logger.warning(
                    f"ImageAnalyzer 已存在（model_type={cls._instance._init_model_type}），"
                    f"忽略新参数 model_type={model_type}；如需切换后端请调用 ensure_model_type()"
                )
        return cls._instance

    def __init__(
        self,
        model_type: str = "auto",
        local_model: str = None
    ):
        """
        初始化图像分析器（单例模式，只初始化一次）

        Args:
            model_type: auto/qwen_vl/llava/openai/mock
            local_model: 本地模型名称
        """
        # 避免重复初始化
        if hasattr(self, '_init_done'):
            return

        self.model_type = model_type
        self._init_model_type = model_type  # 记录初始化时的参数，用于检测参数不匹配
        self.local_model = local_model
        self.model = None
        self.processor = None
        self._initialized = False
        self._init_done = True

    async def initialize(self):
        """初始化模型"""
        if self._initialized:
            return
        
        if self.model_type == "auto":
            self.model_type = self._detect_available_model()

        if self.model_type == "mlx_vlm":
            await self._load_mlx_vlm()
        elif self.model_type == "qwen_vl":
            await self._load_qwen_vl()
        elif self.model_type == "llava":
            await self._load_llava()
        elif self.model_type == "openai":
            await self._init_openai()
        else:
            raise ValueError(f"不支持的视觉后端类型: {self.model_type}")

        self._initialized = True
        logger.info(f"图像分析器初始化完成 (类型: {self.model_type})")

    def _detect_available_model(self) -> str:
        """根据配置选择视觉后端（api / local）"""
        from config.settings import settings

        backend = settings.VISION_BACKEND.lower().strip()

        if backend == "api":
            if settings.effective_vision_api_key:
                return "openai"
            logger.error("VISION_BACKEND=api 但无 API Key")
            return "unavailable"

        # local 模式: 根据用户选择的模型决定加载器
        local_model = settings.effective_vision_local_model

        # 如果用户指定了模型路径，检查其 config.json 确定格式
        if local_model:
            from pathlib import Path
            cfg_path = Path(local_model) / "config.json"
            if cfg_path.exists():
                try:
                    import json
                    with open(cfg_path) as f:
                        cfg = json.load(f)
                    arch = cfg.get("architectures", [""])[0] if cfg.get("architectures") else ""
                    model_type = cfg.get("model_type", "")

                    # MLX 模型通常有 mlx_vlm 相关字段
                    if "mlx" in local_model.lower() or "mlx" in model_type:
                        if _IS_APPLE_SILICON:
                            try:
                                from mlx_vlm import generate, load  # noqa: F401
                                return "mlx_vlm"
                            except ImportError:
                                pass

                    # Qwen2-VL / Qwen-VL 系列
                    if "qwen" in model_type.lower() or "Qwen2VL" in arch:
                        try:
                            from transformers import Qwen2VLForConditionalGeneration  # noqa: F401
                            from qwen_vl_utils import process_vision_info  # noqa: F401
                            return "qwen_vl"
                        except ImportError:
                            pass

                    # LLaVA 系列
                    if "llava" in model_type.lower():
                        try:
                            import llava  # noqa: F401
                            return "llava"
                        except ImportError:
                            pass

                    # 通用 transformers 模型 — 尝试 AutoModel
                    try:
                        from transformers import AutoModelForCausalLM, AutoProcessor  # noqa: F401
                        return "qwen_vl"  # 用 qwen_vl 路径加载通用模型
                    except ImportError:
                        pass
                except Exception as e:
                    logger.debug(f"解析模型 config.json 失败: {e}")

        # 无指定模型或解析失败，按库可用性自动检测
        # 1) Apple Silicon: mlx-vlm
        if _IS_APPLE_SILICON:
            try:
                from mlx_vlm import generate, load  # noqa: F401
                logger.info("视觉后端: MLX-VLM (Apple Silicon)")
                return "mlx_vlm"
            except ImportError:
                pass

        # 2) transformers + Qwen2-VL
        try:
            from transformers import Qwen2VLForConditionalGeneration  # noqa: F401
            from qwen_vl_utils import process_vision_info  # noqa: F401
            logger.info("视觉后端: transformers (本地模型)")
            return "qwen_vl"
        except ImportError:
            pass

        try:
            import llava  # noqa: F401
            logger.info("视觉后端: LLaVA (本地模型)")
            return "llava"
        except ImportError:
            pass

        # 3) 本地模型都不可用，回退到云端 API
        if settings.effective_vision_api_key:
            logger.info("视觉后端: 云端 API (本地模型不可用)")
            return "openai"

        logger.error("无可用视觉后端（未安装 mlx-vlm / transformers / 无 API Key）")
        return "unavailable"

    async def _load_mlx_vlm(self):
        """加载 MLX-VLM 模型（Apple Silicon 优化，4-bit 量化）- 使用全局缓存"""
        global _MODEL_CACHE
        import asyncio

        try:
            from mlx_vlm import load, generate
            from config.settings import settings

            model_name = self.local_model or settings.effective_vision_local_model or settings.effective_vision_mlx_model

            # 检查缓存
            if _MODEL_CACHE.get("mlx_vlm") and _MODEL_CACHE["mlx_vlm"].get("name") == model_name:
                logger.info(f"MLX-VLM 从缓存加载: {model_name}")
                cached = _MODEL_CACHE["mlx_vlm"]
                self.model = cached["model"]
                self.processor = cached["processor"]
                self._mlx_generate = cached["generate"]
                self._mlx_model_name = model_name
                return

            logger.info(f"MLX-VLM 首次加载: {model_name}")
            # load() 是同步阻塞操作（加载 ~4GB 模型），放在线程池执行避免阻塞事件循环
            model, processor = await asyncio.to_thread(lambda: load(model_name))
            self.model = model
            self.processor = processor
            self._mlx_generate = generate
            self._mlx_model_name = model_name

            # 保存到缓存
            _MODEL_CACHE["mlx_vlm"] = {
                "name": model_name,
                "model": model,
                "processor": processor,
                "generate": generate,
            }
            logger.info(f"MLX-VLM 模型加载成功并已缓存: {model_name}")
            self._mlx_config = None  # 延迟加载，由 _analyze_mlx_vlm 缓存

            # ═══════════════════════════════════════════════════════════════
            # 重要：warmup 推理
            # 触发 MLX JIT 编译 + Metal 着色器缓存，避免首次调用 150s+ 冷启动惩罚
            # 实验数据：无 warmup 首次推理 ~153s，有 warmup 后 ~7s
            # 必须通过 asyncio.to_thread 执行（与真实推理路径一致），因为
            # Metal 着色器缓存是 per-thread 的，主线程 warmup 无法预热工作线程
            # 警告：禁止删除此 warmup，禁止改为直接调用而非 to_thread
            # ═══════════════════════════════════════════════════════════════
            try:
                logger.info("MLX-VLM warmup 推理中...")
                _warm_start = time.time()
                import os as _os
                import tempfile as _tf
                from PIL import Image as _PIL
                from mlx_vlm.utils import load_config as _load_config
                from mlx_vlm.prompt_utils import apply_chat_template as _apply_template
                _img = _PIL.new("RGB", (200, 200), color=128)
                _buf = io.BytesIO()
                _img.save(_buf, format="JPEG")
                with _tf.NamedTemporaryFile(delete=False, suffix=".jpg") as _f:
                    _f.write(_buf.getvalue())
                    _warmup_path = _f.name
                _warm_config = _load_config(self._mlx_model_name)
                _warm_messages = [
                    {"role": "user", "content": [
                        {"type": "image", "image": _warmup_path},
                        {"type": "text", "text": "describe"},
                    ]}
                ]
                _warm_prompt = _apply_template(self.processor, _warm_config, _warm_messages, num_images=1)
                # 使用 to_thread 预热工作线程池中的 Metal 着色器缓存
                await asyncio.to_thread(
                    self._mlx_generate,
                    self.model, self.processor,
                    _warm_prompt,
                    [_warmup_path],
                    max_tokens=1, temperature=0, verbose=False,
                )
                _os.unlink(_warmup_path)
                logger.info(f"MLX-VLM warmup 完成 ({time.time()-_warm_start:.1f}s)")
            except Exception as e:
                logger.warning(f"MLX-VLM warmup 失败 (非致命): {e}")
                logger.debug("warmup 异常详情", exc_info=True)
        except Exception as e:
            logger.error(f"MLX-VLM 加载失败: {e}")
            self.model_type = "unavailable"

    async def _analyze_mlx_vlm(
        self,
        image_data: bytes,
        prompt: str,
    ) -> Dict[str, Any]:
        """使用 MLX-VLM 分析图像（Apple Silicon）"""
        from PIL import Image as _PIL
        import io as _io

        # macOS 截图可能含 alpha 通道，JPEG 不支持 RGBA，务必先转 RGB
        # 警告：禁止删除此转换，否则 Pillow 保存 JPEG 时会抛 OSError
        _img = _PIL.open(_io.BytesIO(image_data))
        if _img.mode == "RGBA":
            _img = _img.convert("RGB")
        # 限制最大边长，加速 ViT 推理（屏幕理解不需要超高分辨率）
        # 原 768 对全屏截图仍产生大量 ViT patch（~1800+），大幅拖慢推理
        _max_dim = 512
        if max(_img.size) > _max_dim:
            ratio = _max_dim / max(_img.size)
            _img = _img.resize((int(_img.width * ratio), int(_img.height * ratio)), _PIL.LANCZOS)
        buf = _io.BytesIO()
        _img.save(buf, format="JPEG", quality=85)
        image_data = buf.getvalue()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            f.write(image_data)
            temp_path = f.name

        try:
            if self._mlx_config is None:
                from mlx_vlm.utils import load_config
                self._mlx_config = load_config(self._mlx_model_name)
            config = self._mlx_config

            from mlx_vlm.prompt_utils import apply_chat_template

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": temp_path},
                        {"type": "text", "text": prompt},
                    ],
                }
            ]
            formatted_prompt = apply_chat_template(
                self.processor, config, messages, num_images=1
            )

            # ── 同步调用 self._mlx_generate ──
            # _analyze_mlx_vlm 通过 MCP 执行路径（_run_async_in_thread）已运行在
            # 独立线程 + 独立事件循环中，不存在阻塞主事件循环的问题。
            # 若再加一层 asyncio.to_thread，会导致：
            #   1. _run_async_in_thread 的 loop.shutdown_default_executor()
            #      在 cleanup 时中断正在执行的 to_thread 任务
            #   2. Python 3.13 下触发 PyThreadState_Get crash
            # 警告：禁止加 asyncio.to_thread 包裹
            output = self._mlx_generate(
                self.model,
                self.processor,
                formatted_prompt,
                [temp_path],
                max_tokens=80,
                temperature=0,
                verbose=False,
            )

            output_text = output.text if hasattr(output, "text") else str(output)

            return {
                "description": output_text.strip(),
                "objects": await self._detect_objects(image_data),
                "scene": await self._classify_scene(image_data),
                "colors": [],
                "format": "mlx_vlm",
            }
        finally:
            import os
            os.unlink(temp_path)

    async def _load_qwen_vl(self):
        """加载Qwen-VL模型"""
        try:
            import torch
            from transformers import Qwen2VLForConditionalGeneration, AutoProcessor
            from qwen_vl_utils import process_vision_info
            from config.settings import settings

            model_name = self.local_model or settings.effective_vision_local_model

            # 选择设备
            if torch.cuda.is_available():
                device = "cuda"
            elif hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                device = "mps"
            else:
                device = "cpu"

            logger.info(f"Qwen-VL 加载中: {model_name} (device={device})")

            self.processor = AutoProcessor.from_pretrained(model_name)
            dtype = torch.float16 if device in ("cuda", "mps") else torch.float32
            self.model = Qwen2VLForConditionalGeneration.from_pretrained(
                model_name,
                torch_dtype=dtype,
                device_map=device if device != "mps" else None,
            )
            if device == "mps":
                self.model = self.model.to("mps")

            self._process_vl = process_vision_info
            self._device = device
            logger.info(f"Qwen-VL 模型加载成功 (device={device})")
        except Exception as e:
            logger.error(f"Qwen-VL 加载失败: {e}")
            self.model_type = "unavailable"



    async def _init_openai(self):
        """初始化云端视觉 API"""
        from config.settings import settings
        api_url = settings.effective_vision_api_url
        api_key = settings.effective_vision_api_key
        model = settings.effective_vision_api_model
        if not api_key:
            logger.error("视觉 API 无 Key，不可用")
            self.model_type = "unavailable"
            return
        logger.info(f"视觉 API 初始化: model={model}, url={api_url}")

    async def analyze(
        self,
        image_data: bytes,
        prompt: str = "详细描述这张图片"
    ) -> Dict[str, Any]:
        """
        分析图像
        
        Args:
            image_data: 图像字节数据
            prompt: 分析提示词
        
        Returns:
            分析结果
        """
        if not self._initialized:
            await self.initialize()
        
        if self.model_type == "mlx_vlm":
            return await self._analyze_mlx_vlm(image_data, prompt)
        elif self.model_type == "qwen_vl":
            return await self._analyze_qwen_vl(image_data, prompt)
        elif self.model_type == "openai":
            return await self._analyze_openai(image_data, prompt)
        elif self.model_type == "unavailable":
            return {
                "error": "视觉后端不可用：未安装 mlx-vlm/transformers 且未配置 VISION_API_KEY",
                "description": "",
                "format": "unavailable"
            }
        else:
            return await self._analyze_mock(image_data, prompt)

    async def _analyze_qwen_vl(
        self,
        image_data: bytes,
        prompt: str
    ) -> Dict[str, Any]:
        """使用Qwen-VL分析"""
        import torch
        from qwen_vl_utils import process_vision_info
        
        # 限制最大边长，加速 ViT 推理
        _img = Image.open(io.BytesIO(image_data))
        _max_dim = 768
        if max(_img.size) > _max_dim:
            ratio = _max_dim / max(_img.size)
            _img = _img.resize((int(_img.width * ratio), int(_img.height * ratio)), Image.LANCZOS)
            buf = io.BytesIO()
            _img.save(buf, format="JPEG", quality=85)
            image_data = buf.getvalue()

        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            f.write(image_data)
            temp_path = f.name
        
        try:
            Image.open(temp_path)
            
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": temp_path},
                        {"type": "text", "text": prompt}
                    ]
                }
            ]
            
            text = self.processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
            image_inputs, video_inputs = process_vision_info(messages)
            
            inputs = self.processor(
                text=[text],
                images=image_inputs,
                videos=video_inputs,
                padding=True,
                return_tensors="pt"
            )
            inputs = inputs.to(getattr(self, '_device', 'cpu'))
            
            with torch.no_grad():
                output_ids = self.model.generate(**inputs, max_new_tokens=128)
            
            generated_ids = [
                output_ids[len(input_ids):]
                for input_ids, output_ids in zip(inputs.input_ids, output_ids)
            ]
            
            output_text = self.processor.batch_decode(
                generated_ids, skip_special_tokens=True, clean_up_tokenization_spaces=True
            )[0]
            
            return {
                "description": output_text.strip(),
                "objects": await self._detect_objects(image_data),
                "scene": await self._classify_scene(image_data),
                "colors": [],
                "format": "qwen_vl"
            }
        finally:
            import os
            os.unlink(temp_path)



    async def _analyze_openai(
        self,
        image_data: bytes,
        prompt: str
    ) -> Dict[str, Any]:
        """使用云端视觉 API 分析（OpenAI / DeepSeek / DashScope / 兼容接口）"""
        from config.settings import settings
        import openai
        import httpx

        image_b64 = base64.b64encode(image_data).decode()

        api_key = settings.effective_vision_api_key
        api_url = settings.effective_vision_api_url
        # 去除 base_url 尾部 /chat/completions，避免 openai SDK 再拼接导致双重路径 404
        # （与 config/providers/openai.py:76-78 的处理保持一致）
        api_url = api_url.rstrip("/")
        if api_url.endswith("/chat/completions"):
            api_url = api_url.rsplit("/chat/completions", 1)[0]
        model = settings.effective_vision_api_model

        # 创建自定义 httpx client，避免 openai 库的 proxies 参数错误
        http_client = httpx.AsyncClient(timeout=30.0, trust_env=True)
        client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=api_url,
            http_client=http_client
        )

        try:
            # 检测 API 类型：DeepSeek/DashScope 走 base64 内联，OpenAI 走 image_url。
            # 优先用 VISION_API_FORMAT 配置，未配置时按 URL 推断。
            api_format = str(getattr(settings, "VISION_API_FORMAT", "") or "").lower()
            is_deepseek = api_format in ("deepseek", "dashscope") or (
                not api_format and "deepseek" in api_url.lower()
            )

            if is_deepseek:
                # DeepSeek: 使用 base64 图片内联在 content 中
                messages = [{
                    "role": "user",
                    "content": f"[image: data:image/jpeg;base64,{image_b64}]\n{prompt}"
                }]
            else:
                # OpenAI/DashScope: 使用标准 image_url 格式
                messages = [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }]

            response = await client.chat.completions.create(
                model=model,
                messages=messages
            )

            return {
                "description": response.choices[0].message.content,
                "objects": [],
                "scene": "unknown",
                "colors": [],
                "format": "openai"
            }
        finally:
            await client.close()



    async def _detect_objects(self, image_data: bytes) -> List[Dict[str, Any]]:
        """目标检测（简化版）"""
        try:
            Image.open(io.BytesIO(image_data))
            return [
                {"label": "物体", "confidence": 0.9, "bbox": [0, 0, 100, 100]}
            ]
        except Exception:
            return []

    async def _classify_scene(self, image_data: bytes) -> str:
        """场景分类"""
        try:
            Image.open(io.BytesIO(image_data))
            return "场景"
        except Exception:
            return "unknown"

    async def analyze_base64(
        self,
        image_b64: str,
        prompt: str = "详细描述这张图片"
    ) -> Dict[str, Any]:
        """分析Base64编码的图像"""
        image_data = base64.b64decode(image_b64)
        return await self.analyze(image_data, prompt)

    async def close(self):
        """关闭模型"""
        if self.model is not None:
            del self.model
            self.model = None
        self.processor = None
        if hasattr(self, '_mlx_generate'):
            self._mlx_generate = None
        self._initialized = False
        self.model_type = self._init_model_type

    async def ensure_model_type(self, model_type: str) -> None:
        """确保使用指定后端，必要时重新初始化

        允许在运行时切换视觉后端（例如：本地模型失败后切换到 API）。
        如果当前后端与目标一致且已初始化，跳过。
        如果不一致，关闭当前模型后重新初始化。
        """
        if self._initialized and self.model_type == model_type:
            return
        if self._initialized:
            await self.close()
        self.model_type = model_type
        await self.initialize()

    async def detect_ui_elements(
        self,
        image_data: bytes,
        element_types: List[str] = None
    ) -> Dict[str, Any]:
        """
        检测UI元素（按钮、输入框、图标等）
        
        Args:
            image_data: 图像数据
            element_types: 要检测的元素类型列表
        
        Returns:
            {
                "elements": [
                    {
                        "type": "button",
                        "text": "提交",
                        "bounds": {"x": 100, "y": 200, "width": 80, "height": 30},
                        "center": {"x": 140, "y": 215},
                        "colors": {"bg": "#3498db", "text": "#ffffff"},
                        "confidence": 0.95
                    },
                    ...
                ],
                "layout": {
                    "width": 1920,
                    "height": 1080,
                    "grid": "3x4"
                }
            }
        """
        if not self._initialized:
            await self.initialize()
        
        if self.model_type in ("qwen_vl", "mlx_vlm"):
            return await self._detect_ui_qwen_vl(image_data, element_types)
        elif self.model_type == "openai":
            return await self._detect_ui_openai(image_data, element_types)
        else:
            return await self._detect_ui_mock(image_data, element_types)

    async def _detect_ui_qwen_vl(
        self,
        image_data: bytes,
        element_types: List[str]
    ) -> Dict[str, Any]:
        """使用Qwen-VL检测UI元素"""
        prompt = """你是纯视觉描述模块，只客观描述屏幕上实际可见的 UI 元素，不做任何评价、不给任何操作建议或引导。
分析这张截图，找出所有UI元素。对于每个元素，请输出：
1. 类型（button/input/icon/link/text/image/container）
2. 文字内容（如果有）
3. 精确位置 [x1,y1,x2,y2] 左上角到右下角
4. 背景颜色和文字颜色（如果有）

严格按JSON格式输出：
{"elements": [{"type":"","text":"","bounds":[x1,y1,x2,y2],"colors":{}}]}

只输出JSON，不要其他文字。"""

        analysis = await self._analyze_qwen_vl(image_data, prompt)
        
        try:
            elements_data = json.loads(analysis.get("description", "{}"))
            return elements_data
        except Exception:
            return await self._detect_ui_mock(image_data, element_types)

    async def _detect_ui_openai(
        self,
        image_data: bytes,
        element_types: List[str]
    ) -> Dict[str, Any]:
        """使用 OpenAI/DeepSeek 检测 UI 元素"""
        image_b64 = base64.b64encode(image_data).decode()

        from config.settings import settings
        import openai
        import httpx

        # 创建自定义 httpx client，避免 openai 库的 proxies 参数错误
        http_client = httpx.AsyncClient(timeout=30.0, trust_env=True)
        api_url = settings.OPENAI_API_BASE_URL
        # 去除 base_url 尾部 /chat/completions，避免 openai SDK 再拼接导致双重路径 404
        api_url = api_url.rstrip("/")
        if api_url.endswith("/chat/completions"):
            api_url = api_url.rsplit("/chat/completions", 1)[0]
        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=api_url,
            http_client=http_client,
        )

        prompt = """你是纯视觉描述模块，只客观描述屏幕上实际可见的 UI 元素，不做任何评价、不给任何操作建议或引导。
分析这张截图，找出所有UI元素。输出JSON格式：
{"elements": [
  {"type":"button","text":"确定","bounds":[100,200,180,230],"colors":{"bg":"#2196F3","text":"#FFFFFF"}},
  {"type":"input","text":"","bounds":[50,100,300,140],"colors":{"bg":"#FFFFFF","border":"#CCCCCC"}}
]}

每行一个元素，bounds为[x1,y1,x2,y2]像素坐标。"""

        try:
            # 检测 API 类型：DeepSeek 不支持 image_url，需要使用 base64 内联格式
            is_deepseek = "deepseek" in settings.OPENAI_API_BASE_URL.lower()

            if is_deepseek:
                # DeepSeek: 使用 base64 图片内联在 content 中
                messages = [{
                    "role": "user",
                    "content": f"[image: data:image/jpeg;base64,{image_b64}]\n{prompt}"
                }]
            else:
                # OpenAI/DashScope: 使用标准 image_url 格式
                messages = [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                        {"type": "text", "text": prompt}
                    ]
                }]

            response = await client.chat.completions.create(
                model=settings.IMAGE_MODEL_NAME,
                messages=messages
            )

            try:
                return json.loads(response.choices[0].message.content)
            except Exception:
                return await self._detect_ui_mock(image_data, element_types)
        finally:
            await client.close()



    def _estimate_grid(self, width: int, height: int) -> str:
        """估算布局网格"""
        cols = max(1, round(width / 400))
        rows = max(1, round(height / 300))
        return f"{cols}x{rows}"

    def draw_elements(
        self,
        image_data: bytes,
        elements: List[Dict],
        output_path: str = None
    ) -> bytes:
        """
        在图像上绘制UI元素标注
        
        Args:
            image_data: 原始图像
            elements: UI元素列表
            output_path: 输出文件路径
        
        Returns:
            标注后的图像字节
        """
        try:
            image = Image.open(io.BytesIO(image_data)).convert("RGB")
            draw = ImageDraw.Draw(image)
            
            type_colors = {
                "button": "#e74c3c",
                "input": "#3498db",
                "text": "#2ecc71",
                "icon": "#9b59b6",
                "link": "#f39c12",
                "container": "#95a5a6"
            }
            
            for elem in elements:
                bounds = elem.get("bounds", {})
                x1 = bounds.get("x", 0)
                y1 = bounds.get("y", 0)
                x2 = x1 + bounds.get("width", 50)
                y2 = y1 + bounds.get("height", 30)
                
                color = type_colors.get(elem.get("type", "text"), "#ffffff")
                
                draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
                
                label = f"{elem.get('type', '?')} | {elem.get('text', '')}"
                if len(label) > 25:
                    label = label[:22] + "..."
                draw.text((x1 + 5, y1 + 5), label, fill=color)
            
            if output_path:
                image.save(output_path)
            
            output = io.BytesIO()
            image.save(output, format="PNG")
            return output.getvalue()
            
        except Exception as e:
            logger.error(f"绘制UI元素失败: {e}")
            return image_data

    @staticmethod
    def get_click_point(
        element: Dict[str, Any],
        offset_range: int = 5,
        random_seed: int = None
    ) -> Tuple[int, int]:
        """
        获取点击坐标（带随机偏移，防止被检测）
        
        Args:
            element: UI元素
            offset_range: 偏移范围（像素），默认5像素
            random_seed: 随机种子（用于调试）
        
        Returns:
            (x, y) 点击坐标
        """
        if random_seed is not None:
            random.seed(random_seed)
        
        center = element.get("center", {})
        cx = center.get("x", 0)
        cy = center.get("y", 0)
        
        offset_x = random.randint(-offset_range, offset_range)
        offset_y = random.randint(-offset_range, offset_range)
        
        return (cx + offset_x, cy + offset_y)

    @staticmethod
    def find_element_by_text(
        elements: List[Dict],
        text: str,
        element_type: str = None
    ) -> Optional[Dict[str, Any]]:
        """
        根据文字查找UI元素
        
        Args:
            elements: UI元素列表
            text: 要查找的文字
            element_type: 元素类型过滤
        
        Returns:
            匹配的UI元素，未找到返回None
        """
        for elem in elements:
            if element_type and elem.get("type") != element_type:
                continue
            elem_text = elem.get("text", "")
            if text in elem_text or elem_text in text:
                return elem
        return None

    @staticmethod
    def find_element_by_color(
        elements: List[Dict],
        color_hex: str
    ) -> Optional[Dict[str, Any]]:
        """
        根据颜色查找UI元素
        
        Args:
            elements: UI元素列表
            color_hex: 颜色值，如 "#3498db"
        
        Returns:
            匹配的UI元素
        """
        for elem in elements:
            colors = elem.get("colors", {})
            if any(color_hex in str(v) for v in colors.values()):
                return elem
        return None


class UIClickHelper:
    """
    UI点击辅助类
    封装查找元素 + 计算坐标 + 生成点击指令
    """

    def __init__(self, analyzer: ImageAnalyzer = None):
        self.analyzer = analyzer or ImageAnalyzer()
        self._elements = []
        self._image_data = None

    async def detect_from_image(self, image_data: bytes) -> List[Dict]:
        """从图像检测UI元素"""
        self._image_data = image_data
        result = await self.analyzer.detect_ui_elements(image_data)
        self._elements = result.get("elements", [])
        return self._elements

    def set_elements(self, elements: List[Dict]):
        """直接设置UI元素列表"""
        self._elements = elements

    def find_by_text(self, text: str, elem_type: str = None) -> Optional[Dict]:
        """根据文字查找"""
        return ImageAnalyzer.find_element_by_text(self._elements, text, elem_type)

    def find_by_color(self, color: str) -> Optional[Dict]:
        """根据颜色查找"""
        return ImageAnalyzer.find_element_by_color(self._elements, color)

    def get_click_point(
        self,
        element: Dict,
        offset_range: int = 5
    ) -> Tuple[int, int]:
        """获取随机点击坐标"""
        return ImageAnalyzer.get_click_point(element, offset_range)

    def click(
        self,
        text: str = None,
        color: str = None,
        elem_type: str = None,
        offset_range: int = 5
    ) -> Optional[Tuple[int, int]]:
        """
        查找并计算点击坐标
        
        Args:
            text: 元素文字
            color: 元素颜色
            elem_type: 元素类型
            offset_range: 随机偏移范围
        
        Returns:
            (x, y) 点击坐标
        """
        element = None
        
        if text:
            element = self.find_by_text(text, elem_type)
        elif color:
            element = self.find_by_color(color)
        
        if not element:
            return None
        
        return self.get_click_point(element, offset_range)

    async def analyze_with_coordinates(
        self,
        image_data: bytes,
        query: str = "找出所有可点击的元素"
    ) -> Dict[str, Any]:
        """
        带坐标的图像分析
        
        回答"按钮在哪里"、"某个元素是什么颜色"等问题
        
        Args:
            image_data: 图像数据
            query: 分析查询
        
        Returns:
            {
                "answer": "在坐标(100,200)处有一个蓝色按钮'提交'",
                "elements": [...],
                "coordinates": {"x": 100, "y": 200}
            }
        """
        elements = await self.detect_ui_elements(image_data)
        
        analysis_prompt = f"""{query}

检测到的UI元素：
{json.dumps(elements.get('elements', []), ensure_ascii=False, indent=2)}

请根据以上元素信息回答问题，引用具体的坐标和颜色。"""

        if self.model_type == "openai":
            result = await self._analyze_openai(image_data, analysis_prompt)
            answer = result.get("description", "")
        else:
            result = await self._analyze_mock(image_data, analysis_prompt)
            answer = result.get("description", "")
        
        return {
            "answer": answer,
            "elements": elements.get("elements", []),
            "layout": elements.get("layout", {}),
            "raw_query": query
        }


_default_analyzer: Optional[ImageAnalyzer] = None


async def get_default_analyzer() -> ImageAnalyzer:
    """获取默认分析器单例"""
    global _default_analyzer
    if _default_analyzer is None:
        _default_analyzer = ImageAnalyzer()
        await _default_analyzer.initialize()
    return _default_analyzer
