"""
图像分析核心 - 支持本地多模态模型 + 云API
支持：Qwen-VL (MLX/transformers) / LLaVA / GPT-4V / UI检测 / 模拟模式

平台适配:
  - macOS (Apple Silicon): 优先 mlx-vlm (4-bit量化, ~4GB), 回退 transformers+mps
  - Windows/Linux (CUDA):   transformers + CUDA
  - CPU 兜底:              transformers + float32
"""
import base64
import io
import sys
import tempfile
import json
import random
from typing import Dict, Any, List, Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
from utils.logger import setup_logger

logger = setup_logger("image_analyzer")

# 平台检测
_IS_APPLE_SILICON = sys.platform == "darwin" and hasattr(__import__("platform"), "machine") and __import__("platform").machine() == "arm64"


class ImageAnalyzer:
    """图像分析器"""

    def __init__(
        self,
        model_type: str = "auto",
        local_model: str = None
    ):
        """
        初始化图像分析器
        
        Args:
            model_type: auto/qwen_vl/llava/openai/mock
            local_model: 本地模型名称
        """
        self.model_type = model_type
        self.local_model = local_model
        self.model = None
        self.processor = None
        self._initialized = False

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
        elif self.model_type == "unavailable":
            logger.error("视觉后端不可用")
        else:
            logger.info("使用模拟模式")
        
        self._initialized = True
        logger.info(f"图像分析器初始化完成 (类型: {self.model_type})")

    def _detect_available_model(self) -> str:
        """根据配置和平台自动选择视觉后端（不可用返回 unavailable，不降级 mock）"""
        from config.settings import settings

        backend = settings.VISION_BACKEND.lower().strip()

        # 用户显式指定后端
        if backend and backend != "auto":
            if backend == "api":
                if settings.effective_vision_api_key:
                    return "openai"
                logger.error("VISION_BACKEND=api 但无 API Key")
                return "unavailable"
            elif backend == "mlx":
                return "mlx_vlm"
            elif backend == "transformers":
                return "qwen_vl"
            elif backend == "mock":
                return "mock"
            else:
                logger.error(f"未知 VISION_BACKEND: {backend}")
                return "unavailable"

        # auto 模式: api > mlx > transformers > unavailable
        # 1) 云端 API（有 API Key 即可用）
        if settings.effective_vision_api_key:
            logger.info("视觉后端: 云端 API")
            return "openai"

        # 2) Apple Silicon: mlx-vlm
        if _IS_APPLE_SILICON:
            try:
                from mlx_vlm import generate, load
                logger.info("视觉后端: MLX-VLM (Apple Silicon)")
                return "mlx_vlm"
            except ImportError:
                logger.debug("mlx-vlm 未安装，尝试 transformers")

        # 3) transformers + Qwen2-VL (CUDA/MPS/CPU)
        try:
            from transformers import Qwen2VLForConditionalGeneration
            from qwen_vl_utils import process_vision_info
            logger.info("视觉后端: transformers (本地模型)")
            return "qwen_vl"
        except ImportError:
            pass

        try:
            import llava
            return "llava"
        except ImportError:
            pass

        logger.error("无可用视觉后端（未安装 mlx-vlm / transformers / 无 API Key）")
        return "unavailable"

    async def _load_mlx_vlm(self):
        """加载 MLX-VLM 模型（Apple Silicon 优化，4-bit 量化）"""
        try:
            from mlx_vlm import load, generate
            from config.settings import settings

            model_name = self.local_model or settings.effective_vision_mlx_model

            logger.info(f"MLX-VLM 加载中: {model_name}")
            model, processor = load(model_name)
            self.model = model
            self.processor = processor
            self._mlx_generate = generate
            self._mlx_model_name = model_name
            logger.info(f"MLX-VLM 模型加载成功: {model_name}")
        except Exception as e:
            logger.error(f"MLX-VLM 加载失败: {e}")
            self.model_type = "unavailable"

    async def _analyze_mlx_vlm(
        self,
        image_data: bytes,
        prompt: str,
    ) -> Dict[str, Any]:
        """使用 MLX-VLM 分析图像（Apple Silicon）"""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            f.write(image_data)
            temp_path = f.name

        try:
            from mlx_vlm.prompt_utils import apply_chat_template
            from mlx_vlm.utils import load_config

            config = load_config(self._mlx_model_name)

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
                self.processor, config, prompt, num_images=1
            )

            output = self._mlx_generate(
                self.model,
                self.processor,
                formatted_prompt,
                [temp_path],
                max_tokens=256,
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
            self.model = Qwen2VLForConditionalGeneration.from_pretrained(
                model_name,
                torch_dtype=torch.float16 if device == "cuda" else torch.float32,
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

    async def _load_llava(self):
        """加载LLaVA模型"""
        try:
            import llava
            self.model = llava.load_img
            logger.info("LLaVA模型加载成功")
        except ImportError:
            logger.warning("LLaVA未安装，使用模拟模式")
            self.model_type = "mock"

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
        elif self.model_type == "llava":
            return await self._analyze_llava(image_data, prompt)
        elif self.model_type == "openai":
            return await self._analyze_openai(image_data, prompt)
        elif self.model_type == "unavailable":
            return {"error": "视觉后端不可用：未安装 mlx-vlm/transformers 且未配置 VISION_API_KEY", "format": "unavailable"}
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
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as f:
            f.write(image_data)
            temp_path = f.name
        
        try:
            image = Image.open(temp_path)
            
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

    async def _analyze_llava(
        self,
        image_data: bytes,
        prompt: str
    ) -> Dict[str, Any]:
        """使用LLaVA分析"""
        import llava
        
        image = Image.open(io.BytesIO(image_data))
        response = llava.conversation([
            {"role": "user", "content": prompt}
        ], image)
        
        return {
            "description": response,
            "objects": [],
            "scene": "unknown",
            "colors": [],
            "format": "llava"
        }

    async def _analyze_openai(
        self,
        image_data: bytes,
        prompt: str
    ) -> Dict[str, Any]:
        """使用云端视觉 API 分析（OpenAI / DashScope / 兼容接口）"""
        from config.settings import settings
        import openai

        image_b64 = base64.b64encode(image_data).decode()

        api_key = settings.effective_vision_api_key
        api_url = settings.effective_vision_api_url
        model = settings.effective_vision_api_model

        client = openai.AsyncOpenAI(api_key=api_key, base_url=api_url, timeout=30.0)
        response = await client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        await client.close()

        return {
            "description": response.choices[0].message.content,
            "objects": [],
            "scene": "unknown",
            "colors": [],
            "format": "openai"
        }

    async def _analyze_mock(
        self,
        image_data: bytes,
        prompt: str
    ) -> Dict[str, Any]:
        """模拟分析"""
        try:
            image = Image.open(io.BytesIO(image_data))
            width, height = image.size
            mode = image.mode
        except Exception:
            width, height, mode = 0, 0, "unknown"
        
        return {
            "description": f"[模拟分析] 这是一张 {width}x{height} 的 {mode} 图像。{prompt}",
            "objects": [
                {"label": "物体1", "confidence": 0.9, "bbox": [10, 10, 100, 100]},
                {"label": "物体2", "confidence": 0.8, "bbox": [120, 50, 200, 150]}
            ],
            "scene": "室内场景",
            "colors": ["blue", "white", "gray"],
            "width": width,
            "height": height,
            "format": "mock"
        }

    async def _detect_objects(self, image_data: bytes) -> List[Dict[str, Any]]:
        """目标检测（简化版）"""
        try:
            image = Image.open(io.BytesIO(image_data))
            return [
                {"label": "物体", "confidence": 0.9, "bbox": [0, 0, 100, 100]}
            ]
        except Exception:
            return []

    async def _classify_scene(self, image_data: bytes) -> str:
        """场景分类"""
        try:
            image = Image.open(io.BytesIO(image_data))
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
        prompt = """分析这张截图，找出所有UI元素。对于每个元素，请输出：
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
        """使用OpenAI检测UI元素"""
        image_b64 = base64.b64encode(image_data).decode()

        from config.settings import settings
        import openai

        client = openai.AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY,
            base_url=settings.OPENAI_API_BASE_URL,
            timeout=30.0,
        )
        prompt = """分析这张截图，找出所有UI元素。输出JSON格式：
{"elements": [
  {"type":"button","text":"确定","bounds":[100,200,180,230],"colors":{"bg":"#2196F3","text":"#FFFFFF"}},
  {"type":"input","text":"","bounds":[50,100,300,140],"colors":{"bg":"#FFFFFF","border":"#CCCCCC"}}
]}

每行一个元素，bounds为[x1,y1,x2,y2]像素坐标。"""

        response = await client.chat.completions.create(
            model=settings.IMAGE_MODEL_NAME,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": prompt}
                ]
            }]
        )
        await client.close()
        
        try:
            return json.loads(response.choices[0].message.content)
        except Exception:
            return await self._detect_ui_mock(image_data, element_types)

    async def _detect_ui_mock(
        self,
        image_data: bytes,
        element_types: List[str]
    ) -> Dict[str, Any]:
        """模拟UI元素检测（用于测试）"""
        try:
            image = Image.open(io.BytesIO(image_data))
            width, height = image.size
        except Exception:
            width, height = 1920, 1080
        
        elements = [
            {
                "type": "button",
                "text": "提交",
                "bounds": {"x": 800, "y": 500, "width": 120, "height": 40},
                "center": {"x": 860, "y": 520},
                "colors": {"bg": "#3498db", "text": "#ffffff"},
                "confidence": 0.95
            },
            {
                "type": "input",
                "text": "",
                "placeholder": "请输入...",
                "bounds": {"x": 600, "y": 400, "width": 400, "height": 50},
                "center": {"x": 800, "y": 425},
                "colors": {"bg": "#ffffff", "border": "#cccccc"},
                "confidence": 0.90
            },
            {
                "type": "text",
                "text": "欢迎使用",
                "bounds": {"x": 700, "y": 300, "width": 200, "height": 30},
                "center": {"x": 800, "y": 315},
                "colors": {"text": "#333333"},
                "confidence": 0.99
            },
            {
                "type": "icon",
                "text": "菜单",
                "bounds": {"x": 50, "y": 50, "width": 40, "height": 40},
                "center": {"x": 70, "y": 70},
                "colors": {"bg": "transparent", "icon": "#666666"},
                "confidence": 0.85
            },
            {
                "type": "link",
                "text": "了解更多",
                "bounds": {"x": 750, "y": 600, "width": 100, "height": 25},
                "center": {"x": 800, "y": 612},
                "colors": {"text": "#0066cc"},
                "confidence": 0.92
            }
        ]
        
        return {
            "elements": elements,
            "layout": {
                "width": width,
                "height": height,
                "grid": self._estimate_grid(width, height)
            },
            "summary": f"检测到 {len(elements)} 个UI元素"
        }

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
