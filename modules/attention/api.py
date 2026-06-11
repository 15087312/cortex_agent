"""
注意力 API - 权重计算、任务调度、候选上下文排序

提供注意力模块的核心功能接口：
1. /attention/weight/calculate - 计算注意力权重
2. /attention/task/schedule - 任务调度
3. /attention/status - 获取注意力状态

注意：记忆检索由 modules.memory.retriever 负责，完整上下文构建由 modules.context.manager 负责。
"""
from fastapi import Depends,  APIRouter, Form, Body, Query
from api.auth import require_api_key
from typing import Dict, Any, List, Optional
from datetime import datetime

from api.errors import AppError, ErrorCode
from modules.attention.core import (
    WeightCalculator,
    MemoryAttentionScorer,
    AttentionCore
)
from modules.thinking.context import ContextManager
from modules.attention.utils.task_sorter import TaskSorter
from utils.logger import setup_logger

router = APIRouter(prefix="/attention", tags=["注意力"],
    dependencies=[Depends(require_api_key)],
)
logger = setup_logger("attention_api")

# 全局组件实例（延迟初始化）
_weight_calculator: Optional[WeightCalculator] = None
_attention_core: Optional[AttentionCore] = None
_task_sorter: Optional[TaskSorter] = None
_context_manager: Optional[ContextManager] = None
_memory_attention_scorer: Optional[MemoryAttentionScorer] = None


def _get_weight_calculator() -> WeightCalculator:
    """获取权重计算器实例（延迟初始化）"""
    global _weight_calculator
    if _weight_calculator is None:
        _weight_calculator = WeightCalculator()
    return _weight_calculator


def _get_attention_core() -> AttentionCore:
    """获取注意力核心实例（延迟初始化）"""
    global _attention_core
    if _attention_core is None:
        _attention_core = AttentionCore()
    return _attention_core


def _get_task_sorter() -> TaskSorter:
    """获取任务排序器实例（延迟初始化）"""
    global _task_sorter
    if _task_sorter is None:
        _task_sorter = TaskSorter()
    return _task_sorter


def _get_memory_attention_scorer() -> MemoryAttentionScorer:
    """获取记忆注意力打分器实例（延迟初始化）"""
    global _memory_attention_scorer
    if _memory_attention_scorer is None:
        _memory_attention_scorer = MemoryAttentionScorer()
    return _memory_attention_scorer


def _get_context_manager() -> Optional[ContextManager]:
    """获取上下文管理器实例（延迟初始化，可能失败）"""
    global _context_manager
    if _context_manager is None:
        try:
            from modules.memory.core.memory_manager import MemoryManager
            memory_manager = MemoryManager()
            _context_manager = ContextManager(
                memory_manager=memory_manager,
                scorer=_get_memory_attention_scorer(),
            )
        except Exception as e:
            logger.warning(f"ContextManager 初始化失败: {e}")
            return None
    return _context_manager


# ==============================================================================
# 1. 权重计算
# ==============================================================================

