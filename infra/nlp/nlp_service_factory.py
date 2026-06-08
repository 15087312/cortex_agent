"""
NLP 服务工厂 - 管理服务实例的生命周期

使用模块级单例模式确保全局只有一个服务实例。
"""
from typing import Optional
from infra.nlp.text_analysis_service import TextAnalysisService
from utils.logger import setup_logger
import threading

logger = setup_logger("nlp_service_factory")

_text_analysis_service: Optional[TextAnalysisService] = None
_init_lock = threading.Lock()


async def get_text_analysis_service() -> TextAnalysisService:
    """获取文本分析服务单例"""
    global _text_analysis_service
    if _text_analysis_service is None:
        with _init_lock:
            if _text_analysis_service is None:
                logger.info("创建新的 TextAnalysisService 实例")
                _text_analysis_service = TextAnalysisService()
                await _text_analysis_service.initialize()
    return _text_analysis_service


async def close_all() -> None:
    """关闭所有服务实例"""
    global _text_analysis_service
    logger.info("关闭所有 NLP 服务实例")
    if _text_analysis_service is not None:
        try:
            await _text_analysis_service.close()
        except Exception as e:
            logger.warning("关闭 TextAnalysisService 时发生错误: %s", e)
        finally:
            _text_analysis_service = None
    logger.info("所有 NLP 服务实例已关闭")


def reset() -> None:
    """重置工厂状态（仅用于测试）"""
    global _text_analysis_service
    _text_analysis_service = None


# 向后兼容
class NLPServiceFactory:
    """向后兼容包装"""
    get_text_analysis_service = staticmethod(get_text_analysis_service)
    close_all = staticmethod(close_all)
    reset = staticmethod(reset)
