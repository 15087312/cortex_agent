"""
插件引擎 — AI 自创插件的生命周期管理

设计目标：
  管理 AI 自创插件的安装、启用、停用、卸载和热加载。
  插件由 learn 模式生成，存放在 data/plugins/ 下。

职责：
  - 安装/卸载插件包
  - 热加载（discover）
  - 启用/停用
  - 运行状态追踪
  - 安全沙箱执行（production_mode 启用时）
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .event_bus import Event, EventBus, EventCallback, global_event_bus
from .gateway import PluginGateway
from .loader import PluginLoader
from .models import (
    InstalledPlugin,
    PluginMetadata,
    PluginStatus,
    PluginToolArgumentError,
    RunMode,
    TrustLevel,
)
from .policy import PolicyEngine
from .sandbox import SandboxManager
from .sandbox_backend import create_sandbox_backend
from .schema_validation import SchemaDefinitionError, SchemaValidationError, validate_json_value
from .tool_adapter import PluginToolRegistryBridge
from .tool_result import PluginToolResultError, sanitize_tool_result_with_report
from .tool_security import RequestPermissionRegistry, ToolPermissionScope


class PluginLifecycleError(RuntimeError):
    pass


class PluginMiddlewareError(RuntimeError):
    pass


class PluginMemoryProviderError(RuntimeError):
    pass


class PluginCircuitBreakerError(RuntimeError):
    pass


BLOCKED_START_STATUSES = {
    PluginStatus.DISABLED,
    PluginStatus.QUARANTINED,
    PluginStatus.REVOKED,
    PluginStatus.SUSPENDED,
    PluginStatus.UNINSTALLED,
}


class PluginEngine:
    """Facade that coordinates loading, sandbox lifecycle, tool calls, and events."""

    def __init__(
        self,
        plugins_dir: str | Path = "data/plugins",
        loader: PluginLoader | None = None,
        gateway: PluginGateway | None = None,
        event_bus: EventBus | None = None,
        sandbox_backend: str = "auto",
        audit_logger: AuditLogger | NullAuditLogger | None = None,
        require_signatures: bool = False,
        require_enforced_sandbox: bool = False,
        production_mode: bool = False,
        policy_engine: PolicyEngine | None = None,
    ):
        self.plugins_dir = Path(plugins_dir).resolve()
        self.production_mode = production_mode
        self.event_bus = event_bus or global_event_bus
        self.audit_logger = audit_logger or AuditLogger(self.plugins_dir / "audit.log")
        self.permission_registry = RequestPermissionRegistry()
        self.gateway = gateway or PluginGateway(
            data_dir=self.plugins_dir,
            event_bus=self.event_bus,
            audit_logger=self.audit_logger,
            permission_registry=self.permission_registry,
        )
        self.gateway.audit_logger = self.audit_logger
        self.gateway.permission_registry = self.permission_registry
        self.policy_engine = policy_engine or (PolicyEngine(audit_logger=self.audit_logger) if production_mode else None)
        effective_require_signatures = require_signatures or production_mode
        self.loader = loader or PluginLoader(
            self.plugins_dir,
            require_signatures=effective_require_signatures,
            production_mode=production_mode,
            policy_engine=self.policy_engine,
        )
        if loader is not None:
            self.loader.production_mode = production_mode or self.loader.production_mode
            self.loader.require_signatures = effective_require_signatures or self.loader.require_signatures
            if self.loader.production_mode and self.loader.policy_engine is None:
                self.loader.policy_engine = self.policy_engine or PolicyEngine(audit_logger=self.audit_logger)
        self.sandboxes: dict[str, SandboxManager] = {}
        self.sandbox_backend = sandbox_backend
        self.require_enforced_sandbox = require_enforced_sandbox or production_mode
        self._event_listener_callbacks: dict[str, list[tuple[str, EventCallback]]] = {}
        self._runtime_states: dict[str, PluginRuntimeState] = {}
        self.tool_registry_bridge = PluginToolRegistryBridge(self)
        self.tool_registry_bridge.sync_enabled_plugins()

    def discover(self) -> dict[str, PluginMetadata]:
        discovered = self.loader.discover_installed()
        self.tool_registry_bridge.sync_enabled_plugins()
        return discovered

    def install(
        self,
        package_path: str | Path,
        replace: bool = True,
        signature: dict[str, Any] | None = None,
        install_dependencies: bool = False,
        scan_report: dict[str, Any] | None = None,
    ) -> PluginMetadata:
        request_id = new_request_id()
        try:
            metadata = self.loader.install(
                package_path,
                replace=replace,
                signature=signature,
                install_dependencies=install_dependencies,
                scan_report=scan_report,
            )
        except Exception as exc:
            self.audit_logger.record(
                "plugin.install_failed",
                "error",
                request_id=request_id,
                plugin=None,
                action="install",
                details={
                    "package": str(package_path),
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "production_mode": self.production_mode,
                },
            )
            raise
        installed = self.loader.get_installed(metadata.name)
        self.audit_logger.record(
            "plugin.installed",
            "success",
            request_id=request_id,
            plugin=metadata.name,
            action="install",
            details={
                "version": metadata.version,
                "replace": replace,
                "install_dependencies": install_dependencies,
                "signed": signature is not None,
                "require_signatures": self.loader.require_signatures,
                "production_mode": self.production_mode,
                "status": installed.status.value if installed else None,
                "granted_permissions": sorted(installed.granted_permission_names) if installed else [],
                "permission_review": installed.permission_review if installed else {},
            },
        )
        self.event_bus.publish("plugin.installed", {"name": metadata.name, "version": metadata.version})
        if installed and installed.status == PluginStatus.ENABLED:
            self.tool_registry_bridge.register_plugin(installed)
        return metadata

    def grant_permissions(
        self,
        name: str,
        permissions: list[dict[str, Any]] | None = None,
        reviewer: str | None = None,
        review_reason: str | None = None,
    ) -> InstalledPlugin:
        request_id = new_request_id()
        installed = self.loader.grant_permissions(
            name,
            permissions,
            reviewer=reviewer,
            review_reason=review_reason,
        )
        self.gateway.register_plugin(installed)
        registered_tools = self.tool_registry_bridge.register_plugin(installed)
        self.audit_logger.record(
            "plugin.permissions_granted",
            "success",
            request_id=request_id,
            plugin=name,
            action="grant_permissions",
            details={
                "permissions": sorted(installed.granted_permission_names),
                "denied_permissions": installed.permission_review.get("denied_permissions", []),
                "reviewer": installed.permission_review.get("reviewer"),
                "review_reason": installed.permission_review.get("review_reason"),
                "registered_tools": registered_tools,
            },
        )
        self.event_bus.publish(
            "plugin.permissions_granted",
            {
                "name": name,
                "permissions": sorted(installed.granted_permission_names),
            },
        )
        return installed

    def enable_plugin(self, name: str, actor: str | None = None, reason: str | None = None) -> InstalledPlugin:
        request_id = new_request_id()
        installed_before = self.loader.get_installed(name)
        if installed_before is not None and self.policy_engine is not None:
            review = installed_before.permission_review or {}
            admin_approved = bool(review.get("reviewer")) and review.get("reviewer") != "system"
            decisions = self.policy_engine.evaluate_enable(
                installed_before.metadata,
                installed_before.granted_permission_names,
                admin_approved=admin_approved,
                production_mode=self.production_mode,
            )
            self.policy_engine.enforce(decisions)
        installed = self.loader.enable_plugin(name)
        self.gateway.register_plugin(installed)
        registered_tools = self.tool_registry_bridge.register_plugin(installed)
        self.audit_logger.record(
            "plugin.enabled",
            "success",
            request_id=request_id,
            plugin=name,
            action="enable",
            details={
                "actor": actor or "admin",
                "reason": reason or "admin_enable",
                "version": installed.metadata.version,
                "registered_tools": registered_tools,
            },
        )
        self.event_bus.publish("plugin.enabled", {"name": name})
        return installed

    def disable_plugin(self, name: str, actor: str | None = None, reason: str | None = None) -> InstalledPlugin:
        request_id = new_request_id()
        self.stop_plugin(name)
        installed = self.loader.disable_plugin(name)
        self.gateway.register_plugin(installed)
        unregistered_tools = self.tool_registry_bridge.unregister_plugin(name)
        self.audit_logger.record(
            "plugin.disabled",
            "success",
            request_id=request_id,
            plugin=name,
            action="disable",
            details={
                "actor": actor or "admin",
                "reason": reason or "admin_disable",
                "version": installed.metadata.version,
                "unregistered_tools": unregistered_tools,
            },
        )
        self.event_bus.publish("plugin.disabled", {"name": name})
        return installed

    def quarantine_plugin(self, name: str, actor: str | None = None, reason: str | None = None) -> InstalledPlugin:
        request_id = new_request_id()
        self.stop_plugin(name)
        installed = self.loader.quarantine_plugin(name)
        self.gateway.register_plugin(installed)
        unregistered_tools = self.tool_registry_bridge.unregister_plugin(name)
        self.audit_logger.record(
            "plugin.quarantined",
            "success",
            request_id=request_id,
            plugin=name,
            action="quarantine",
            details={
                "actor": actor or "admin",
                "reason": reason or "admin_quarantine",
                "version": installed.metadata.version,
                "unregistered_tools": unregistered_tools,
            },
        )
        self.event_bus.publish("plugin.quarantined", {"name": name})
        return installed

    def revoke_plugin(self, name: str, actor: str | None = None, reason: str | None = None) -> InstalledPlugin:
        request_id = new_request_id()
        self.stop_plugin(name)
        installed = self.loader.revoke_plugin(name)
        self.gateway.register_plugin(installed)
        unregistered_tools = self.tool_registry_bridge.unregister_plugin(name)
        self.audit_logger.record(
            "plugin.revoked",
            "success",
            request_id=request_id,
            plugin=name,
            action="revoke",
            details={
                "actor": actor or "admin",
                "reason": reason or "admin_revoke",
                "version": installed.metadata.version,
                "unregistered_tools": unregistered_tools,
            },
        )
        self.event_bus.publish("plugin.revoked", {"name": name})
        return installed

    def revoke_plugin_version(
        self,
        name: str,
        version: str,
        actor: str | None = None,
        reason: str | None = None,
    ) -> None:
        request_id = new_request_id()
        installed = self.loader.get_installed(name)
        revokes_current_version = bool(installed and installed.metadata.version == version)
        if revokes_current_version:
            self.stop_plugin(name)
        self.loader.revoke_plugin_version(
            name,
            version,
            actor=actor or "admin",
            reason=reason or "admin_revoke_version",
        )
        unregistered_tools: list[str] = []
        installed = self.loader.get_installed(name)
        if installed:
            self.gateway.register_plugin(installed)
        if revokes_current_version:
            unregistered_tools = self.tool_registry_bridge.unregister_plugin(name)
        self.audit_logger.record(
            "plugin.version_revoked",
            "success",
            request_id=request_id,
            plugin=name,
            action="revoke",
            details={
                "version": version,
                "actor": actor or "admin",
                "reason": reason or "admin_revoke_version",
                "unregistered_tools": unregistered_tools,
            },
        )
        self.event_bus.publish("plugin.version_revoked", {"name": name, "version": version})

    def start_plugin(self, name: str) -> SandboxManager:
        request_id = new_request_id()
        metadata = self.loader.get_plugin(name)
        if not metadata:
            self.loader.discover_installed()
            metadata = self.loader.get_plugin(name)
        if not metadata:
            raise KeyError(f"plugin not found: {name}")
        installed = self.loader.get_installed(name)
        if not installed:
            raise KeyError(f"plugin install record not found: {name}")
        if installed.status != PluginStatus.ENABLED:
            self.audit_logger.record(
                "plugin.start_denied",
                "error",
                request_id=request_id,
                plugin=name,
                action="start",
                details={
                    "status": installed.status.value,
                    "reason": f"plugin is not enabled: {name} ({installed.status.value})",
                    "version": installed.metadata.version,
                },
            )
            if self.production_mode or installed.status in BLOCKED_START_STATUSES:
                raise PluginLifecycleError(
                    f"plugin is not startable: {name} ({installed.status.value})"
                )
            raise PluginLifecycleError(f"plugin is not enabled: {name} ({installed.status.value})")
        if self.production_mode:
            self._validate_production_start_policy(installed)
            try:
                production_policy_report = self.loader.verify_production_install_policy(name)
            except Exception as exc:
                self.audit_logger.record(
                    "plugin.production_policy",
                    "error",
                    request_id=request_id,
                    plugin=name,
                    action="verify_production_policy",
                    details={"error": str(exc), "error_type": type(exc).__name__},
                )
                raise
            self.audit_logger.record(
                "plugin.production_policy",
                "success" if production_policy_report.get("status") == "success" else "skipped",
                request_id=request_id,
                plugin=name,
                action="verify_production_policy",
                details=production_policy_report,
            )
        if self.policy_engine is not None:
            decisions = self.policy_engine.evaluate_start(
                installed.metadata,
                production_mode=self.production_mode,
                sandbox_enforced=self._sandbox_backend_can_enforce(installed),
                audit_checkpoint_configured=False,
            )
            self.policy_engine.enforce(decisions)
        try:
            integrity_report = self.loader.verify_integrity(name)
        except Exception as exc:
            self.audit_logger.record(
                "plugin.integrity_check",
                "error",
                request_id=request_id,
                plugin=name,
                action="verify_integrity",
                details={"error": str(exc), "error_type": type(exc).__name__},
            )
            raise
        self.audit_logger.record(
            "plugin.integrity_check",
            "success" if integrity_report.get("status") == "success" else "skipped",
            request_id=request_id,
            plugin=name,
            action="verify_integrity",
            details=integrity_report,
        )
        sandbox = SandboxManager(
            installed,
            plugins_dir=self.plugins_dir,
            gateway=self.gateway,
            sandbox_backend=self.sandbox_backend,
            require_enforced_sandbox=self.require_enforced_sandbox,
        )
        try:
            if not sandbox.start():
                raise RuntimeError(f"failed to start plugin: {name}")
        except Exception as exc:
            sandbox.stop()
            self.audit_logger.record(
                "plugin.start_failed",
                "error",
                request_id=request_id,
                plugin=name,
                action="start",
                details={
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "sandbox_backend": self.sandbox_backend,
                    "require_enforced_sandbox": self.require_enforced_sandbox,
                },
            )
            raise
        self.sandboxes[name] = sandbox
        self.gateway.register_sandbox(sandbox)
        self._register_event_listeners(name, sandbox)
        self.audit_logger.record(
            "plugin.started",
            "success",
            request_id=request_id,
            plugin=name,
            action="start",
            details={"run_mode": sandbox.run_mode.value},
        )
        self.event_bus.publish("plugin.started", {"name": name, "run_mode": sandbox.run_mode.value})
        return sandbox

    def _validate_production_start_policy(self, installed: InstalledPlugin) -> None:
        metadata = installed.metadata
        if metadata.runtime.trust != TrustLevel.THIRD_PARTY:
            return
        if metadata.effective_run_mode != RunMode.SUB_PROCESS:
            raise PluginLifecycleError(
                f"production mode requires third-party plugin {metadata.name} to run in sub_process"
            )
        if installed.permission_review.get("required"):
            raise PluginLifecycleError(
                f"production mode requires permission review before starting: {metadata.name}"
            )

    def _sandbox_backend_can_enforce(self, installed: InstalledPlugin) -> bool:
        metadata = installed.metadata
        if not (
            self.production_mode
            and metadata.runtime.trust == TrustLevel.THIRD_PARTY
            and metadata.effective_run_mode == RunMode.SUB_PROCESS
        ):
            return True
        backend = create_sandbox_backend(
            metadata.runtime.memory_mb,
            metadata.runtime.cpu_seconds,
            self.sandbox_backend,
        )
        try:
            return backend.report.enforced and not backend.report.missing_capabilities()
        finally:
            backend.close()

    def stop_plugin(self, name: str) -> None:
        self._unregister_event_listeners(name)
        sandbox = self.sandboxes.pop(name, None)
        if sandbox:
            sandbox.stop()
            self.gateway.unregister_sandbox(name)
            self.audit_logger.record(
                "plugin.stopped",
                "success",
                request_id=new_request_id(),
                plugin=name,
                action="stop",
            )
            self.event_bus.publish("plugin.stopped", {"name": name})

    def stop_all(self) -> None:
        for name in list(self.sandboxes):
            self.stop_plugin(name)

    def call_tool(
        self,
        plugin_name: str,
        tool_name: str,
        args: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        metadata = self.loader.get_plugin(plugin_name)
        if not metadata:
            self.loader.discover_installed()
            self.tool_registry_bridge.sync_enabled_plugins()
            metadata = self.loader.get_plugin(plugin_name)
        if not metadata:
            return {
                "status": "error",
                "error": f"plugin not found: {plugin_name}",
                "request_id": request_id,
            }
        try:
            validated_args = metadata.validate_tool_args(tool_name, args)
        except PluginToolArgumentError as exc:
            self.audit_logger.record(
                "plugin.tool_args_invalid",
                "error",
                request_id=request_id,
                plugin=plugin_name,
                action=tool_name,
                details={"error": str(exc)},
            )
            return {
                "status": "error",
                "error": str(exc),
                "request_id": request_id,
            }
        if plugin_name not in self.sandboxes:
            self.start_plugin(plugin_name)
        sandbox = self.sandboxes[plugin_name]
        installed = self.loader.get_installed(plugin_name)
        if installed is None:
            return {
                "status": "error",
                "error": f"plugin install record not found: {plugin_name}",
                "request_id": request_id,
            }
        scope = self._build_tool_permission_scope(installed, tool_name, request_id)
        self.permission_registry.register(scope)
        try:
            result = self._execute_plugin_action(
                plugin_name,
                request_id,
                "tool",
                tool_name,
                lambda: self.gateway.call_plugin_tool(plugin_name, tool_name, validated_args, request_id=request_id),
                timeout_error="plugin execution timed out",
                failure_error_key="error",
                timeout_seconds=sandbox.meta.runtime.timeout_seconds,
            )
            return self._finalize_tool_result(plugin_name, tool_name, request_id, result)
        finally:
            self.permission_registry.unregister(request_id)

    def _build_tool_permission_scope(
        self,
        installed: InstalledPlugin,
        tool_name: str,
        request_id: str,
    ) -> ToolPermissionScope:
        metadata = installed.metadata
        policy_denied = set()
        if self.policy_engine is not None:
            policy_denied = set(getattr(self.policy_engine.policy, "deny_permissions", set()))
        return ToolPermissionScope.build(
            request_id=request_id,
            plugin_id=metadata.name,
            plugin_version=metadata.version,
            tool_name=tool_name,
            plugin_permissions=metadata.requested_permissions,
            plugin_granted_permissions=installed.granted_permission_names,
            tool_permissions=metadata.tool_requested_permissions(tool_name),
            policy_denied_permissions=policy_denied,
        )

    def _finalize_tool_result(
        self,
        plugin_name: str,
        tool_name: str,
        request_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        if result.get("status") != "success":
            return result
        metadata = self.loader.get_plugin(plugin_name)
        if metadata is None:
            result["status"] = "error"
            result["error"] = f"plugin metadata not found: {plugin_name}"
            return result
        schema = metadata.tool_result_schema(tool_name)
        if schema is not None:
            try:
                validate_json_value(result.get("data"), schema)
            except SchemaValidationError as exc:
                violation = exc.violation
                self.audit_logger.record(
                    "plugin.tool_return_schema_violation",
                    "error",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=tool_name,
                    details={
                        "plugin_id": plugin_name,
                        "plugin_version": metadata.version,
                        "tool_name": tool_name,
                        "request_id": request_id,
                        "instance_path": violation.instance_path,
                        "schema_path": violation.schema_path,
                        "violation": violation.violation,
                        "expected": violation.expected,
                        "actual_type": violation.actual_type,
                        "decision": "deny",
                        "version": metadata.version,
                    },
                    plugin_id=plugin_name,
                    plugin_version=metadata.version,
                    decision="deny",
                    reason=violation.violation,
                )
                return {
                    "status": "error",
                    "error": f"tool return schema violation: {exc}",
                    "request_id": request_id,
                }
            except SchemaDefinitionError as exc:
                self.audit_logger.record(
                    "plugin.tool_return_schema_violation",
                    "error",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=tool_name,
                    details={
                        "plugin_id": plugin_name,
                        "plugin_version": metadata.version,
                        "tool_name": tool_name,
                        "request_id": request_id,
                        "schema_path": "$",
                        "violation": "invalid_return_schema",
                        "expected": "supported JSON Schema subset",
                        "actual_type": "schema",
                        "decision": "deny",
                        "version": metadata.version,
                    },
                    plugin_id=plugin_name,
                    plugin_version=metadata.version,
                    decision="deny",
                    reason="invalid_return_schema",
                )
                return {
                    "status": "error",
                    "error": f"tool return schema violation: invalid returns schema: {exc}",
                    "request_id": request_id,
                }
        try:
            sanitized, report = sanitize_tool_result_with_report(result.get("data"))
        except PluginToolResultError as exc:
            self.audit_logger.record(
                "plugin.tool_result_rejected",
                "error",
                request_id=request_id,
                plugin=plugin_name,
                action=tool_name,
                details={
                    "plugin_id": plugin_name,
                    "plugin_version": metadata.version,
                    "tool_name": tool_name,
                    "request_id": request_id,
                    "reason": str(exc),
                    "decision": "deny_if_too_large",
                    "version": metadata.version,
                },
                plugin_id=plugin_name,
                plugin_version=metadata.version,
                decision="deny",
                reason="tool_result_rejected",
            )
            return {
                "status": "error",
                "error": str(exc),
                "request_id": request_id,
            }
        if report.changed:
            self.audit_logger.record(
                "plugin.tool_result_sanitized",
                "success",
                request_id=request_id,
                plugin=plugin_name,
                action=tool_name,
                details={
                    "plugin_id": plugin_name,
                    "plugin_version": metadata.version,
                    "tool_name": tool_name,
                    "request_id": request_id,
                    "sanitized_fields": report.sanitized_fields,
                    "truncated_fields": report.truncated_fields,
                    "original_size_bytes": report.original_size_bytes,
                    "final_size_bytes": report.final_size_bytes,
                    "decision": "allow_after_sanitization",
                    "version": metadata.version,
                },
                plugin_id=plugin_name,
                plugin_version=metadata.version,
                decision="allow_after_sanitization",
                reason="tool_result_sanitized",
            )
        result["data"] = sanitized
        result["_tool_result_metadata"] = {
            "sanitized_fields": report.sanitized_fields,
            "truncated_fields": report.truncated_fields,
            "original_size_bytes": report.original_size_bytes,
            "final_size_bytes": report.final_size_bytes,
        }
        return result

    def tools(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for name, metadata in self.loader.get_all_plugins().items():
            result[name] = metadata.tool_entries()
        return result

    def event_listeners(self) -> dict[str, dict[str, list[str]]]:
        result: dict[str, dict[str, list[str]]] = {}
        for name, metadata in self.loader.get_all_plugins().items():
            result[name] = metadata.event_listener_entries()
        return result

    def middlewares(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for name, metadata in self.loader.get_all_plugins().items():
            result[name] = metadata.middleware_entries()
        return result

    def memory_providers(self) -> dict[str, dict[str, str]]:
        result: dict[str, dict[str, str]] = {}
        for name, metadata in self.loader.get_all_plugins().items():
            result[name] = metadata.memory_provider_entries()
        return result

    def call_memory_provider(
        self,
        plugin_name: str,
        provider_name: str,
        operation: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request_id = new_request_id()
        if plugin_name not in self.sandboxes:
            self.start_plugin(plugin_name)
        sandbox = self.sandboxes[plugin_name]
        if provider_name not in sandbox.meta.memory_provider_entries():
            response = {"status": "error", "error": f"memory provider is not declared by plugin: {provider_name}"}
            response["request_id"] = request_id
            self.audit_logger.record(
                "plugin.memory_provider_call",
                "error",
                request_id=request_id,
                plugin=plugin_name,
                action=provider_name,
                details={"operation": operation, "error": response["error"]},
            )
            return response
        result = self._execute_plugin_action(
            plugin_name,
            request_id,
            "memory_provider",
            provider_name,
            lambda: sandbox.execute_with_timeout(
                action="call_memory_provider",
                payload={
                    "provider_name": provider_name,
                    "operation": operation,
                    "payload": payload or {},
                },
                timeout=sandbox.meta.runtime.timeout_seconds,
                request_id=request_id,
            ),
            timeout_error="plugin execution timed out",
            failure_error_key="error",
            timeout_seconds=sandbox.meta.runtime.timeout_seconds,
        )
        result.setdefault("request_id", request_id)
        self.audit_logger.record(
            "plugin.memory_provider_call",
            "success" if result.get("status") == "success" else "error",
            request_id=request_id,
            plugin=plugin_name,
            action=provider_name,
            details={
                "operation": operation,
                "payload_keys": sorted((payload or {}).keys()),
                **({"error": str(result.get("error"))} if result.get("status") != "success" else {}),
            },
        )
        return result

    def run_middlewares(self, context: dict[str, Any]) -> dict[str, Any]:
        current = dict(context)
        for plugin_name, middleware_entries in sorted(self.middlewares().items()):
            if not middleware_entries:
                continue
            if plugin_name not in self.sandboxes:
                self.start_plugin(plugin_name)
            sandbox = self.sandboxes[plugin_name]
            for middleware_name in sorted(middleware_entries):
                request_id = new_request_id()
                result = self._execute_plugin_action(
                    plugin_name,
                    request_id,
                    "middleware",
                    middleware_name,
                    lambda: sandbox.execute_with_timeout(
                        action="run_middleware",
                        payload={"middleware_name": middleware_name, "context": current},
                        timeout=sandbox.meta.runtime.timeout_seconds,
                        request_id=request_id,
                    ),
                    timeout_error="plugin execution timed out",
                    failure_error_key="error",
                    timeout_seconds=sandbox.meta.runtime.timeout_seconds,
                )
                self.audit_logger.record(
                    "plugin.middleware_call",
                    "success" if result.get("status") == "success" else "error",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=middleware_name,
                    details={
                        "context_keys": sorted(current.keys()),
                        **({"error": str(result.get("error"))} if result.get("status") != "success" else {}),
                    },
                )
                if result.get("status") != "success":
                    raise PluginMiddlewareError(
                        f"{plugin_name}.{middleware_name} failed: {result.get('error', 'unknown error')}"
                    )
                data = result.get("data")
                if not isinstance(data, dict):
                    raise PluginMiddlewareError(f"{plugin_name}.{middleware_name} must return a dict")
                current = data
        return current

    def _register_event_listeners(self, name: str, sandbox: SandboxManager) -> None:
        callbacks: list[tuple[str, EventCallback]] = []
        for event in sandbox.meta.event_listener_entries():
            callback = self._make_plugin_event_callback(name)
            self.event_bus.subscribe(event, callback)
            callbacks.append((event, callback))
        self._event_listener_callbacks[name] = callbacks

    def _unregister_event_listeners(self, name: str) -> None:
        callbacks = self._event_listener_callbacks.pop(name, [])
        for event, callback in callbacks:
            self.event_bus.unsubscribe(event, callback)

    def _make_plugin_event_callback(self, plugin_name: str) -> EventCallback:
        def callback(event: Event) -> Any:
            sandbox = self.sandboxes.get(plugin_name)
            if not sandbox:
                return {"status": "error", "error": f"plugin is not active: {plugin_name}"}
            request_id = new_request_id()
            return self._execute_plugin_action(
                plugin_name,
                request_id,
                "event_listener",
                event.name,
                lambda: sandbox.execute_with_timeout(
                    action="handle_event",
                    payload={
                        "event": {
                            "name": event.name,
                            "data": event.data,
                            "source": event.source,
                            "created_at": event.created_at,
                        }
                    },
                    timeout=sandbox.meta.runtime.timeout_seconds,
                    request_id=request_id,
                ),
                timeout_error="plugin execution timed out",
                failure_error_key="error",
                timeout_seconds=sandbox.meta.runtime.timeout_seconds,
            )

        return callback

    def _runtime_state_for(self, plugin_name: str) -> "PluginRuntimeState":
        state = self._runtime_states.get(plugin_name)
        metadata = self.loader.get_plugin(plugin_name)
        if state and metadata and state.max_concurrency == metadata.runtime.max_concurrency:
            return state
        max_concurrency = metadata.runtime.max_concurrency if metadata else 1
        state = PluginRuntimeState(max_concurrency=max_concurrency)
        self._runtime_states[plugin_name] = state
        return state

    def _execute_plugin_action(
        self,
        plugin_name: str,
        request_id: str,
        action_type: str,
        action_name: str,
        runner: Any,
        *,
        timeout_error: str,
        failure_error_key: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        installed = self.loader.get_installed(plugin_name)
        if not installed or installed.status != PluginStatus.ENABLED:
            status = installed.status.value if installed else "missing"
            self.audit_logger.record(
                "plugin.action_denied",
                "error",
                request_id=request_id,
                plugin=plugin_name,
                action=action_name,
                details={
                    "action_type": action_type,
                    "status": status,
                    "reason": f"plugin is not enabled: {plugin_name} ({status})",
                    "version": installed.metadata.version if installed else None,
                },
            )
            return {
                "status": "error",
                "error": f"plugin is not enabled: {plugin_name} ({status})",
                "request_id": request_id,
            }
        state = self._runtime_state_for(plugin_name)
        if not state.semaphore.acquire(blocking=False):
            result = {
                "status": "error",
                "error": f"plugin concurrency limit exceeded: {plugin_name}",
                "request_id": request_id,
            }
            self.audit_logger.record(
                "plugin.concurrency_rejected",
                "error",
                request_id=request_id,
                plugin=plugin_name,
                action=action_name,
                details={
                    "action_type": action_type,
                    "max_concurrency": state.max_concurrency,
                },
            )
            return result
        try:
            result = runner()
            if not isinstance(result, dict):
                result = {"status": "error", "error": "plugin action returned non-dict response"}
            result.setdefault("request_id", request_id)
            if result.get("status") == "success":
                self._record_plugin_success(plugin_name, request_id, action_type, action_name)
            else:
                self._record_plugin_failure(
                    plugin_name,
                    request_id,
                    action_type,
                    action_name,
                    str(result.get(failure_error_key, "unknown error")),
                    timeout_seconds=timeout_seconds,
                    timed_out=str(result.get(failure_error_key, "")) == timeout_error,
                )
            return result
        finally:
            state.semaphore.release()

    def _record_plugin_success(
        self,
        plugin_name: str,
        request_id: str,
        action_type: str,
        action_name: str,
    ) -> None:
        state = self._runtime_state_for(plugin_name)
        state.consecutive_failures = 0
        self.audit_logger.record(
            "plugin.runtime_success",
            "success",
            request_id=request_id,
            plugin=plugin_name,
            action=action_name,
            details={"action_type": action_type},
        )

    def _record_plugin_failure(
        self,
        plugin_name: str,
        request_id: str,
        action_type: str,
        action_name: str,
        error: str,
        *,
        timeout_seconds: float,
        timed_out: bool,
    ) -> None:
        state = self._runtime_state_for(plugin_name)
        state.consecutive_failures += 1
        metadata = self.loader.get_plugin(plugin_name)
        threshold = metadata.runtime.failure_threshold if metadata else 3
        self.audit_logger.record(
            "plugin.runtime_failure",
            "error",
            request_id=request_id,
            plugin=plugin_name,
            action=action_name,
            details={
                "action_type": action_type,
                "error": error,
                "consecutive_failures": state.consecutive_failures,
                "failure_threshold": threshold,
                "timed_out": timed_out,
                "timeout_seconds": timeout_seconds,
            },
        )
        if not metadata or not metadata.runtime.disable_on_failure_threshold:
            return
        if state.consecutive_failures < threshold:
            return
        self.disable_plugin(plugin_name)
        self.audit_logger.record(
            "plugin.circuit_opened",
            "error",
            request_id=request_id,
            plugin=plugin_name,
            action=action_name,
            details={
                "action_type": action_type,
                "consecutive_failures": state.consecutive_failures,
                "failure_threshold": threshold,
            },
        )


class PluginRuntimeState:
    def __init__(self, max_concurrency: int):
        self.max_concurrency = max_concurrency
        self.semaphore = threading.BoundedSemaphore(max_concurrency)
        self.consecutive_failures = 0
