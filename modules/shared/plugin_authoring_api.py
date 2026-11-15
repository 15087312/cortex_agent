"""自制插件的 HTTP 入口 — 前端与 curl 用的那三个动作。

和模型面工具（``plugin_authoring_tools``）是同一层机件的两个门面：语义必须一字
不差，所以这里只翻译形状（JSON ↔ 关键字参数、异常 ↔ 状态码），不加策略、不缓存、
不第二条提交路径。

失败带 traceback：``error_response`` 的信封只有一个 message 字段，而迭代插件的人
要看的正是那一段 traceback（``File "<plugin:name>", line 7``），所以它进 message
而不是被丢掉——宁长不可丢。
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from infra.errors import ErrorCode, error_response
from modules.shared import plugin_authoring as pa
from utils.logger import get_logger

logger = get_logger("plugin_authoring_api")

router = APIRouter(prefix="/plugins/authored", tags=["plugins"])

__all__ = ["router"]


class AuthoredPluginRequest(BaseModel):
    """提交：源码 + 可选试运行。"""

    name: str
    source: str
    trial: Optional[Dict[str, Any]] = None
    replace: bool = False


def _failure(code: ErrorCode, exc: BaseException) -> JSONResponse:
    payload = pa.failure(exc)
    return JSONResponse(
        status_code=_STATUS[code],
        content=error_response(code, f"{payload['error']}\n{payload['traceback']}").model_dump(),
    )


_STATUS: Dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.INTERNAL_ERROR: 500,
}


@router.post("")
async def submit_authored_plugin(req: AuthoredPluginRequest):
    """挂载提交进来的源码，试运行通过才写盘。

    语义与 ``mount_plugin`` 工具一致：失败当场撤销，不留半挂载。
    """
    try:
        mount = await pa.author(
            name=req.name,
            source=req.source,
            trial=req.trial,
            replace=req.replace,
            store=pa.get_default_store(),
        )
    except (pa.PluginSourceError, pa.PluginConflictError) as exc:
        # 提交人的错，不是服务的错：说清楚是哪一行以及谁在应答这个名字。
        code = ErrorCode.BAD_REQUEST if isinstance(exc, pa.PluginConflictError) else ErrorCode.VALIDATION_ERROR
        return _failure(code, exc)
    except Exception as exc:  # noqa: BLE001 — 插件代码炸出的任何异常都是响应，不是 500
        # 源码在自己的 apply / 试运行里抛的东西在这一层不可归类（没有沙箱，什么
        # 都能抛）。归到 4xx 而不是 500：500 会让提交人以为同一份源码重试可能成
        # 功，而这里唯一会变的是他自己的代码。traceback 全带回去。
        logger.warning(f"[plugin] 自制插件 {req.name!r} 提交失败: {type(exc).__name__}: {exc}")
        return _failure(ErrorCode.BAD_REQUEST, exc)
    return {"success": True, "data": {
        "name": mount.name,
        "status": "mounted",
        "tools": sorted(mount.tools),
        "capabilities": sorted(mount.capabilities),
        "trial_ran": req.trial is not None,
    }}


@router.get("")
async def list_authored_plugins():
    """盘上的每一条 + 它现在是否在跑、正应答哪些名字（模型可见集同源）。"""
    return {"success": True, "data": {"plugins": pa.status()}}


@router.get("/{name}")
async def get_authored_plugin(name: str):
    """单个插件的行与实时归属。"""
    row = pa.get_default_store().get(name)
    if row is None and name not in pa.MOUNTED:
        return JSONResponse(
            status_code=404,
            content=error_response(ErrorCode.NOT_FOUND, f"没有名为 {name!r} 的自制插件").model_dump(),
        )
    mount = pa.MOUNTED.get(name)
    return {"success": True, "data": {
        "row": row,
        "mounted": mount is not None,
        "tools": sorted(mount.tools) if mount else [],
        "capabilities": sorted(mount.capabilities) if mount else [],
    }}


@router.delete("/{name}")
async def delete_authored_plugin(name: str, forget: bool = True):
    """卸掉正在跑的作用域。``?forget=false`` 只卸不删，重启后仍会回来。"""
    try:
        was_mounted = await pa.drop(name)
        removed = pa.get_default_store().remove(name) if forget else False
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"[plugin] 自制插件 {name!r} 删除失败")
        return _failure(ErrorCode.INTERNAL_ERROR, exc)
    if not was_mounted and not removed:
        return JSONResponse(
            status_code=404,
            content=error_response(
                ErrorCode.NOT_FOUND,
                f"没有名为 {name!r} 的自制插件（既没在跑，盘上也没这一行）",
            ).model_dump(),
        )
    return {"success": True, "data": {
        "name": name,
        "unmounted": was_mounted,
        "removed_from_store": removed,
    }}
