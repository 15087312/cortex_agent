"""
NLP 服务工厂 - 管理服务实例的生命周期

使用单例模式确保全局只有一个服务实例。
"""
from typing import Optional
from infra.nlp.text_analysis_service import TextAnalysisService
from utils.logger import setup_logger


class NLPServiceFactory:
    """
    NLP 服务工厂
    
    单例模式管理 TextAnalysisService 实例，
    确保资源正确分配和释放。
    """
    
    _text_analysis_service: Optional[TextAnalysisService] = None
    _logger = setup_logger("nlp_service_factory")
    
    @classmethod
    async def get_text_analysis_service(cls) -> TextAnalysisService:
        """
        获取文本分析服务实例
        
        如果实例不存在，则创建并初始化。
        返回的是已初始化的服务实例。
        
        Returns:
            TextAnalysisService 实例
        """
        if cls._text_analysis_service is None:
            cls._logger.info("创建新的 TextAnalysisService 实例")
            cls._text_analysis_service = TextAnalysisService()
            await cls._text_analysis_service.initialize()
        return cls._text_analysis_service
    
    @classmethod
    async def close_all(cls) -> None:
        """
        关闭所有服务实例
        
        释放所有资源，重置实例状态。
        """
        cls._logger.info("关闭所有 NLP 服务实例")
        
        if cls._text_analysis_service is not None:
            try:
                await cls._text_analysis_service.close()
            except Exception as e:
                cls._logger.warning("关闭 TextAnalysisService 时发生错误: %s", e)
            finally:
                cls._text_analysis_service = None
        
        cls._logger.info("所有 NLP 服务实例已关闭")
    
    @classmethod
    def reset(cls) -> None:
        """
        重置工厂状态
        
        仅重置引用，不关闭资源。
        用于测试场景。
        """
        cls._text_analysis_service = None
