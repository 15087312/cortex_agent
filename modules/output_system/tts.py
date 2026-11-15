"""
TTS 引擎 — 文本转语音输出

基于 edge-tts（Microsoft Edge Text-to-Speech）：
- 免费、无需 API Key、国内直连、中文音质好
- 输出 MP3 到 data/output/
- 依赖缺失时优雅降级（返回 None，不影响主流程）

配置：
- OUTPUT_TTS_ENABLED: 总开关（默认 False）
- OUTPUT_TTS_LANGUAGE: 合成语言（默认 zh）
- OUTPUT_TTS_OUTPUT_DIR: 输出目录（默认 data/output）
"""
import asyncio
import time
import uuid
from pathlib import Path
from typing import Optional, cast

from config.settings import settings
from utils.logger import setup_logger

logger = setup_logger("tts_engine")

# 默认输出目录（供 api/main.py 静态挂载引用）
# 相对路径一律按项目根解析：静态挂载若按 cwd 解析，从别的目录启动服务时
# /audio 就指向了别处——合成的文件存在、前端却 404（「没声音」的静默形态）。
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TTS_OUTPUT_DIR = Path(getattr(settings, "OUTPUT_TTS_OUTPUT_DIR", "data/output"))
if not DEFAULT_TTS_OUTPUT_DIR.is_absolute():
    DEFAULT_TTS_OUTPUT_DIR = _PROJECT_ROOT / DEFAULT_TTS_OUTPUT_DIR


def get_tts_output_dir() -> Path:
    """端口优先取TTS 输出目录：接管 ``tts_output_dir`` 端口 = 换掉全部调用点。

    取不到端口才回退内置单例（脚本、没跑过 bootstrap 的入口）。bootstrap 注册
    的 provider 必须是 ``_default_tts_output_dir`` 而不是本函数，否则端口→本函数→端口
    一调用就递归。
    """
    from infra.tool_manager.service_registry import get_capability

    factory = get_capability("tts_output_dir")
    if factory is None:
        return _default_tts_output_dir()
    return factory()


def _default_tts_output_dir() -> Path:
    """公开访问器：TTS 输出目录（api 静态挂载等外部消费者经此获取，不直接引用内部常量）。"""
    return DEFAULT_TTS_OUTPUT_DIR


class TTSEngine:
    """文本转语音引擎（edge-tts）"""

    # edge-tts 中文语音映射
    _VOICE_MAP = {
        "zh": "zh-CN-XiaoxiaoNeural",
        "en": "en-US-AriaNeural",
        "ja": "ja-JP-NanamiNeural",
        "ko": "ko-KR-SunHiNeural",
    }

    def __init__(self, output_dir: Optional[str] = None):
        resolved = Path(cast(str, output_dir)) if output_dir else DEFAULT_TTS_OUTPUT_DIR
        if not resolved.is_absolute():
            resolved = _PROJECT_ROOT / resolved
        self.output_dir = resolved
        self.language = getattr(settings, "OUTPUT_TTS_LANGUAGE", "zh")
        self._available: Optional[bool] = None

    @property
    def enabled(self) -> bool:
        """TTS 总开关"""
        return bool(getattr(settings, "OUTPUT_TTS_ENABLED", False))

    @property
    def available(self) -> bool:
        """edge-tts 依赖是否可用（惰性探测 + 缓存）"""
        if self._available is None:
            try:
                import edge_tts  # noqa: F401
                self._available = True
            except ImportError:
                self._available = False
        return self._available

    def synthesize_sync(self, text: str, language: Optional[str] = None) -> Optional[str]:
        """同步合成语音，返回音频文件路径；失败/禁用返回 None"""
        if not text or not text.strip():
            return None
        if not self.enabled:
            logger.info("TTS 未启用（OUTPUT_TTS_ENABLED=false），跳过合成")
            return None

        from config.settings import settings
        backend = getattr(settings, "OUTPUT_TTS_BACKEND", "local")
        if backend == "api":
            return self._synthesize_api(text, language)
        return self._synthesize_edge(text, language)

    def _synthesize_edge(self, text: str, language: Optional[str] = None) -> Optional[str]:
        """edge-tts 合成（Microsoft，国内直连）"""
        if not self.available:
            logger.warning("edge-tts 依赖不可用，请安装: pip install edge-tts")
            return None

        try:
            import edge_tts
            import asyncio

            lang = language or self.language
            voice = self._VOICE_MAP.get(lang, f"{lang}-{lang.upper()}-Neural")
            # 兜底：如果映射里没有，用中文
            if lang not in self._VOICE_MAP:
                voice = self._VOICE_MAP["zh"]

            self.output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"tts_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3"
            file_path = self.output_dir / filename

            async def _do():
                comm = edge_tts.Communicate(text, voice)
                await comm.save(str(file_path))

            # 如果已在事件循环内，用线程池避免嵌套
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_do())
            else:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    pool.submit(lambda: asyncio.run(_do())).result()

            if file_path.exists() and file_path.stat().st_size > 0:
                logger.info(f"TTS 合成成功: {file_path} ({file_path.stat().st_size} bytes)")
                return str(file_path)
            logger.error("TTS 合成失败: 输出文件为空")
            return None
        except Exception as e:
            logger.error(f"TTS 合成失败: {e}")
            return None

    def _synthesize_api(self, text: str, language: Optional[str] = None) -> Optional[str]:
        """云端 TTS（OpenAI 兼容 /audio/speech）——配置 Key 后使用"""
        try:
            import requests
            from config.settings import settings
            url = getattr(settings, "OUTPUT_TTS_API_URL", "") \
                or "https://api.openai.com/v1/audio/speech"
            key = getattr(settings, "OUTPUT_TTS_API_KEY", "") \
                or getattr(settings, "OPENAI_API_KEY", "")
            model = getattr(settings, "OUTPUT_TTS_API_MODEL", "") or "tts-1"
            voice = getattr(settings, "OUTPUT_TTS_API_VOICE", "") or "alloy"
            if not key:
                logger.warning("TTS 云端 API 未配置 Key，请设置 OUTPUT_TTS_API_KEY")
                return None

            self.output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"tts_api_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3"
            file_path = self.output_dir / filename

            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json={"model": model, "input": text, "voice": voice},
                timeout=30,
            )
            resp.raise_for_status()
            file_path.write_bytes(resp.content)
            logger.info(f"TTS 云端合成成功: {file_path} ({file_path.stat().st_size} bytes)")
            return str(file_path)
        except Exception as e:
            logger.error(f"TTS 云端合成失败: {e}")
            return None

    async def synthesize(self, text: str, language: Optional[str] = None) -> Optional[str]:
        """异步合成语音（在线程池执行，避免阻塞事件循环）"""
        return await asyncio.to_thread(self.synthesize_sync, text, language)


# 全局单例
tts_engine = TTSEngine()
