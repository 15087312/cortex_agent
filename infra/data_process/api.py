"""
信息处理 API

提供语音识别和图像分析的完整流程支持。
"""
from fastapi import APIRouter, UploadFile, File, Form, Depends
from infra.auth import require_api_key
from fastapi.responses import JSONResponse
import base64
import json

from infra.errors import AppError, ErrorCode
from utils.logger import setup_logger
from infra.data_process.core.speech_recognizer import get_default_recognizer
from infra.data_process.core.image_analyzer import get_default_analyzer
from infra.data_process.core.speaker_classifier import get_default_classifier

logger = setup_logger("data_process_api")

# 文件上传限制
MAX_UPLOAD_SIZE_MB = 50          # 最大 50MB
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024

ALLOWED_AUDIO_MIME = {
    "audio/wav", "audio/wave", "audio/x-wav",
    "audio/mpeg", "audio/mp3",
    "audio/flac", "audio/x-flac",
    "audio/ogg", "audio/webm",
    "audio/aac", "audio/m4a", "audio/x-m4a",
}
ALLOWED_IMAGE_MIME = {
    "image/png", "image/jpeg", "image/jpg",
    "image/gif", "image/webp", "image/bmp",
}


# @link:block Validate_Guard = Guard
def _validate_upload(file: UploadFile, allowed_mime: set, max_size: int = MAX_UPLOAD_SIZE_BYTES) -> None:
    """验证上传文件的 MIME 类型与大小，不合法时抛出 AppError"""
    ct = file.content_type
    if ct and ct != "application/octet-stream" and ct not in allowed_mime:
        raise AppError(
            ErrorCode.UNSUPPORTED_MEDIA_TYPE,
            f"不支持的文件类型: {file.content_type}，允许: {', '.join(sorted(allowed_mime))}"
        )
    # 部分客户端不传 content_type，此时仅做大小限制
    if file.size is not None and file.size > max_size:
        raise AppError(
            ErrorCode.PAYLOAD_TOO_LARGE,
            f"文件过大 ({file.size} bytes)，最大允许 {max_size} bytes"
        )


router = APIRouter(prefix="/data-process", tags=["信息处理"],
    dependencies=[Depends(require_api_key)],
)


# @link:block Speech_Recognize = Emit
@router.post("/speech/recognize")
async def recognize_speech(
    audio_file: UploadFile = File(...),
    language: str = Form("auto"),
    task: str = Form("transcribe")
):
    """
    语音识别接口

    参数:
        audio_file: 音频文件
        language: 语言代码（auto/zh/en/ja/ko等）
        task: transcribe(转写) / translate(翻译)
    
    返回:
        {
            "success": true,
            "text": "识别文本",
            "language": "zh",
            "confidence": 0.95,
            "segments": [...]
        }
    """
    try:
        _validate_upload(audio_file, ALLOWED_AUDIO_MIME)
        audio_data = await audio_file.read()

        recognizer = await get_default_recognizer()
        result = await recognizer.recognize(
            audio_data,
            language=language if language != "auto" else None,
            task=task
        )
        
        return JSONResponse(content={
            "success": True,
            "data": result
        })
    except Exception as e:
        logger.error(f"[data_process] 语音识别失败: {type(e).__name__}: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")


# @link:block Speech_Recognize_Base64 = Emit
@router.post("/speech/recognize-base64")
async def recognize_speech_base64(
    audio: str = Form(...),
    language: str = Form("auto")
):
    """Base64音频识别"""
    try:
        audio_data = base64.b64decode(audio)
        recognizer = await get_default_recognizer()
        result = await recognizer.recognize(audio_data, language)
        
        return {"success": True, "data": result}
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")


# ── 声纹识别 / 说话人分类 ──────────────────────────────────

