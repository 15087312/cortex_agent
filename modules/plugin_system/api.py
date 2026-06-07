from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query

from api.errors import AppError, ErrorCode
from config.plugin_config import PluginConfig
from config.settings import settings as global_settings

from .engine import PluginEngine
from .models import InstalledPlugin, PluginMetadata, PluginStatus
from .provider_tools import ModelToolBridge, ProviderToolExportOptions, export_provider_tools
from .tool_adapter import plugin_model_tool_whitelist, plugin_tool_registry_entries
from .tool_governance import FileToolCallSessionStore, ToolCallSessionStore, ToolExecutionMode

logger = __import__("utils.logger", fromlist=["setup_logger"]).setup_logger("plugin_api")

_PLUGIN_API_TOKEN = os.environ.get("PLUGIN_API_TOKEN", "")


def require_plugin_auth(authorization: str = Header(None)):
    if not _PLUGIN_API_TOKEN:
        return
    if not authorization:
        raise HTTPException(status_code=403, detail="Missing authorization header")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="Invalid authorization format")
    token = authorization.split(" ", 1)[1] if len(authorization.split(" ", 1)) > 1 else ""
    if not token or token != _PLUGIN_API_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid token")


router = APIRouter(
    prefix="/plugins",
    tags=["插件"],
    dependencies=[Depends(require_plugin_auth)],
)

_engine: PluginEngine | None = None
_model_tool_bridge: ModelToolBridge | None = None
_governance_store: ToolCallSessionStore | None = None


def get_engine() -> PluginEngine:
    global _engine
    if _engine is None:
        raise AppError(ErrorCode.SERVICE_UNAVAILABLE, "plugin engine is not initialized")
    return _engine


def init_engine(cfg: PluginConfig | None = None) -> PluginEngine | None:
    global _engine, _model_tool_bridge, _governance_store
    if cfg is None:
        cfg = PluginConfig(
            plugins_dir=global_settings.PLUGINS_DIR,
            engine_enabled=global_settings.PLUGIN_ENGINE_ENABLED,
            require_signatures=global_settings.PLUGIN_REQUIRE_SIGNATURES,
            require_enforced_sandbox=global_settings.PLUGIN_REQUIRE_ENFORCED_SANDBOX,
            production_mode=global_settings.APP_ENV == "production",
            sandbox_backend=global_settings.PLUGIN_SANDBOX_BACKEND,
        )
    if not cfg.engine_enabled:
        logger.info("plugin engine is disabled by configuration")
        return None
    try:
        _engine = PluginEngine(
            plugins_dir=cfg.plugins_dir,
            require_signatures=cfg.require_signatures,
            require_enforced_sandbox=cfg.require_enforced_sandbox,
            production_mode=cfg.production_mode,
            sandbox_backend=cfg.sandbox_backend,
        )
        _governance_store = _build_governance_store(_engine)
        _model_tool_bridge = ModelToolBridge(
            _engine,
            governance_store=_governance_store,
            audit_logger=_engine.audit_logger,
        )
        logger.info(f"plugin engine initialized (plugins dir: {cfg.plugins_dir})")
        return _engine
    except Exception as exc:
        logger.error(f"failed to initialize plugin engine: {exc}")
        raise


def close_engine() -> None:
    global _engine, _model_tool_bridge, _governance_store
    if _engine is not None:
        try:
            _engine.stop_all()
            logger.info("plugin engine shut down")
        except Exception as exc:
            logger.error(f"error shutting down plugin engine: {exc}")
        finally:
            _engine = None
            _model_tool_bridge = None
            _governance_store = None


def get_model_tool_bridge() -> ModelToolBridge:
    global _model_tool_bridge, _governance_store
    engine = get_engine()
    if _model_tool_bridge is None:
        _governance_store = _governance_store or _build_governance_store(engine)
        _model_tool_bridge = ModelToolBridge(
            engine,
            governance_store=_governance_store,
            audit_logger=engine.audit_logger,
        )
    return _model_tool_bridge