@router.post("/weight/calculate")
async def calculate_weight(
    base_weight: float = Form(default=0.5),
    sound_level: float = Form(default=0.0),
    visual_level: float = Form(default=0.0),
    task_priority: float = Form(default=0.5),
    emotion_intensity: float = Form(default=0.0)
):
    """
    计算注意力权重
    
    结合多维度因子计算综合注意力权重：
    - base_weight: 基础权重 (0-1)
    - sound_level: 声音紧急度 (0-1)
    - visual_level: 视觉紧急度 (0-1)
    - task_priority: 任务优先级 (0-1)
    - emotion_intensity: 情绪强度 (0-1)
    
    返回归一化后的权重值 (0-1)
    """
    try:
        calculator = _get_weight_calculator()
        weight = calculator.calculate({
            "base_weight": base_weight,
            "sound_level": sound_level,
            "visual_level": visual_level,
            "task_priority": task_priority,
            "emotion_intensity": emotion_intensity
        })
        return {
            "success": True,
            "data": {
                "weight": weight,
                "input_factors": {
                    "base_weight": base_weight,
                    "sound_level": sound_level,
                    "visual_level": visual_level,
                    "task_priority": task_priority,
                    "emotion_intensity": emotion_intensity
                }
            }
        }
    except Exception as e:
        logger.error(f"权重计算失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "权重计算失败")


# ==============================================================================
# 2. 工作上下文构建
# ==============================================================================

@router.post("/context/build")
async def build_working_context(
    query: str = Form(...),
    attention_level: float = Form(default=0.6)
):
    """构建工作上下文。

    注意力系统不再直接负责记忆检索；检索由 Retrieval Layer 完成，
    ContextManager 负责调用检索层并让 Attention System 对候选上下文排序。
    """
    try:
        context_manager = _get_context_manager()
        if context_manager is None:
            return {
                "success": True,
                "data": {
                    "selected_memories": [],
                    "selected_events": [],
                    "priority_score": 0,
                    "warning": "ContextManager 未初始化，无法构建上下文"
                }
            }

        working_context = await context_manager.build_working_context(
            current_goal=query,
            current_state={},
            attention_level=attention_level,
        )
        policy = _get_memory_attention_scorer().get_last_effective_policy()
        metadata = dict(working_context.metadata or {})
        metadata.update({
            "effective_threshold": policy.get("threshold"),
            "effective_max_recall": policy.get("max_recall"),
            "effective_attention_level": policy.get("attention_level"),
        })
        return {
            "success": True,
            "data": {
                "selected_memories": working_context.selected_memories,
                "selected_events": working_context.selected_events,
                "selected_goals": working_context.selected_goals,
                "priority_score": working_context.priority_score,
                "metadata": metadata,
            }
        }
    except Exception as e:
        logger.error(f"工作上下文构建失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "工作上下文构建失败")


# ==============================================================================
# 3. 任务调度
# ==============================================================================

@router.post("/task/schedule")
async def schedule_tasks(tasks: List[Dict] = Body(...)):
    """
    调度任务优先级
    
    根据任务优先级对任务列表进行排序
    
    参数:
        tasks: 任务列表，每个任务应包含 priority 字段
    
    返回:
        排序后的任务列表及调度信息
    """
    try:
        task_sorter = _get_task_sorter()
        sorted_tasks = task_sorter.sort_by_priority(tasks)
        
        # 生成调度顺序索引
        order = [tasks.index(task) for task in sorted_tasks if task in tasks]
        
        return {
            "success": True,
            "data": {
                "scheduled_tasks": sorted_tasks,
                "order": order,
                "total_tasks": len(tasks)
            }
        }
    except Exception as e:
        logger.error(f"任务调度失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "任务调度失败")


# ==============================================================================
# 4. 注意力状态
# ==============================================================================

@router.get("/status")
async def get_status():
    """
    获取注意力系统状态
    
    返回各组件初始化状态及当前注意力水平
    """
    try:
        # 检查各组件状态
        components = {
            "weight_calculator": _weight_calculator is not None,
            "attention_core": _attention_core is not None,
            "task_sorter": _task_sorter is not None,
            "memory_attention_scorer": _memory_attention_scorer is not None,
            "context_manager": _context_manager is not None
        }
        
        # 尝试初始化各组件以获取真实状态
        try:
            calculator = _get_weight_calculator()
            components["weight_calculator"] = True
        except Exception as e:
            logger.warning(f"WeightCalculator 初始化失败: {e}")
            components["weight_calculator"] = False
        
        try:
            core = _get_attention_core()
            components["attention_core"] = True
        except Exception as e:
            logger.warning(f"AttentionCore 初始化失败: {e}")
            components["attention_core"] = False
        
        try:
            sorter = _get_task_sorter()
            components["task_sorter"] = True
        except Exception as e:
            logger.warning(f"TaskSorter 初始化失败: {e}")
            components["task_sorter"] = False
        
        try:
            scorer = _get_memory_attention_scorer()
            components["memory_attention_scorer"] = True
        except Exception as e:
            logger.warning(f"MemoryAttentionScorer 初始化失败: {e}")
            components["memory_attention_scorer"] = False
        
        # ContextManager 依赖 MemoryManager，单独检查
        try:
            cm = _get_context_manager()
            components["context_manager"] = cm is not None
            components["memory_manager"] = cm is not None
        except Exception as e:
            logger.warning(f"ContextManager 不可用: {e}")
            components["context_manager"] = False
            components["memory_manager"] = False
        
        # 计算整体状态
        core_components = [
            components["weight_calculator"],
            components["attention_core"],
            components["task_sorter"]
        ]
        
        if all(core_components):
            status = "healthy"
        elif any(core_components):
            status = "degraded"
        else:
            status = "unavailable"
        
        return {
            "success": True,
            "data": {
                "module": "attention",
                "status": status,
                "timestamp": datetime.now().isoformat(),
                "components": components,
                "capabilities": [
                    "weight_calculation",
                    "task_scheduling",
                    "working_context_build"
                ]
            }
        }
    except Exception as e:
        logger.error(f"获取注意力状态失败: {e}")
        return {
            "success": True,
            "data": {
                "module": "attention",
                "status": "error",
                "error": "Internal server error",
                "timestamp": datetime.now().isoformat()
            }
        }


# ==============================================================================
# 5. 注意力分析（额外功能）
# ==============================================================================

@router.post("/analyze")
async def analyze_attention(
    user_input: str = Body(...),
    context: Optional[List[Dict]] = Body(default=None),
    short_term_memory: Optional[List[str]] = Body(default=None)
):
    """
    分析用户输入的注意力决策
    
    根据用户输入和上下文，决定激活哪些模块、休眠哪些模块
    
    参数:
        user_input: 用户输入文本
        context: 对话上下文（可选）
        short_term_memory: 短期记忆列表（可选）
    
    返回:
        注意力决策结果
    """
    try:
        core = _get_attention_core()
        decision = core.analyze(user_input, context or [], short_term_memory or [])
        
        return {
            "success": True,
            "data": {
                "focus": decision.focus,
                "active_modules": decision.active_modules,
                "sleep_modules": decision.sleep_modules,
                "priority_weights": decision.priority_weights,
                "related_memory_count": len(decision.related_memory),
                "context_related_count": len(decision.context_related),
                "importance_score": getattr(decision, "importance_score", 0.5),
                "importance_reasons": getattr(decision, "importance_reasons", []),
                "attention_level": getattr(decision, "attention_level", 0.6),
            }
        }
    except Exception as e:
        logger.error(f"注意力分析失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "注意力分析失败")


# ==============================================================================
# 6. 记忆打分（额外功能）
# ==============================================================================

@router.post("/memory/score")
async def score_memories(
    query: str = Body(...),
    memories: List[Dict[str, Any]] = Body(...),
    attention_level: float = Body(default=0.6)
):
    """
    对记忆列表进行注意力打分
    
    根据查询文本和注意力水平，对记忆列表进行评分和筛选
    
    参数:
        query: 查询文本
        memories: 记忆列表
        attention_level: 注意力水平 (0-1)，默认 0.6
    
    返回:
        打分后的记忆列表
    """
    try:
        scorer = _get_memory_attention_scorer()
        scored_results = await scorer.score_memories(query, memories, attention_level)
        
        policy = scorer.get_last_effective_policy()
        return {
            "success": True,
            "data": {
                "scored_memories": scored_results,
                "total_input": len(memories),
                "total_output": len(scored_results),
                "attention_level": attention_level,
                "effective_threshold": policy.get("threshold"),
                "effective_max_recall": policy.get("max_recall"),
            }
        }
    except Exception as e:
        logger.error(f"记忆打分失败: {e}")
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆打分失败")
