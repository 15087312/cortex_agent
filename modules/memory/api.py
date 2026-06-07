"""
记忆 API - 统一对外接口

提供标准方法供网关调用：
- get_short_term()
- get_context()
- get_long_term(query)
- save_dialog(role, text)
- save_thought(thought_chain)
- save_personality()
- get_blackbox()
- clear_short_term()
"""
from fastapi import APIRouter, Body, Query
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from api.errors import AppError, ErrorCode
from modules.memory.core.memory_manager import MemoryManager
from utils.logger import setup_logger

router = APIRouter(prefix="/memory", tags=["记忆"])
logger = setup_logger("memory_api")

# 记忆管理器实例
_memory_manager = MemoryManager()


# ========== 请求模型 ==========

class DialogRequest(BaseModel):
    role: str = Field(..., description="角色 (user, assistant, system)")
    text: str = Field(..., description="对话内容")
    metadata: Dict[str, Any] = Field(default={}, description="元数据")


class WorkingMemoryRequest(BaseModel):
    key: str = Field(..., description="键")
    value: Any = Field(..., description="值")
    ttl: Optional[int] = Field(default=None, description="过期时间(秒)")


class EmotionRequest(BaseModel):
    emotion: str = Field(..., description="情绪类型")
    intensity: float = Field(default=0.5, ge=0, le=1, description="情绪强度")
    trigger: Optional[str] = Field(default=None, description="触发因素")


class SaveLongTermRequest(BaseModel):
    memory_type: str = Field(..., description="记忆类型 (dialog, thought, preference, summary, evolution, event)")
    content: Dict[str, Any] = Field(..., description="记忆内容")


class PersonalityTraitRequest(BaseModel):
    value: Any = Field(..., description="特征值")


# ========== 短期记忆 API ==========

@router.post("/short-term/dialog")
async def add_dialog(request: DialogRequest):
    """
    添加对话到短期记忆
    """
    try:
        dialog = _memory_manager.add_dialog(request.role, request.text, request.metadata)
        return {"success": True, "data": dialog}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/short-term/context")
async def get_context(limit: int = Query(default=20)):
    """
    获取对话上下文
    
    参数:
        limit: 返回轮次限制
    
    返回:
        对话上下文列表
    """
    try:
        context = _memory_manager.get_context(limit)
        return {"success": True, "data": context}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.post("/short-term/working")
