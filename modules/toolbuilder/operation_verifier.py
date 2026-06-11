"""
操作验证器

在学习模式中验证UI操作是否真正成功。
使用主动感知能力（截图、OCR、UI元素检测）来确认操作效果。
"""
import asyncio
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
from utils.logger import setup_logger

logger = setup_logger("operation_verifier")


@dataclass
class VerificationResult:
    """验证结果"""
    success: bool
    screenshot_captured: bool = False
    ocr_text: str = ""
    ui_elements: List[Dict[str, Any]] = None
    understanding: str = ""
    confidence: float = 0.0
    error: Optional[str] = None
    
    def __post_init__(self):
        if self.ui_elements is None:
            self.ui_elements = []


class OperationVerifier:
    """操作验证器"""
    
    def __init__(self):
        self.logger = setup_logger("operation_verifier")
    
    async def verify_operation(
        self,
        operation_description: str,
        expected_outcome: Optional[str] = None,
        focus: str = ""
    ) -> VerificationResult:
        """验证操作是否成功
        
        Args:
            operation_description: 操作描述
            expected_outcome: 预期结果（可选）
            focus: 关注重点
            
        Returns:
            VerificationResult
        """
        try:
            # 1. 截图并获取OCR文本
            from infra.tool_manager.tools.perception_tools import understand_screen
            screen_result = await understand_screen(focus=focus or operation_description)
            
            if screen_result.get("error"):
                return VerificationResult(
                    success=False,
                    error=f"截图失败: {screen_result['error']}"
                )
            
            # 2. 检测UI元素
            ui_elements = []
            try:
                from infra.tool_manager.tools.perception_tools import detect_ui_elements
                ui_result = await detect_ui_elements(focus=focus or operation_description)
                if ui_result.get("success"):
                    ui_elements = ui_result.get("elements", [])
            except Exception as e:
                self.logger.warning(f"UI元素检测失败（非致命）: {e}")
            
            # 3. 分析验证结果
            ocr_text = screen_result.get("ocr_text", "")
            understanding = screen_result.get("understanding", "")
            
            # 4. 评估成功度
            confidence = self._evaluate_confidence(
                operation_description=operation_description,
                expected_outcome=expected_outcome,
                ocr_text=ocr_text,
                understanding=understanding,
                ui_elements=ui_elements
            )
            
            return VerificationResult(
                success=confidence >= 0.5,
                screenshot_captured=True,
                ocr_text=ocr_text,
                ui_elements=ui_elements,
                understanding=understanding,
                confidence=confidence,
            )
            
        except Exception as e:
            self.logger.error(f"操作验证失败: {e}")
            return VerificationResult(
                success=False,
                error=str(e)
            )
    
    def _evaluate_confidence(
        self,
        operation_description: str,
        expected_outcome: Optional[str],
        ocr_text: str,
        understanding: str,
        ui_elements: List[Dict[str, Any]]
    ) -> float:
        """评估验证置信度"""
        confidence = 0.5  # 基础置信度
        
        # 如果有预期结果，检查是否匹配
        if expected_outcome:
            if expected_outcome.lower() in understanding.lower():
                confidence += 0.3
            elif expected_outcome.lower() in ocr_text.lower():
                confidence += 0.2
        
        # 如果有UI元素，检查是否合理
        if ui_elements:
            confidence += 0.1
        
        # 如果有理解结果，稍微增加置信度
        if understanding and len(understanding) > 10:
            confidence += 0.1
        
        return min(1.0, confidence)
    
    async def verify_batch(
        self,
        operations: List[Dict[str, Any]]
    ) -> List[VerificationResult]:
        """批量验证操作"""
        results = []
        for op in operations:
            result = await self.verify_operation(
                operation_description=op.get("description", ""),
                expected_outcome=op.get("expected_outcome"),
                focus=op.get("focus", "")
            )
            results.append(result)
        return results


# 全局实例
_verifier: Optional[OperationVerifier] = None


def get_operation_verifier() -> OperationVerifier:
    """获取操作验证器实例"""
    global _verifier
    if _verifier is None:
        _verifier = OperationVerifier()
    return _verifier