def _build_governance_store(engine: PluginEngine) -> ToolCallSessionStore:
    store_path = os.environ.get("PLUGIN_TOOL_GOVERNANCE_STORE", "").strip()
    if not store_path:
        return ToolCallSessionStore()
    path = Path(store_path)
    if not path.is_absolute():
        path = Path(engine.plugins_dir) / path
    return FileToolCallSessionStore(path)


def _installed_to_dict(installed: InstalledPlugin) -> dict[str, Any]:
    return {
        "name": installed.metadata.name,
        "version": installed.metadata.version,
        "description": installed.metadata.description,
        "author": installed.metadata.author,
        "license": installed.metadata.license,
        "status": installed.status.value,
        "path": installed.path,
        "installed_at": installed.installed_at,
        "package_hash": installed.package_hash,
        "granted_permissions": [
            {k: v for item in installed.granted_permissions for k, v in item.items()}
        ],
        "permission_review": installed.permission_review,
        "tools": list(installed.metadata.tool_entries().keys()),
        "tool_registry": plugin_tool_registry_entries(installed.metadata),
        "middlewares": list(installed.metadata.middleware_entries().keys()),
        "event_listeners": list(installed.metadata.event_listener_entries().keys()),
        "memory_providers": list(installed.metadata.memory_provider_entries().keys()),
    }


def _metadata_to_dict(metadata: PluginMetadata) -> dict[str, Any]:
    return {
        "name": metadata.name,
        "version": metadata.version,
        "description": metadata.description,
        "author": metadata.author,
        "license": metadata.license,
        "tools": list(metadata.tool_entries().keys()),
        "tool_registry": plugin_tool_registry_entries(metadata),
        "middlewares": list(metadata.middleware_entries().keys()),
        "event_listeners": list(metadata.event_listener_entries().keys()),
        "memory_providers": list(metadata.memory_provider_entries().keys()),
    }


@router.get("/")
def list_plugins(source: str = Query(None, description="filter: all/enabled/disabled")):
    engine = get_engine()
    discovered = engine.discover()
    installed_map = {name: engine.loader.get_installed(name) for name in discovered}

    result = []
    for name, meta in discovered.items():
        installed = installed_map.get(name)
        entry = _metadata_to_dict(meta)
        if installed:
            entry["status"] = installed.status.value
        result.append(entry)

    if source == "enabled":
        result = [p for p in result if p.get("status") == PluginStatus.ENABLED.value]
    elif source == "disabled":
        result = [p for p in result if p.get("status") != PluginStatus.ENABLED.value]

    return {"success": True, "data": {"plugins": result, "count": len(result)}}


@router.get("/tools/model-whitelist")
def model_tool_whitelist(
    caller_role: str = Query("expert", description="model caller role"),
    allow_high_risk_tools: bool = Query(False),
    allow_third_party_tools: bool = Query(True),
):
    engine = get_engine()
    engine.discover()
    installed = list(engine.loader.installed_plugins.values())
    whitelist = plugin_model_tool_whitelist(
        installed,
        caller_role=caller_role,
        allow_high_risk_tools=allow_high_risk_tools,
        allow_third_party_tools=allow_third_party_tools,
    )
    return {
        "success": True,
        "data": {
            "tools": whitelist,
            "count": len(whitelist),
            "caller_role": caller_role,
        },
    }


