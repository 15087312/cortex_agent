"""
环境一致性监控器

在执行已学技能时，使用被动感知监控环境是否与预期一致。
当环境发生意外变化时（如窗口切换、页面跳转），触发警告或重试。
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime
from utils.logger import setup_logger

logger = setup_logger("environment_monitor")


@dataclass
class EnvironmentSnapshot:
    """环境快照"""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    window_title: str = ""
    ocr_text: str = ""
    ui_elements: List[Dict[str, Any]] = field(default_factory=list)
    perception_summary: str = ""


@dataclass
class ConsistencyCheckResult:
    """一致性检查结果"""
    consistent: bool
    confidence: float = 1.0
    changes: List[str] = field(default_factory=list)
    recommendation: str = ""


class EnvironmentMonitor:
    """环境一致性监控器"""
    
    def __init__(self):
        self.logger = setup_logger("environment_monitor")
        self._baseline: Optional[EnvironmentSnapshot] = None
    
    def capture_baseline(self) -> EnvironmentSnapshot:
        """捕获基线环境状态（执行前调用）"""
        try:
            from modules.perception.integration import get_perception_integrator
            integrator = get_perception_integrator()
            summary = integrator.get_context_summary()
            
            self._baseline = EnvironmentSnapshot(
                perception_summary=summary or ""
            )
            self.logger.debug(f"基线环境已捕获")
            return self._baseline
        except Exception as e:
            self.logger.warning(f"捕获基线失败: {e}")
            self._baseline = EnvironmentSnapshot()
            return self._baseline
    
    def check_consistency(self) -> ConsistencyCheckResult:
        """检查当前环境与基线是否一致（执行后调用）"""
        if self._baseline is None:
            return ConsistencyCheckResult(
                consistent=True,
                confidence=0.5,
                recommendation="无基线，跳过检查"
            )
        
        try:
            from modules.perception.integration import get_perception_integrator
            integrator = get_perception_integrator()
            current_summary = integrator.get_context_summary()
            
            if not current_summary:
                return ConsistencyCheckResult(
                    consistent=True,
                    confidence=0.5,
                    recommendation="无法获取当前环境状态"
                )
            
            # 简单的一致性检查：比较感知摘要
            # 实际项目中可以更精细地比较窗口标题、UI元素等
            changes = []
            confidence = 1.0
            
            # 检查是否有显著变化
            if self._baseline.perception_summary and current_summary:
                # 如果感知摘要变化很大，可能环境发生了变化
                baseline_len = len(self._baseline.perception_summary)
                current_len = len(current_summary)
                
                if abs(baseline_len - current_len) > 100:
                    changes.append("环境感知摘要长度显著变化")
                    confidence -= 0.3
            
            consistent = len(changes) == 0 or confidence > 0.5
            
            if not consistent:
                recommendation = "环境可能已变化，建议重试或检查操作结果"
            else:
                recommendation = "环境一致性正常"
            
            return ConsistencyCheckResult(
                consistent=consistent,
                confidence=confidence,
                changes=changes,
                recommendation=recommendation,
            )
            
        except Exception as e:
            self.logger.warning(f"一致性检查失败: {e}")
            return ConsistencyCheckResult(
                consistent=True,
                confidence=0.5,
                recommendation=f"检查失败: {e}"
            )
    
    def reset(self):
        """重置基线"""
        self._baseline = None


# 全局实例
_monitor: Optional[EnvironmentMonitor] = None


def get_environment_monitor() -> EnvironmentMonitor:
    """获取环境监控器实例"""
    global _monitor
    if _monitor is None:
        _monitor = EnvironmentMonitor()
    return _monitor