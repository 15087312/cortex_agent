"""
注意力驱动的记忆打分器

结合 MemoryMatchEngine 的多维度评分和当前注意力水平，
对记忆进行智能筛选和排序。
"""
from typing import List, Dict, Any, Optional
from utils.logger import setup_logger
from utils.async_utils import async_wrap
from infra.tool_manager.tools.memory_matcher import MemoryMatchEngine
from config.attention_config import get_attention_config


class MemoryAttentionScorer:
    """注意力驱动的记忆打分器
    
    结合 MemoryMatchEngine 的多维度评分和当前注意力水平，
    对记忆进行智能筛选和排序。
    """
    
    def __init__(self, attention_level: float = 0.6):
        """初始化注意力记忆打分器

        Args:
            attention_level: 初始注意力水平 (0-1)，默认 0.6
        """
        self.attention_level = max(0.0, min(1.0, attention_level))
        self.engine = MemoryMatchEngine()
        self.logger = setup_logger("memory_attention_scorer")
        self._config = get_attention_config()
        self._last_effective_threshold = self._config.threshold_base
        self._last_effective_max_recall = self._config.max_recall_medium
        self.logger.info(f"MemoryAttentionScorer 初始化完成，注意力水平: {self.attention_level}")
    
    def set_attention_level(self, level: float):
        """设置当前注意力水平 (0-1)

        Args:
            level: 注意力水平，范围 0-1
        """
        self.attention_level = max(0.0, min(1.0, level))
        self.logger.debug(f"注意力水平更新为: {self.attention_level}")

    def get_last_effective_policy(self) -> Dict[str, Any]:
        """获取最近一次评分生效的策略参数"""
        return {
            "threshold": self._last_effective_threshold,
            "max_recall": self._last_effective_max_recall,
            "attention_level": self.attention_level,
        }
    
    def _get_threshold(self, level: float = None) -> float:
        """根据注意力水平动态计算阈值"""
        cfg = self._config
        effective_level = level if level is not None else self.attention_level
        threshold = cfg.threshold_base - cfg.threshold_slope * effective_level
        threshold = max(cfg.threshold_min, min(cfg.threshold_max, threshold))
        threshold = round(threshold, 2)
        self._last_effective_threshold = threshold
        return threshold

    def _get_max_recall(self, level: float = None) -> int:
        """根据注意力水平动态计算最大召回数量"""
        cfg = self._config
        effective_level = level if level is not None else self.attention_level
        if effective_level > 0.8:
            max_recall = cfg.max_recall_high
        elif effective_level > 0.4:
            max_recall = cfg.max_recall_medium
        else:
            max_recall = cfg.max_recall_low
        self._last_effective_max_recall = max_recall
        return max_recall
    
    async def score_memories(
        self, 
        query: str, 
        memories: List[Dict[str, Any]],
        attention_level: float = None
    ) -> List[Dict[str, Any]]:
        """对记忆列表进行注意力打分
        
        Args:
            query: 查询文本
            memories: 记忆列表，每条包含 content, timestamp, importance 等字段
            attention_level: 可选，临时覆盖注意力水平
            
        Returns:
            打分后的记忆列表（已排序、已过滤），每条附加:
            - attention_score: 注意力综合分数
            - score_detail: 各维度分数明细
            - passed_threshold: 是否通过阈值
        """
        # CONC-3: Use local attention_level parameter instead of modifying instance state
        # This avoids race conditions in concurrent scenarios
        effective_attention_level = attention_level if attention_level is not None else self.attention_level

        try:
            if not memories:
                self.logger.debug("记忆列表为空，直接返回")
                return []

            self.logger.info(f"开始对 {len(memories)} 条记忆进行注意力打分，当前注意力水平: {effective_attention_level}")

            # 1. 使用 MemoryMatchEngine.score_batch() 获取多维度分数
            try:
                # 使用 async_wrap 包装同步的 engine 调用
                score_batch_async = async_wrap(self.engine.score_batch)
                scored_results = await score_batch_async(query, memories)
            except Exception as e:
                self.logger.error(f"MemoryMatchEngine 评分失败: {e}")
                # 失败时返回空列表，避免整体崩溃
                return []

            # 2. 根据当前注意力水平计算阈值
            # Pass effective_attention_level to threshold calculation
            threshold = self._get_threshold(effective_attention_level)
            max_recall = self._get_max_recall(effective_attention_level)
            
            self.logger.debug(f"动态阈值: {threshold}, 最大召回: {max_recall}")
            
            # 3. 处理评分结果，添加注意力相关字段
            processed_results = []
            for result in scored_results:
                try:
                    total_score = result.get("total_score", 0.0)
                    
                    # 判断是否通过阈值
                    passed_threshold = total_score >= threshold
                    
                    # 构建详细分数信息
                    score_detail = {
                        "semantic": result.get("semantic", 0.0),
                        "keyword": result.get("keyword", 0.0),
                        "time_decay": result.get("time_decay", 0.0),
                        "importance": result.get("importance", 0.0),
                        "total": total_score
                    }
                    
                    # 获取原始记忆数据
                    memory = result.get("memory", {})
                    
                    # 构建输出结果
                    processed_result = {
                        **memory,  # 保留原始记忆的所有字段
                        "attention_score": total_score,
                        "score_detail": score_detail,
                        "passed_threshold": passed_threshold,
                        "attention_level": self.attention_level,
                        "threshold": threshold
                    }
                    
                    processed_results.append(processed_result)
                    
                except Exception as e:
                    self.logger.warning(f"处理单条记忆评分结果失败: {e}")
                    continue
            
            # 4. 过滤低于阈值的记忆
            filtered_results = [r for r in processed_results if r["passed_threshold"]]
            
            # 5. 限制最大召回数量（已在排序后的结果上）
            if len(filtered_results) > max_recall:
                filtered_results = filtered_results[:max_recall]
            
            self.logger.info(f"注意力打分完成，原始 {len(memories)} 条，通过阈值 {len(filtered_results)} 条")

            return filtered_results

        except Exception as e:
            self.logger.error(f"注意力打分过程发生错误: {e}")
            # 异常时返回空列表，避免整体崩溃
            return []
    
    async def score_single(
        self,
        query: str,
        memory: Dict[str, Any],
        attention_level: float = None
    ) -> Optional[Dict[str, Any]]:
        """对单条记忆进行注意力打分

        Args:
            query: 查询文本
            memory: 单条记忆字典，包含 content, timestamp, importance 等字段
            attention_level: 可选，临时覆盖注意力水平

        Returns:
            打分后的记忆字典，包含 attention_score, score_detail 等字段
            如果评分失败则返回 None
        """
        # CONC-3: Use local attention_level parameter instead of modifying instance state
        effective_attention_level = attention_level if attention_level is not None else self.attention_level

        try:
            if not memory:
                self.logger.warning("记忆为空，无法评分")
                return None

            self.logger.debug(f"开始对单条记忆进行注意力打分")

            # 使用 MemoryMatchEngine.score_single() 获取多维度分数
            try:
                score_single_async = async_wrap(self.engine.score_single)
                result = await score_single_async(query, memory)
            except Exception as e:
                self.logger.error(f"MemoryMatchEngine 单条评分失败: {e}")
                return None

            # 计算阈值
            threshold = self._get_threshold(effective_attention_level)
            total_score = result.get("total_score", 0.0)
            passed_threshold = total_score >= threshold

            # 构建详细分数信息
            score_detail = {
                "semantic": result.get("semantic", 0.0),
                "keyword": result.get("keyword", 0.0),
                "time_decay": result.get("time_decay", 0.0),
                "importance": result.get("importance", 0.0),
                "total": total_score
            }

            # 构建输出结果
            processed_result = {
                **memory,  # 保留原始记忆的所有字段
                "attention_score": total_score,
                "score_detail": score_detail,
                "passed_threshold": passed_threshold,
                "attention_level": self.attention_level,
                "threshold": threshold
            }

            self.logger.debug(f"单条记忆打分完成，分数: {total_score}, 通过阈值: {passed_threshold}")

            return processed_result

        except Exception as e:
            self.logger.error(f"单条记忆打分过程发生错误: {e}")
            return None
