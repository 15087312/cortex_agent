"""
注意力 API

提供注意力模块的 HTTP 接口：
1. /analyze - 分析用户输入的重要性
2. /status - 获取模块状态
"""
from fastapi import Depends, APIRouter, Body
from api.auth import require_api_key
from typing import List, Dict, Optional
from datetime import datetime

from utils.logger import setup_logger
from modules.attention import create_attention_analyzer

router = APIRouter(
    prefix="/attention",
    tags=["注意力"],
)
logger = setup_logger("attention_api")

_analyzer = None


def _get_analyzer():
    global _analyzer
    if _analyzer is None:
        _analyzer = create_attention_analyzer()
    return _analyzer


@router.post("/analyze", dependencies=[Depends(require_api_key)])
async def analyze_attention(
    user_input: str = Body(...),
    context: Optional[List[Dict]] = Body(default=None),
    short_term_memory: Optional[List[str]] = Body(default=None),
):
    """
    分析用户输入的注意力决策

    根据用户输入和上下文，返回重要性评分、注意力向量等。
    """
    try:
        analyzer = _get_analyzer()
        result = analyzer.analyze(user_input, context or [], short_term_memory or [])

        return {
            "success": True,
            "data": {
                "importance_score": result.importance_score,
                "attention_level": result.attention_level,
                "importance_reasons": result.importance_reasons,
                "vector": result.vector.to_dict() if result.vector else None,
            },
        }
    except Exception as e:
        logger.error(f"注意力分析失败: {e}")
        return {
            "success": False,
            "error": "注意力分析失败",
        }


@router.get("/status")
async def get_status():
    """获取注意力系统状态"""
    try:
        _get_analyzer()
        return {
            "success": True,
            "data": {
                "module": "attention",
                "status": "healthy",
                "timestamp": datetime.now().isoformat(),
                "components": {
                    "analyzer": True,
                },
            },
        }
    except Exception as e:
        logger.error(f"获取注意力状态失败: {e}")
        return {
            "success": True,
            "data": {
                "module": "attention",
                "status": "unavailable",
                "error": str(e),
                "timestamp": datetime.now().isoformat(),
            },
        }