@router.post("/speaker/identify")
async def identify_speaker(
    audio_file: UploadFile = File(...),
    threshold: float = Form(0.75),
    register_unknown: str = Form(""),
):
    """
    声纹识别：从音频识别说话人身份

    参数:
        audio_file: 音频文件
        threshold: 相似度阈值 (0-1)，默认 0.75
        register_unknown: 若非空且未识别到说话人，以此名称自动注册

    返回:
        {
            "identified": true/false,
            "speaker": "说话人名称",
            "similarity": 0.85,
            "embedding": [...]
        }
    """
    try:
        _validate_upload(audio_file, ALLOWED_AUDIO_MIME)
        audio_data = await audio_file.read()

        classifier = await get_default_classifier()
        result = await classifier.identify_speaker(audio_data, threshold=threshold)

        if not result["identified"] and register_unknown:
            emb = result.get("embedding")
            if emb:
                await classifier.register_speaker_from_embedding(register_unknown, emb)
                result["identified"] = True
                result["speaker"] = register_unknown
                result["auto_registered"] = True

        return JSONResponse(content={"success": True, "data": result})
    except Exception as e:
        logger.error(f"[data_process] 声纹识别失败: {type(e).__name__}: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "声纹识别失败，请稍后重试")


@router.post("/speaker/register")
async def register_speaker(
    audio_file: UploadFile = File(...),
    name: str = Form(...),
):
    """
    注册说话人：从音频提取声纹并存储

    参数:
        audio_file: 音频文件（建议 ≥3 秒清晰语音）
        name: 说话人名称
    """
    try:
        _validate_upload(audio_file, ALLOWED_AUDIO_MIME)
        audio_data = await audio_file.read()

        classifier = await get_default_classifier()
        result = await classifier.register_speaker(name, audio_data)

        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"[data_process] 说话人注册失败: {type(e).__name__}: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "说话人注册失败")


@router.get("/speaker/list")
async def list_speakers():
    """列出所有已注册的说话人"""
    classifier = await get_default_classifier()
    speakers = await classifier.list_speakers()
    return {"success": True, "data": {"speakers": speakers, "count": len(speakers)}}


@router.delete("/speaker/{name}")
async def delete_speaker(name: str):
    """删除指定说话人"""
    classifier = await get_default_classifier()
    result = await classifier.delete_speaker(name)
    return JSONResponse(content=result)


@router.post("/speech/recognize-with-speaker")
async def recognize_speech_with_speaker(
    audio_file: UploadFile = File(...),
    language: str = Form("auto"),
    task: str = Form("transcribe"),
    speaker_threshold: float = Form(0.75),
    auto_register: str = Form(""),
):
    """
    语音识别 + 声纹识别一体化接口

    同时返回转写文本和说话人身份。

    参数:
        audio_file: 音频文件
        language: 语言代码
        task: transcribe / translate
        speaker_threshold: 声纹匹配阈值
        auto_register: 若非空且未识别，以此名称自动注册新说话人
    """
    try:
        _validate_upload(audio_file, ALLOWED_AUDIO_MIME)
        audio_data = await audio_file.read()

        recognizer = await get_default_recognizer()
        classifier = await get_default_classifier()

        stt_result = await recognizer.recognize(
            audio_data,
            language=language if language != "auto" else None,
            task=task,
        )
        speaker_result = await classifier.identify_speaker(
            audio_data, threshold=speaker_threshold
        )

        if not speaker_result["identified"] and auto_register:
            emb = speaker_result.get("embedding")
            if emb:
                await classifier.register_speaker_from_embedding(auto_register, emb)
                speaker_result["identified"] = True
                speaker_result["speaker"] = auto_register
                speaker_result["auto_registered"] = True

        return JSONResponse(content={
            "success": True,
            "data": {
                "text": stt_result.get("text", ""),
                "language": stt_result.get("language", ""),
                "confidence": stt_result.get("confidence", 0),
                "segments": stt_result.get("segments", []),
                "speaker": speaker_result,
            }
        })
    except Exception as e:
        logger.error(f"[data_process] 语音+声纹识别失败: {type(e).__name__}: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")


# @link:block Image_Analyze = Emit
@router.post("/image/analyze")
async def analyze_image(
    image_file: UploadFile = File(...),
    prompt: str = Form("详细描述这张图片，包含所有可见的物体、场景和细节")
):
    """
    图像分析接口

    参数:
        image_file: 图像文件
        prompt: 分析提示词
    
    返回:
        {
            "success": true,
            "description": "图像描述",
            "objects": [...],
            "scene": "场景类型",
            "colors": [...]
        }
    """
    try:
        _validate_upload(image_file, ALLOWED_IMAGE_MIME)
        image_data = await image_file.read()

        analyzer = await get_default_analyzer()
        result = await analyzer.analyze(image_data, prompt)
        
        return JSONResponse(content={
            "success": True,
            "data": result
        })
    except Exception as e:
        logger.debug(f"[data_process] 语音识别失败: {type(e).__name__}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": "服务暂时不可用，请稍后重试"}
        )


