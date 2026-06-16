"""差异检测 REST API — 通过 FastAPI 路由暴露差异查询/启停控制"""
from fastapi import Depends, APIRouter, Query, Path
from api.auth import require_api_key

from api.errors import AppError, ErrorCode
from modules.perception.difference import get_detector, get_heartbeat

router = APIRouter(prefix="/differences", tags=["差异检测"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/status")
async def get_detector_status():
    """获取检测器 + 心跳的状态信息"""
    detector = get_detector()
    heartbeat = get_heartbeat()
    return {
        "success": True,
        "data": {
            "detector": detector.get_status(),
            "heartbeat": heartbeat.get_status(),
        },
    }


@router.get("/active")
async def get_active_differences(
    # 获取活跃差异列表（支持来源类型/强度过滤）
    source_type: str = Query(default=None, description="源类型过滤"),
    min_intensity: float = Query(default=0.0, ge=0, le=100, description="最低强度"),
    limit: int = Query(default=50, ge=1, le=200, description="返回数量限制"),
):
    detector = get_detector()
    differences = detector.get_active(
        source_type=source_type,
        min_intensity=min_intensity,
        limit=limit,
    )
    return {
        "success": True,
        "data": {
            "differences": differences,
            "count": len(differences),
        },
    }


@router.get("/history")
async def get_difference_history(
    # 获取历史差异记录
    limit: int = Query(default=100, ge=1, le=500, description="返回数量限制"),
):
    detector = get_detector()
    history = detector.get_history(limit=limit)
    return {
        "success": True,
        "data": {
            "history": history,
            "count": len(history),
        },
    }


@router.get("/{diff_id}")
async def get_difference(diff_id: str = Path(..., description="差异 ID")):
    """根据 ID 获取单个差异详情"""
    detector = get_detector()
    diff = detector.repository.get_by_id(diff_id)
    if not diff:
        raise AppError(ErrorCode.NOT_FOUND, f"差异 {diff_id} 不存在")
    return {"success": True, "data": diff}


@router.get("/sources/list")
async def list_sources():
    """列出所有已注册的差异源"""
    detector = get_detector()
    return {
        "success": True,
        "data": {
            "sources": detector.registry.list_sources(),
        },
    }


@router.post("/sources/{source_type}/enable")
async def enable_source(source_type: str = Path(..., description="源类型")):
    """启用指定差异源"""
    detector = get_detector()
    ok = detector.registry.enable(source_type)
    if not ok:
        raise AppError(ErrorCode.NOT_FOUND, f"差异源 {source_type} 不存在")
    return {"success": True, "data": {"message": f"差异源 {source_type} 已启用"}}


@router.post("/sources/{source_type}/disable")
async def disable_source(source_type: str = Path(..., description="源类型")):
    """禁用指定差异源"""
    detector = get_detector()
    ok = detector.registry.disable(source_type)
    if not ok:
        raise AppError(ErrorCode.NOT_FOUND, f"差异源 {source_type} 不存在")
    return {"success": True, "data": {"message": f"差异源 {source_type} 已禁用"}}


@router.post("/scan")
async def trigger_scan():
    """手动触发一次差异扫描（用于调试）"""
    detector = get_detector()
    differences = detector.scan()
    return {
        "success": True,
        "data": {
            "differences_found": len(differences),
            "differences": [d.to_dict() for d in differences[:20]],
        },
    }


@router.get("/heartbeat/status")
async def get_heartbeat_status():
    """获取存在心跳的状态"""
    heartbeat = get_heartbeat()
    return {"success": True, "data": heartbeat.get_status()}