@router.get("/tools/provider-export")
def provider_tool_export(
    provider: str = Query("openai", description="generic/openai/anthropic"),
    actor_role: str = Query("model", description="model/expert/admin"),
    production_mode: bool | None = Query(None),
    include_hidden: bool = Query(False),
    include_returns_summary: bool = Query(False),
    governance_preview: bool = Query(True),
):
    engine = get_engine()
    request_id = None
    production = engine.production_mode if production_mode is None else production_mode
    from .llm_tools import LLMToolCatalog
    from .tool_governance import tool_risk_decision

    catalog = LLMToolCatalog.from_engine(
        engine,
        actor_role=actor_role,
        production_mode=production,
        approved_only=True,
        include_hidden=include_hidden,
        request_id=request_id,
        audit_logger=engine.audit_logger,
    )
    payload = export_provider_tools(
        catalog,
        options=ProviderToolExportOptions(
            provider=provider,
            actor_role=actor_role,
            production_mode=production,
            include_hidden=include_hidden,
            include_returns_summary=include_returns_summary,
        ),
        audit_logger=engine.audit_logger,
    )
    if governance_preview:
        specs_by_name = {spec.name: spec for spec in catalog.specs}
        payload["governance_preview"] = {}
        for provider_name, mapping in payload.get("name_mapping", {}).items():
            if not isinstance(mapping, dict):
                continue
            spec = specs_by_name.get(str(mapping.get("model_tool_name") or ""))
            if spec is None:
                continue
            risk = tool_risk_decision(spec)
            payload["governance_preview"][str(provider_name)] = {
                "model_tool_name": spec.name,
                "risk_level": risk.risk_level,
                "required_permissions": risk.required_permissions,
                "side_effecting": risk.side_effecting,
                "requires_confirmation": risk.requires_confirmation,
                "expected_side_effects": risk.expected_side_effects,
            }
    return {"success": True, "data": payload}


@router.post("/tools/provider-preview")
def provider_tool_preview(
    provider: str = Body("openai"),
    payload: dict[str, Any] = Body(...),
    actor_role: str = Body("model"),
    conversation_id: str | None = Body(None),
    production_mode: bool | None = Body(None),
):
    return _provider_tool_governed_call(
        provider=provider,
        payload=payload,
        actor_role=actor_role,
        conversation_id=conversation_id,
        production_mode=production_mode,
        execution_mode=ToolExecutionMode.PREVIEW_ONLY,
    )


@router.post("/tools/provider-confirm")
def provider_tool_confirm(
    provider: str = Body("openai"),
    payload: dict[str, Any] = Body(...),
    actor_role: str = Body("model"),
    conversation_id: str | None = Body(None),
    production_mode: bool | None = Body(None),
    idempotency_key: str | None = Body(None),
):
    return _provider_tool_governed_call(
        provider=provider,
        payload=payload,
        actor_role=actor_role,
        conversation_id=conversation_id,
        production_mode=production_mode,
        execution_mode=ToolExecutionMode.CONFIRMATION_ONLY,
        idempotency_key=idempotency_key,
    )


@router.post("/tools/provider-call")
def provider_tool_call(
    provider: str = Body("openai"),
    payload: dict[str, Any] = Body(...),
    actor_role: str = Body("model"),
    conversation_id: str | None = Body(None),
    production_mode: bool | None = Body(None),
    execution_mode: str = Body(ToolExecutionMode.EXECUTE),
    confirmation_token: str | None = Body(None),
    idempotency_key: str | None = Body(None),
):
    return _provider_tool_governed_call(
        provider=provider,
        payload=payload,
        actor_role=actor_role,
        conversation_id=conversation_id,
        production_mode=production_mode,
        execution_mode=execution_mode,
        confirmation_token=confirmation_token,
        idempotency_key=idempotency_key,
    )


