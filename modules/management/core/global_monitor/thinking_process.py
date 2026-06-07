"""
3. 思考全过程监控

监控主管模型的任务拆解、决策、调度
多轮思考次数、思考深度、思考分支
专家模型的任务执行、推理过程
"""
from typing import Dict, Any, List, Optional
from .base_recorder import BaseRecorder


class ThinkingProcessRecorder(BaseRecorder):
    """思考全过程记录器"""

    def __init__(self):
        super().__init__("thinking_process", max_records=500)
        self.max_rounds = 20  # 最大思考轮次

    def record_phase(
        self,
        thinking_id: str,
        phase: str,
        details: Dict[str, Any] = None
    ) -> None:
        """
        记录思考阶段
        
        Args:
            thinking_id: 思考 ID
            phase: 阶段 (start, manager_decide, expert_execute, end)
            details: 详细信息
        """
        super().add_record({
            "thinking_id": thinking_id,
            "phase": phase,
            "details": details or {}
        })

    def get_history(
        self,
        thinking_id: str = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        获取思考历史
        
        Args:
            thinking_id: 思考 ID（可选）
            limit: 返回数量限制
            
        Returns:
            思考历史记录
        """
        records = self.get_records(limit=limit * 2)  # 多取一些用于过滤
        
        if thinking_id:
            records = [r for r in records if r.get("thinking_id") == thinking_id]
        
        return records[-limit:]

    def count_rounds(self, thinking_id: str) -> int:
        """
        计算思考轮次
        
        Args:
            thinking_id: 思考 ID
            
        Returns:
            思考轮次数
        """
        return sum(
            1 for r in self.records
            if r.get("thinking_id") == thinking_id and r.get("phase") == "expert_execute"
        )

    def check_rounds_limit(self, thinking_id: str) -> Dict[str, Any]:
        """
        检查思考轮次是否超限
        
        Returns:
            检查结果
        """
        rounds = self.count_rounds(thinking_id)
        
        if rounds > self.max_rounds:
            return {
                "exceeded": True,
                "rounds": rounds,
                "max_rounds": self.max_rounds,
                "message": f"思考轮次过多: {rounds} (限制 {self.max_rounds})"
            }
        
        return {
            "exceeded": False,
            "rounds": rounds,
            "max_rounds": self.max_rounds
        }