async def set_working_memory(request: WorkingMemoryRequest):
    try:
        _memory_manager.set_working_memory(request.key, request.value, request.ttl)
        return {"success": True, "data": {"message": f"工作记忆已设置: {request.key}"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/short-term/working/{key}")
async def get_working_memory(key: str):
    """
    获取工作记忆
    
    参数:
        key: 键
    
    返回:
        工作记忆值
    """
    try:
        value = _memory_manager.get_working_memory(key)
        return {"success": True, "data": value}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.post("/short-term/emotion")
async def set_current_emotion(emotion: EmotionRequest):
    """
    设置当前情绪
    """
    try:
        _memory_manager.set_current_emotion(emotion.model_dump())
        return {"success": True, "data": {"message": "当前情绪已设置"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/short-term/emotion")
async def get_current_emotion():
    """获取当前情绪"""
    try:
        emotion = _memory_manager.get_current_emotion()
        return {"success": True, "data": emotion}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.delete("/short-term/clear")
async def clear_short_term():
    """清空短期记忆"""
    try:
        _memory_manager.clear_short_term()
        return {"success": True, "data": {"message": "短期记忆已清空"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


# ========== 长期记忆 API ==========

@router.post("/long-term")
async def save_long_term(request: SaveLongTermRequest):
    """
    保存长期记忆
    """
    try:
        memory = _memory_manager.save_long_term(request.memory_type, request.content)
        return {"success": True, "data": memory}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/long-term/{memory_type}")
async def load_long_term(memory_type: str, limit: int = Query(default=50)):
    """
    加载长期记忆
    
    参数:
        memory_type: 记忆类型
        limit: 返回数量限制
    
    返回:
        记忆列表
    """
    try:
        memories = _memory_manager.load_long_term(memory_type, limit)
        return {"success": True, "data": memories}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/long-term/{memory_type}/search")
async def search_long_term(
    memory_type: str,
    keywords: str = Query(...),
    limit: int = Query(default=20)
):
    """
    搜索长期记忆
    
    参数:
        memory_type: 记忆类型
        keywords: 关键词（逗号分隔）
        limit: 返回数量限制
    
    返回:
        匹配的记忆列表
    """
    try:
        keyword_list = [kw.strip() for kw in keywords.split(",")]
        memories = _memory_manager.search_long_term(memory_type, keyword_list, limit)
        return {"success": True, "data": memories}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.delete("/long-term/{memory_id}")
async def delete_long_term(memory_id: str, memory_type: str = Query(default=None)):
    """
    删除长期记忆
    
    参数:
        memory_id: 记忆 ID
        memory_type: 记忆类型（可选）
    
    返回:
        删除的记录数
    """
    try:
        count = _memory_manager.delete_long_term(memory_id, memory_type)
        return {"success": True, "data": {"deleted_count": count}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


# ========== 人格记忆 API ==========

@router.get("/personality")
async def get_personality():
    """获取完整人格配置"""
    try:
        personality = _memory_manager.get_personality()
        return {"success": True, "data": personality}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/personality/trait/{key}")
async def get_personality_trait(key: str, default: str = Query(default=None)):
    """
    获取人格特征
    
    参数:
        key: 特征键名
        default: 默认值
    
    返回:
        特征值
    """
    try:
        value = _memory_manager.get_personality_trait(key, default)
        return {"success": True, "data": value}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.put("/personality/trait/{key}")
async def update_personality_trait(key: str, request: PersonalityTraitRequest):
    try:
        _memory_manager.update_personality_trait(key, request.value)
        return {"success": True, "data": {"message": f"人格特征已更新: {key}"}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/personality/values")
async def get_values():
    """获取价值观倾向"""
    try:
        values = _memory_manager.get_values()
        return {"success": True, "data": values}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


# ========== 黑匣子 API ==========

@router.get("/blackbox/{log_type}")
async def get_blackbox_logs(log_type: str, limit: int = Query(default=50)):
    """
    获取黑匣子日志
    
    参数:
        log_type: 日志类型 (thinking, module_call, emotion, evolution, error)
        limit: 返回数量限制
    
    返回:
        日志列表
    """
    try:
        logs = _memory_manager.get_blackbox_logs(log_type, limit)
        return {"success": True, "data": logs}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/blackbox/timeline")
async def get_timeline(limit: int = Query(default=100)):
    """
    获取时间线
    
    参数:
        limit: 返回数量限制
    
    返回:
        时间线日志列表
    """
    try:
        timeline = _memory_manager.get_timeline(limit)
        return {"success": True, "data": timeline}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


# ========== 综合 API ==========

@router.get("/search")
async def search_memories(
    keywords: str = Query(...),
    limit: int = Query(default=20)
):
    """
    搜索所有类型的记忆
    
    参数:
        keywords: 关键词（逗号分隔）
        limit: 返回数量限制
    
    返回:
        匹配的记忆列表
    """
    try:
        keyword_list = [kw.strip() for kw in keywords.split(",")]
        memories = _memory_manager.search_memories(keyword_list, limit=limit)
        return {"success": True, "data": memories}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/status")
async def get_status():
    """获取记忆模块综合状态"""
    try:
        status = _memory_manager.get_status()
        return {"success": True, "data": status}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.get("/summary")
async def get_summary():
    """获取记忆状态摘要"""
    try:
        summary = _memory_manager.get_summary()
        return {"success": True, "data": summary}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


@router.post("/snapshot")
async def save_snapshot(snapshot_name: str = Body(..., embed=True)):
    """
    保存记忆快照
    
    参数:
        snapshot_name: 快照名称
    
    返回:
        快照文件路径
    """
    try:
        path = _memory_manager.save_snapshot(snapshot_name)
        return {"success": True, "data": {"path": path}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, "记忆操作失败")


# ... existing code ...

# ========== 分类记忆 API ==========

@router.post("/classified-memory/save")
async def save_classified_memory(entry: dict):
    """保存分类记忆"""
    try:
        category = entry.get("category", "general")
        content = entry.get("content", "")
        metadata = entry.get("metadata", {})

        result = _memory_manager.save_classified_memory(category, content, metadata)
        if result.get("error"):
            raise AppError(ErrorCode.INTERNAL_ERROR, f"保存分类记忆失败: {result['error']}")

        return {"success": True, "data": {"memory_id": result.get("memory_id")}}
    except AppError:
        raise
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"保存分类记忆失败: {str(e)}")


@router.post("/classified-memory/search")
async def search_classified_memory(request: dict):
    """搜索分类记忆"""
    try:
        query = request.get("query", "")
        category = request.get("category")
        memory_age = request.get("memory_age", "all")
        limit = request.get("limit", 10)

        results = _memory_manager.search_classified_memory(query, category, memory_age, limit)
        return {"success": True, "data": {"memories": results}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"搜索分类记忆失败: {str(e)}")


@router.get("/classified-memory/categories")
async def get_memory_categories():
    """获取所有记忆类别"""
    try:
        categories = _memory_manager.get_memory_categories()
        return {"success": True, "data": {"categories": categories}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"获取记忆类别失败: {str(e)}")


@router.get("/classified-memory/stats")
async def get_memory_stats():
    """获取记忆统计"""
    try:
        stats = _memory_manager.get_classified_memory_stats()
        return {"success": True, "data": {"stats": stats}}
    except Exception as e:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"获取记忆统计失败: {str(e)}")
