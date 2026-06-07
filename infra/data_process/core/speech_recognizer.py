"""
语音识别核心 - 基于 Whisper 本地模型
支持：Whisper / 云ASR / 模拟模式
"""
import asyncio
import tempfile
import os
from typing import Dict, Any, Optional
from pathlib import Path
from utils.logger import setup_logger

logger = setup_logger("speech_recognizer")


class SpeechRecognizer:
    """语音识别器"""

    def __init__(
        self,
        model_name: str = "base",
        language: str = "auto",
        use_local: bool = True
    ):
        """
        初始化语音识别器
        
        Args:
            model_name: Whisper模型大小 (tiny/base/small/medium/large)
            language: 语言代码，auto为自动检测
            use_local: 是否使用本地Whisper
        """
        self.model_name = model_name
        self.language = language
        self.use_local = use_local
        self.model = None
        self._initialized = False

    async def initialize(self):
        """初始化模型"""
        if self._initialized:
            return
        
        if self.use_local:
            await self._load_whisper()
        else:
            await self._init_cloud_asr()
        
        self._initialized = True
        logger.info(f"语音识别器初始化完成 (模型: {self.model_name}, 本地: {self.use_local})")

    async def _load_whisper(self):
        """加载本地Whisper模型"""
        try:
            import whisper
            self.model = whisper.load_model(self.model_name)
            logger.info(f"Whisper模型加载成功: {self.model_name}")
        except ImportError:
            logger.warning("Whisper未安装，将使用模拟模式")
            self.model = None

    async def _init_cloud_asr(self):
        """初始化云端ASR"""
        pass

    async def recognize(
        self,
        audio_data: bytes,
        language: Optional[str] = None,
        task: str = "transcribe"
    ) -> Dict[str, Any]:
        """
        识别语音
        
        Args:
            audio_data: 音频字节数据
            language: 语言（可选）
            task: transcribe 或 translate
        
        Returns:
            识别结果
        """
        if not self._initialized:
            await self.initialize()
        
        lang = language or self.language
        
        if self.model is not None:
            return await self._recognize_with_whisper(audio_data, lang, task)
        else:
            return await self._recognize_mock(audio_data)

    async def _recognize_with_whisper(
        self,
        audio_data: bytes,
        language: str,
        task: str
    ) -> Dict[str, Any]:
        """使用Whisper识别"""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as f:
            f.write(audio_data)
            temp_path = f.name
        
        try:
            import whisper
            result = self.model.transcribe(
                temp_path,
                language=None if language == "auto" else language,
                task=task,
                fp16=False
            )
            
            return {
                "text": result["text"].strip(),
                "language": result.get("language", language),
                "confidence": self._calculate_confidence(result),
                "segments": [
                    {
                        "start": seg["start"],
                        "end": seg["end"],
                        "text": seg["text"].strip()
                    }
                    for seg in result.get("segments", [])
                ],
                "duration": result.get("segments", [{}])[-1].get("end", 0) if result.get("segments") else 0
            }
        finally:
            os.unlink(temp_path)

    async def _recognize_mock(self, audio_data: bytes) -> Dict[str, Any]:
        """模拟识别（用于测试）"""
        await asyncio.sleep(0.1)
        return {
            "text": "[模拟识别结果] 这是一段测试语音",
            "language": "zh",
            "confidence": 0.95,
            "duration": 2.5
        }

    def _calculate_confidence(self, result: Dict) -> float:
        """计算置信度"""
        segments = result.get("segments", [])
        if not segments:
            return 0.0
        
        avg_prob = sum(s.get("avg_logprob", -1) for s in segments) / len(segments)
        confidence = min(1.0, max(0.0, (avg_prob + 1) / 2 + 0.5))
        return round(confidence, 3)

    async def recognize_file(
        self,
        file_path: str,
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """识别音频文件"""
        with open(file_path, "rb") as f:
            audio_data = f.read()
        return await self.recognize(audio_data, language)

    async def recognize_base64(
        self,
        audio_b64: str,
        language: Optional[str] = None
    ) -> Dict[str, Any]:
        """识别Base64编码的音频"""
        import base64
        audio_data = base64.b64decode(audio_b64)
        return await self.recognize(audio_data, language)

    async def recognize_stream(self, audio_stream):
        """流式语音识别"""
        buffer = b""
        async for chunk in audio_stream:
            buffer += chunk
            if len(buffer) > 0:
                yield await self.recognize(buffer)
                buffer = b""

    async def close(self):
        """关闭模型"""
        if self.model is not None:
            del self.model
            self.model = None
        self._initialized = False


_default_recognizer: Optional[SpeechRecognizer] = None


async def get_default_recognizer() -> SpeechRecognizer:
    """获取默认识别器单例"""
    global _default_recognizer
    if _default_recognizer is None:
        _default_recognizer = SpeechRecognizer()
        await _default_recognizer.initialize()
    return _default_recognizer