def _provider_tool_governed_call(
    *,
    provider: str,
    payload: dict[str, Any],
    actor_role: str,
    conversation_id: str | None,
    production_mode: bool | None,
    execution_mode: str,
    confirmation_token: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    engine = get_engine()
    production = engine.production_mode if production_mode is None else production_mode
    try:
        response = get_model_tool_bridge().invoke_provider_tool_call(
            provider,
            payload,
            actor_role=actor_role,
            conversation_id=conversation_id,
            production_mode=production,
            execution_mode=execution_mode,
            confirmation_token=confirmation_token,
            idempotency_key=idempotency_key,
        )
        return {"success": bool(response.get("ok")), "data": response}
    except ValueError as exc:
        raise AppError(ErrorCode.BAD_REQUEST, str(exc))


@router.get("/{name}")
def get_plugin(name: str):
    engine = get_engine()
    metadata = engine.loader.get_plugin(name)
    if not metadata:
        raise AppError(ErrorCode.NOT_FOUND, f"plugin not found: {name}")
    installed = engine.loader.get_installed(name)
    if installed:
        return {"success": True, "data": _installed_to_dict(installed)}
    return {"success": True, "data": _metadata_to_dict(metadata)}


@router.post("/install")
def install_plugin(
    package_path: str = Body(..., description="path to plugin package (.zip)"),
    replace: bool = Body(True, description="replace if already installed"),
    install_dependencies: bool = Body(False),
):
    engine = get_engine()
    try:
        metadata = engine.install(
            package_path,
            replace=replace,
            install_dependencies=install_dependencies,
        )
        return {"success": True, "data": _metadata_to_dict(metadata)}
    except Exception as exc:
        raise AppError(ErrorCode.BAD_REQUEST, f"install failed: {exc}")


@router.post("/{name}/uninstall")
def uninstall_plugin(name: str):
    engine = get_engine()
    installed = engine.loader.get_installed(name)
    if not installed:
        raise AppError(ErrorCode.NOT_FOUND, f"plugin not installed: {name}")
    engine.stop_plugin(name)
    engine.tool_registry_bridge.unregister_plugin(name)
    import shutil
    shutil.rmtree(Path(installed.path), ignore_errors=True)
    engine.loader.loaded_plugins.pop(name, None)
    engine.loader.installed_plugins.pop(name, None)
    return {"success": True, "data": {"name": name, "status": "uninstalled"}}


@router.post("/{name}/enable")
def enable_plugin(name: str):
    engine = get_engine()
    try:
        installed = engine.enable_plugin(name)
        return {"success": True, "data": _installed_to_dict(installed)}
    except Exception as exc:
        raise AppError(ErrorCode.BAD_REQUEST, f"enable failed: {exc}")


@router.post("/{name}/disable")
def disable_plugin(name: str):
    engine = get_engine()
    try:
        installed = engine.disable_plugin(name)
        return {"success": True, "data": _installed_to_dict(installed)}
    except Exception as exc:
        raise AppError(ErrorCode.BAD_REQUEST, f"disable failed: {exc}")


@router.post("/{name}/start")
def start_plugin(name: str):
    engine = get_engine()
    try:
        sandbox = engine.start_plugin(name)
        return {
            "success": True,
            "data": {
                "name": name,
                "status": "running",
                "run_mode": sandbox.run_mode.value,
            },
        }
    except Exception as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"start failed: {exc}")


@router.post("/{name}/stop")
def stop_plugin(name: str):
    engine = get_engine()
    engine.stop_plugin(name)
    return {"success": True, "data": {"name": name, "status": "stopped"}}


@router.post("/{name}/grant-permissions")
def grant_permissions(
    name: str,
    permissions: list[dict[str, Any]] | None = Body(None),
    reviewer: str = Body("admin"),
    reason: str = Body("api_grant"),
):
    engine = get_engine()
    try:
        installed = engine.grant_permissions(
            name,
            permissions=permissions,
            reviewer=reviewer,
            review_reason=reason,
        )
        return {"success": True, "data": _installed_to_dict(installed)}
    except Exception as exc:
        raise AppError(ErrorCode.BAD_REQUEST, f"grant permissions failed: {exc}")


@router.post("/{name}/tools/{tool_name}/call")
def call_plugin_tool(
    name: str,
    tool_name: str,
    params: dict[str, Any] = Body(default={}),
):
    engine = get_engine()
    try:
        result = engine.call_tool(name, tool_name, params)
        return {"success": result.get("status") == "success", "data": result}
    except Exception as exc:
        raise AppError(ErrorCode.INTERNAL_ERROR, f"tool call failed: {exc}")


@router.get("/{name}/tools")
def list_plugin_tools(name: str):
    engine = get_engine()
    metadata = engine.loader.get_plugin(name)
    if not metadata:
        raise AppError(ErrorCode.NOT_FOUND, f"plugin not found: {name}")
    tools = metadata.tool_entries()
    return {
        "success": True,
        "data": {
            "tools": tools,
            "tool_registry": plugin_tool_registry_entries(metadata),
            "count": len(tools),
        },
    }


@router.post("/reload")
def reload_plugins():
    engine = get_engine()
    discovered = engine.discover()
    return {
        "success": True,
        "data": {"plugins": list(discovered.keys()), "count": len(discovered)},
    }
