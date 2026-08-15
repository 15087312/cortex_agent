"""
TTS 引擎 — 文本转语音输出

基于 gTTS（Google Text-to-Speech）：
- 免费、无需 API Key、中文支持良好
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
DEFAULT_TTS_OUTPUT_DIR = Path(getattr(settings, "OUTPUT_TTS_OUTPUT_DIR", "data/output"))


class TTSEngine:
    """文本转语音引擎（gTTS）"""

    def __init__(self, output_dir: Optional[str] = None):
        # 实例化时读取配置（支持运行时热更新，不绑定 import 时快照）
        self.output_dir = Path(cast(str,
            output_dir
            or getattr(settings, "OUTPUT_TTS_OUTPUT_DIR", "data/output")
        ))
        self.language = getattr(settings, "OUTPUT_TTS_LANGUAGE", "zh")
        self._available: Optional[bool] = None

    @property
    def enabled(self) -> bool:
        """TTS 总开关"""
        return bool(getattr(settings, "OUTPUT_TTS_ENABLED", False))

    @property
    def available(self) -> bool:
        """gTTS 依赖是否可用（惰性探测 + 缓存）"""
        if self._available is None:
            try:
                import gtts  # noqa: F401
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
        return self._synthesize_gtts(text, language)

    def _synthesize_gtts(self, text: str, language: Optional[str] = None) -> Optional[str]:
        """内置 gTTS 合成（本地默认）"""
        if not self.available:
            logger.warning("gTTS 依赖不可用，请安装: pip install gTTS")
            return None

        try:
            from gtts import gTTS

            self.output_dir.mkdir(parents=True, exist_ok=True)
            filename = f"tts_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.mp3"
            file_path = self.output_dir / filename

            tts = gTTS(text=text, lang=language or self.language, slow=False)
            tts.save(str(file_path))
            logger.info(f"TTS 合成成功: {file_path} ({file_path.stat().st_size} bytes)")
            return str(file_path)
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