# @link:block Image_Analyze_Base64 = Emit
@router.post("/image/analyze-base64")
async def analyze_image_base64(
    image: str = Form(...),
    prompt: str = Form("详细描述这张图片")
):
    """分析Base64编码的图片"""
    try:
        analyzer = await get_default_analyzer()
        result = await analyzer.analyze_base64(image, prompt)
        return {"success": True, "data": result}
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")


@router.get("/status")
async def get_status():
    """获取信息处理模块状态"""
    return {
        "success": True,
        "data": {
            "module": "info_process",
            "status": "healthy",
            "capabilities": {
                "speech_recognition": {
                    "local": True,
                    "models": ["whisper-tiny", "whisper-base", "whisper-small", "whisper-medium", "whisper-large"],
                    "languages": ["auto", "zh", "en", "ja", "ko", "fr", "de", "es", "ru"]
                },
                "image_analysis": {
                    "local": True,
                    "models": ["qwen_vl", "llava", "openai_gpt4v"],
                    "features": ["description", "object_detection", "scene_classification", "color_extraction"]
                },
                "speaker_identification": {
                    "enabled": True,
                    "model": "resemblyzer",
                    "embedding_dim": 256,
                    "features": ["identify", "register", "list", "delete"]
                }
            }
        }
    }


@router.post("/image/detect-ui")
async def detect_ui_elements(
    image_file: UploadFile = File(...),
    element_types: str = Form(None)
):
    """
    UI元素检测接口

    检测图像中的UI元素（按钮、输入框、图标等）

    参数:
        image_file: 图像文件
        element_types: 要检测的元素类型（逗号分隔，如: button,input,icon）
    
    返回:
        {
            "elements": [
                {
                    "type": "button",
                    "text": "提交",
                    "bounds": {"x": 100, "y": 200, "width": 80, "height": 30},
                    "center": {"x": 140, "y": 215},
                    "colors": {"bg": "#3498db", "text": "#ffffff"},
                    "confidence": 0.95
                }
            ],
            "layout": {"width": 1920, "height": 1080, "grid": "3x4"}
        }
    """
    try:
        _validate_upload(image_file, ALLOWED_IMAGE_MIME)
        image_data = await image_file.read()

        types = element_types.split(",") if element_types else None
        
        analyzer = await get_default_analyzer()
        result = await analyzer.detect_ui_elements(image_data, types)
        
        return JSONResponse(content={
            "success": True,
            "data": result
        })
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")


@router.post("/image/analyze-query")
async def analyze_with_query(
    image_file: UploadFile = File(...),
    query: str = Form("详细描述这张图片，包含所有UI元素的位置和颜色")
):
    """
    带自然语言查询的图像分析

    可以问"按钮在哪个位置"、"某个元素是什么颜色"等问题

    参数:
        image_file: 图像文件
        query: 分析查询
    
    返回:
        {
            "answer": "在坐标(100,200)处有一个蓝色按钮'提交'",
            "elements": [...],
            "coordinates": {"x": 100, "y": 200}
        }
    """
    try:
        _validate_upload(image_file, ALLOWED_IMAGE_MIME)
        image_data = await image_file.read()

        analyzer = await get_default_analyzer()
        result = await analyzer.analyze_with_coordinates(image_data, query)  # type: ignore[attr-defined]
        
        return JSONResponse(content={
            "success": True,
            "data": result
        })
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")


# @link:block Draw_Elements_Emit = Emit
@router.post("/image/draw-elements")
async def draw_ui_elements(
    image_file: UploadFile = File(...),
    element_data: str = Form(...)
):
    """
    在图像上绘制UI元素标注

    参数:
        image_file: 原始图像
        element_data: JSON格式的UI元素列表
    
    返回:
        标注后的图像文件
    """
    try:
        from fastapi.responses import Response

        _validate_upload(image_file, ALLOWED_IMAGE_MIME)
        image_data = await image_file.read()
        elements = json.loads(element_data)
        
        analyzer = await get_default_analyzer()
        result_bytes = analyzer.draw_elements(image_data, elements)
        
        return Response(content=result_bytes, media_type="image/png")
    except Exception:
        raise AppError(ErrorCode.INTERNAL_ERROR, "服务暂时不可用，请稍后重试")
