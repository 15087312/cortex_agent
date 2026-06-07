from __future__ import annotations

import fnmatch
import ipaddress
import json
import os
import re
import socket
import threading
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPRedirectHandler, OpenerDirector, ProxyHandler, Request, build_opener

from .audit import AuditLogger, NullAuditLogger, new_request_id
from .config import PluginConfigManager
from .event_bus import EventBus, global_event_bus
from .models import InstalledPlugin, PermissionName, PluginMetadata, PluginStatus
from .tool_security import RequestPermissionRegistry, permission_for_gateway_request


ALLOWED_NETWORK_METHODS = {"GET", "POST"}
DEFAULT_NETWORK_TIMEOUT_SECONDS = 5.0
MIN_NETWORK_TIMEOUT_SECONDS = 0.1
MAX_NETWORK_TIMEOUT_SECONDS = 10.0
MAX_NETWORK_URL_LENGTH = 2048
MAX_NETWORK_REQUEST_BODY_BYTES = 64 * 1024
MAX_NETWORK_RESPONSE_BYTES = 1024 * 1024
MAX_NETWORK_RESPONSE_HEADER_BYTES = 32 * 1024
MAX_NETWORK_RESPONSE_HEADERS = 100
METADATA_SERVICE_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("fd00:ec2::254"),
}
_DNS_PIN_LOCK = threading.Lock()


class PermissionDenied(PermissionError):
    pass


class NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None

    def http_error_302(self, req: Request, fp: Any, code: int, msg: str, headers: Any) -> None:
        raise HTTPError(req.full_url, code, msg, headers, fp)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


class PluginGateway:
    """Capability gateway between plugins and the Humanoid AGI core."""

    def __init__(
        self,
        data_dir: str | Path = "data/plugins",
        event_bus: EventBus | None = None,
        memory_store: dict[str, Any] | None = None,
        config_store: dict[str, Any] | None = None,
        output_store: list[dict[str, Any]] | None = None,
        audit_logger: AuditLogger | NullAuditLogger | None = None,
        permission_registry: RequestPermissionRegistry | None = None,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.event_bus = event_bus or global_event_bus
        self.memory_store = memory_store if memory_store is not None else {}
        self.config_store = config_store if config_store is not None else {}
        self.output_store = output_store if output_store is not None else []
        self.audit_logger = audit_logger or NullAuditLogger()
        self.config_manager = PluginConfigManager(self.data_dir)
        self.active_sandboxes: dict[str, Any] = {}
        self.plugins: dict[str, InstalledPlugin] = {}
        self.permission_registry = permission_registry or RequestPermissionRegistry()

    def register_plugin(self, plugin: InstalledPlugin | PluginMetadata) -> None:
        installed = self._as_installed(plugin)
        self.plugins[installed.metadata.name] = installed

    def register_sandbox(self, sandbox: Any) -> None:
        self.active_sandboxes[sandbox.meta.name] = sandbox
        self.register_plugin(sandbox.installed_plugin)

    def unregister_sandbox(self, plugin_name: str) -> None:
        self.active_sandboxes.pop(plugin_name, None)

    def call_plugin_tool(
        self,
        plugin_name: str,
        tool_name: str,
        args: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        sandbox = self.active_sandboxes.get(plugin_name)
        if not sandbox:
            return self._audited_response(
                {"status": "error", "error": f"plugin is not active: {plugin_name}"},
                event="plugin.tool_call",
                request_id=request_id,
                plugin=plugin_name,
                action=tool_name,
                details={"reason": "inactive"},
            )
        if tool_name not in sandbox.meta.tool_entries():
            return self._audited_response(
                {"status": "error", "error": f"tool is not declared by plugin: {tool_name}"},
                event="plugin.tool_call",
                request_id=request_id,
                plugin=plugin_name,
                action=tool_name,
                details={"reason": "undeclared_tool"},
            )
        result = sandbox.execute_with_timeout(
            action="execute_tool",
            payload={"tool_name": tool_name, "args": args},
            timeout=sandbox.meta.runtime.timeout_seconds,
            request_id=request_id,
        )
        return self._audited_response(
            result,
            event="plugin.tool_call",
            request_id=request_id,
            plugin=plugin_name,
            action=tool_name,
            details={"arg_keys": sorted(args.keys())},
        )

    def handle_sandbox_request(
        self,
        plugin_name: str,
        request_type: str,
        payload: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        try:
            metadata = self._metadata_for(plugin_name)
            if request_type == "memory.read":
                key = str(payload.get("key", ""))
                return self._audited_response(
                    {"status": "success", "data": self.read_memory(metadata.metadata, key, request_id=request_id)},
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"key": key},
                )
            if request_type == "memory.write":
                key = str(payload.get("key", ""))
                self.write_memory(metadata.metadata, key, payload.get("value"), request_id=request_id)
                return self._audited_response(
                    {"status": "success", "data": True},
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"key": key},
                )
            if request_type == "config.read":
                key = str(payload.get("key", ""))
                return self._audited_response(
                    {"status": "success", "data": self.read_config(metadata.metadata, key, request_id=request_id)},
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"key": key},
                )
            if request_type == "network.outbound":
                return self._audited_response(
                    {
                        "status": "success",
                        "data": self.network_request(metadata.metadata, payload, request_id=request_id),
                    },
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"url": str(payload.get("url", "")), "method": str(payload.get("method", "GET")).upper()},
                )
            if request_type == "fs.read":
                relative_path = str(payload.get("path", ""))
                return self._audited_response(
                    {
                        "status": "success",
                        "data": self.read_plugin_file(metadata.metadata, relative_path, request_id=request_id),
                    },
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"path": relative_path},
                )
            if request_type == "fs.write":
                relative_path = str(payload.get("path", ""))
                self.write_plugin_file(
                    metadata.metadata,
                    relative_path,
                    str(payload.get("content", "")),
                    request_id=request_id,
                )
                return self._audited_response(
                    {"status": "success", "data": True},
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"path": relative_path},
                )
            if request_type == "event.publish":
                event_name = str(payload.get("event", ""))
                return self._audited_response(
                    {
                        "status": "success",
                        "data": self.event_bus.publish(
                            event_name,
                            payload.get("data"),
                            source=plugin_name,
                        ),
                    },
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={"event": event_name},
                )
            if request_type == "output.send":
                output = self.send_output(metadata.metadata, payload, request_id=request_id)
                return self._audited_response(
                    {"status": "success", "data": output},
                    event="plugin.gateway_request",
                    request_id=request_id,
                    plugin=plugin_name,
                    action=request_type,
                    details={
                        "channel": output["channel"],
                        "content_type": output["content_type"],
                    },
                )
            return self._audited_response(
                {"status": "error", "error": f"unknown gateway request: {request_type}"},
                event="plugin.gateway_request",
                request_id=request_id,
                plugin=plugin_name,
                action=request_type,
                details={"reason": "unknown_request"},
            )
        except Exception as exc:
            return self._audited_response(
                {"status": "error", "error": str(exc)},
                event="plugin.gateway_request",
                request_id=request_id,
                plugin=plugin_name,
                action=request_type,
                details={"error_type": type(exc).__name__},
            )

    def read_memory(self, metadata: PluginMetadata, key: str, request_id: str | None = None) -> Any:
        self._require_request_scope(metadata, PermissionName.MEMORY_READ, request_id)
        self._require(metadata, PermissionName.MEMORY_READ)
        return self.memory_store.get(key)

    def write_memory(self, metadata: PluginMetadata, key: str, value: Any, request_id: str | None = None) -> None:
        self._require_request_scope(metadata, PermissionName.MEMORY_WRITE, request_id)
        self._require(metadata, PermissionName.MEMORY_WRITE)
        self.memory_store[key] = value

    def read_config(self, metadata: PluginMetadata, key: str, request_id: str | None = None) -> Any:
        self._require_request_scope(metadata, PermissionName.CONFIG_READ, request_id)
        self._require(metadata, PermissionName.CONFIG_READ)
        if self.config_manager.has_value(metadata.name, key):
            return self.config_manager.read_value(metadata.name, key)
        return self.config_store.get(f"{metadata.name}.{key}")

    def network_request(
        self,
        metadata: PluginMetadata,
        payload: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or new_request_id()
        url = str(payload.get("url", "")).strip()
        method = str(payload.get("method", "GET")).upper()
        resolved_ips: list[str] = []
        try:
            self._require_request_scope(metadata, PermissionName.NETWORK_OUTBOUND, request_id)
            self._require_declared_permission(metadata, PermissionName.NETWORK_OUTBOUND)
            self._require(metadata, PermissionName.NETWORK_OUTBOUND)
            url = self._network_url(payload)
            method = self._network_method(payload)
            timeout = self._network_timeout(payload)
            self._reject_custom_network_headers(payload)
            body_bytes = self._network_body_bytes(method, payload)
            if not self._url_allowed(metadata, url, method):
                raise PermissionDenied(f"network target is not whitelisted: {url}")
            first_addresses = self._validate_network_target(url)
            resolved_ips = self._addresses_to_strings(first_addresses)
            request = Request(url=url, method=method)
            if body_bytes is not None:
                request.add_header("Content-Type", "application/json")
            try:
                with self._open_pinned_network_request(request, body_bytes, timeout, first_addresses) as response:
                    self._reject_dns_rebinding(url, first_addresses)
                    raw = response.read(MAX_NETWORK_RESPONSE_BYTES + 1)
                    if len(raw) > MAX_NETWORK_RESPONSE_BYTES:
                        raise PermissionDenied("network response exceeds size limit")
                    result = {
                        "status_code": response.status,
                        "headers": self._safe_response_headers(response.headers.items()),
                        "body": raw.decode("utf-8", errors="replace"),
                    }
            except HTTPError as exc:
                self._raise_network_redirect_denied(metadata, url, method, exc)
                raise PermissionDenied(f"network request failed: HTTP {exc.code}") from exc
            except URLError as exc:
                raise PermissionDenied(f"network request failed: {exc.reason}") from exc
            self._audit_network_decision(
                metadata,
                request_id=request_id,
                url=url,
                method=method,
                resolved_ips=resolved_ips,
                decision="allow",
                reason="allowed",
            )
            return result
        except Exception as exc:
            self._audit_network_decision(
                metadata,
                request_id=request_id,
                url=url,
                method=method,
                resolved_ips=resolved_ips,
                decision="deny",
                reason=self._network_denial_reason(exc),
            )
            raise

    def read_plugin_file(self, metadata: PluginMetadata, relative_path: str, request_id: str | None = None) -> str:
        self._require_request_scope(metadata, PermissionName.FS_READ, request_id)
        self._require(metadata, PermissionName.FS_READ)
        path = self._plugin_data_path(metadata, relative_path, for_write=False)
        return path.read_text(encoding="utf-8")

    def write_plugin_file(
        self,
        metadata: PluginMetadata,
        relative_path: str,
        content: str,
        request_id: str | None = None,
    ) -> None:
        self._require_request_scope(metadata, PermissionName.FS_WRITE, request_id)
        self._require(metadata, PermissionName.FS_WRITE)
        path = self._plugin_data_path(metadata, relative_path, for_write=True)
        self._write_plugin_file_without_following_links(path, content)

    def send_output(
        self,
        metadata: PluginMetadata,
        payload: dict[str, Any],
        request_id: str | None = None,
    ) -> dict[str, Any]:
        self._require_request_scope(metadata, PermissionName.OUTPUT_SEND, request_id)
        self._require(metadata, PermissionName.OUTPUT_SEND)
        channel = str(payload.get("channel") or "default")
        if not channel or len(channel) > 64 or any(item in channel for item in ["/", "\\", "\n", "\r"]):
            raise PermissionDenied(f"invalid output channel: {channel}")
        content = payload.get("content")
        if not isinstance(content, (str, int, float, bool, dict, list)) and content is not None:
            raise PermissionDenied("output content must be JSON serializable")
        content_type = str(payload.get("content_type") or "text/plain")
        if len(content_type) > 128 or "\n" in content_type or "\r" in content_type:
            raise PermissionDenied(f"invalid output content type: {content_type}")
        message = {
            "plugin": metadata.name,
            "channel": channel,
            "content": content,
            "content_type": content_type,
        }
        self.output_store.append(message)
        return message

    def _metadata_for(self, plugin_name: str) -> InstalledPlugin:
        installed = self.plugins.get(plugin_name)
        if not installed:
            sandbox = self.active_sandboxes.get(plugin_name)
            if sandbox:
                installed = sandbox.installed_plugin
        if not installed:
            raise PermissionDenied(f"unknown plugin: {plugin_name}")
        if installed.status != PluginStatus.ENABLED:
            raise PermissionDenied(f"plugin is not enabled: {plugin_name} ({installed.status.value})")
        return installed

    def _require(self, metadata: PluginMetadata, permission: PermissionName) -> None:
        installed = self._metadata_for(metadata.name)
        if not installed.has_granted_permission(permission):
            raise PermissionDenied(f"{metadata.name} does not have {permission.value}")

    def _require_declared_permission(self, metadata: PluginMetadata, permission: PermissionName) -> None:
        if not metadata.has_permission(permission):
            raise PermissionDenied(f"{metadata.name} has not declared {permission.value}")

    def _require_request_scope(
        self,
        metadata: PluginMetadata,
        permission: PermissionName,
        request_id: str | None,
    ) -> None:
        if permission_for_gateway_request(permission.value) is None:
            return
        installed = self._metadata_for(metadata.name)
        scope = self.permission_registry.get(request_id)
        plugin_declared = metadata.has_permission(permission)
        plugin_granted = installed.has_granted_permission(permission)
        if scope is None:
            self._audit_permission_denied(
                metadata,
                request_id=request_id,
                permission=permission,
                tool_name=None,
                plugin_declared=plugin_declared,
                plugin_granted=plugin_granted,
                tool_allowed=False,
                policy_allowed=True,
                reason="missing_request_scope",
            )
            raise PermissionDenied(f"missing active tool permission scope for {permission.value}")
        if scope.plugin_id != metadata.name:
            status = scope.permission_status(permission)
            self._audit_permission_denied(
                metadata,
                request_id=request_id,
                permission=permission,
                tool_name=scope.tool_name,
                plugin_declared=plugin_declared,
                plugin_granted=plugin_granted,
                tool_allowed=status["tool_permission_allowed"],
                policy_allowed=status["policy_permission_allowed"],
                reason="request_scope_plugin_mismatch",
            )
            raise PermissionDenied(f"request scope does not belong to plugin: {metadata.name}")
        status = scope.permission_status(permission)
        if scope.allows(permission):
            return
        reason = "permission_not_in_effective_scope"
        if not status["plugin_declared"]:
            reason = "plugin_permission_not_declared"
        elif not status["plugin_granted"]:
            reason = "plugin_permission_not_granted"
        elif not status["tool_permission_allowed"]:
            reason = "tool_permission_not_declared"
        elif not status["policy_permission_allowed"]:
            reason = "permission_denied_by_policy"
        self._audit_permission_denied(
            metadata,
            request_id=request_id,
            permission=permission,
            tool_name=scope.tool_name,
            plugin_declared=status["plugin_declared"],
            plugin_granted=status["plugin_granted"],
            tool_allowed=status["tool_permission_allowed"],
            policy_allowed=status["policy_permission_allowed"],
            reason=reason,
        )
        raise PermissionDenied(f"{metadata.name}.{scope.tool_name} does not have {permission.value}")

    def _audit_permission_denied(
        self,
        metadata: PluginMetadata,
        *,
        request_id: str | None,
        permission: PermissionName,
        tool_name: str | None,
        plugin_declared: bool,
        plugin_granted: bool,
        tool_allowed: bool,
        policy_allowed: bool,
        reason: str,
    ) -> None:
        plugin_allowed = plugin_declared and plugin_granted
        event = "plugin.permission.denied" if not plugin_allowed or not policy_allowed else "plugin.tool_permission.denied"
        self.audit_logger.record(
            event,
            "error",
            request_id=request_id or new_request_id(),
            plugin=metadata.name,
            action=tool_name or permission.value,
            details={
                "request_id": request_id,
                "plugin_id": metadata.name,
                "plugin_version": metadata.version,
                "tool_name": tool_name,
                "requested_permission": permission.value,
                "plugin_permission_allowed": plugin_allowed,
                "plugin_permission_declared": plugin_declared,
                "plugin_permission_granted": plugin_granted,
                "tool_permission_allowed": tool_allowed,
                "policy_permission_allowed": policy_allowed,
                "decision": "deny",
                "reason": reason,
                "version": metadata.version,
            },
            plugin_id=metadata.name,
            plugin_version=metadata.version,
            permission=permission.value,
            decision="deny",
            reason=reason,
        )

    def _url_allowed(self, metadata: PluginMetadata, url: str, method: str = "GET") -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False
        installed = self._metadata_for(metadata.name)
        declared_rule = metadata.permission_value(PermissionName.NETWORK_OUTBOUND, False)
        granted_rule = installed.granted_permission_value(PermissionName.NETWORK_OUTBOUND, False)
        return self._network_rule_allows(declared_rule, url, method) and self._network_rule_allows(
            granted_rule,
            url,
            method,
        )

    def _network_rule_allows(self, rule: Any, url: str, method: str) -> bool:
        parsed = urlparse(url)
        if rule is True:
            return False
        patterns = rule if isinstance(rule, list) else [rule]
        for pattern in patterns:
            if isinstance(pattern, dict):
                if "methods" in pattern:
                    try:
                        pattern_methods = {str(item).upper() for item in pattern.get("methods", [])}
                    except TypeError:
                        continue
                    if method.upper() not in pattern_methods:
                        continue
                pattern = pattern.get("url") or pattern.get("target") or pattern.get("pattern") or pattern.get("host")
            pattern = str(pattern).strip()
            if not pattern:
                continue
            if "://" not in pattern:
                pattern = f"https://{pattern}"
            pattern_parts = urlparse(pattern)
            if pattern_parts.scheme not in {"http", "https"} or not pattern_parts.netloc:
                continue
            if pattern_parts.username or pattern_parts.password:
                continue
            if not self._port_allowed_by_pattern(parsed, pattern_parts):
                continue
            has_path_or_query_rule = pattern_parts.path not in {"", "/"} or bool(pattern_parts.query)
            if has_path_or_query_rule:
                if fnmatch.fnmatchcase(self._canonical_url_for_match(url), self._canonical_url_for_match(pattern)):
                    return True
                continue
            if pattern_parts.scheme == parsed.scheme and fnmatch.fnmatchcase(
                self._canonical_netloc(parsed),
                self._canonical_netloc(pattern_parts),
            ):
                return True
        return False

    def _canonical_url_for_match(self, url: str) -> str:
        parsed = urlparse(url)
        path = parsed.path or "/"
        query = f"?{parsed.query}" if parsed.query else ""
        return f"{parsed.scheme.lower()}://{self._canonical_netloc(parsed)}{path}{query}"

    def _canonical_netloc(self, parsed: Any) -> str:
        hostname = self._normalize_hostname(parsed.hostname or "", allow_wildcard=True)
        port = parsed.port
        if port is None:
            return hostname
        return f"{hostname}:{port}"

    def _port_allowed_by_pattern(self, parsed: Any, pattern_parts: Any) -> bool:
        try:
            request_port = parsed.port
            pattern_port = pattern_parts.port
        except ValueError:
            return False
        if pattern_port is None:
            return request_port is None
        return request_port == pattern_port

    def _network_url(self, payload: dict[str, Any]) -> str:
        url = str(payload.get("url", "")).strip()
        if not url:
            raise PermissionDenied("network URL is required")
        if len(url) > MAX_NETWORK_URL_LENGTH:
            raise PermissionDenied("network URL exceeds length limit")
        if any(ord(char) <= 32 or ord(char) == 127 for char in url):
            raise PermissionDenied("network URL contains unsafe characters")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise PermissionDenied(f"network target is invalid: {url}")
        if parsed.username or parsed.password:
            raise PermissionDenied("network URL credentials are not allowed")
        if parsed.fragment:
            raise PermissionDenied("network URL fragments are not allowed")
        if not parsed.hostname:
            raise PermissionDenied(f"network target is invalid: {url}")
        self._normalize_hostname(parsed.hostname)
        try:
            parsed.port
        except ValueError as exc:
            raise PermissionDenied(f"network target has an invalid port: {url}") from exc
        return url

    def _network_method(self, payload: dict[str, Any]) -> str:
        method = str(payload.get("method", "GET")).upper()
        if method not in ALLOWED_NETWORK_METHODS:
            raise PermissionDenied("only GET and POST are allowed through the gateway")
        return method

    def _network_timeout(self, payload: dict[str, Any]) -> float:
        raw_timeout = payload.get("timeout", DEFAULT_NETWORK_TIMEOUT_SECONDS)
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise PermissionDenied("network timeout must be a number") from exc
        if not MIN_NETWORK_TIMEOUT_SECONDS <= timeout <= MAX_NETWORK_TIMEOUT_SECONDS:
            raise PermissionDenied(
                f"network timeout must be between {MIN_NETWORK_TIMEOUT_SECONDS} and "
                f"{MAX_NETWORK_TIMEOUT_SECONDS} seconds"
            )
        return timeout

    def _reject_custom_network_headers(self, payload: dict[str, Any]) -> None:
        headers = payload.get("headers")
        if headers not in (None, {}):
            raise PermissionDenied("custom network headers are not allowed through the gateway")

    def _network_body_bytes(self, method: str, payload: dict[str, Any]) -> bytes | None:
        if "body" not in payload or payload.get("body") is None:
            return None
        if method != "POST":
            raise PermissionDenied("network request bodies are only allowed for POST")
        try:
            body_bytes = json.dumps(payload.get("body"), separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise PermissionDenied("network request body must be JSON serializable") from exc
        if len(body_bytes) > MAX_NETWORK_REQUEST_BODY_BYTES:
            raise PermissionDenied("network request body exceeds size limit")
        return body_bytes

    def _safe_response_headers(self, headers: Any) -> dict[str, str]:
        sanitized: dict[str, str] = {}
        total_size = 0
        for index, (name, value) in enumerate(headers):
            if index >= MAX_NETWORK_RESPONSE_HEADERS:
                raise PermissionDenied("network response has too many headers")
            header_name = str(name)
            header_value = str(value)
            if "\r" in header_name or "\n" in header_name or "\r" in header_value or "\n" in header_value:
                raise PermissionDenied("network response contains unsafe headers")
            total_size += len(header_name.encode("utf-8")) + len(header_value.encode("utf-8"))
            if total_size > MAX_NETWORK_RESPONSE_HEADER_BYTES:
                raise PermissionDenied("network response headers exceed size limit")
            sanitized[header_name] = header_value
        return sanitized

    def _open_network_request(self, request: Request, data: bytes | None, timeout: float) -> Any:
        opener: OpenerDirector = build_opener(NoRedirectHandler, ProxyHandler({}))
        return opener.open(request, data=data, timeout=timeout)

    def _open_pinned_network_request(
        self,
        request: Request,
        data: bytes | None,
        timeout: float,
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> Any:
        parsed = urlparse(request.full_url)
        hostname = self._normalize_hostname(parsed.hostname or "")
        pinned_sockaddrs = self._pinned_getaddrinfo_results(hostname, addresses)
        original_getaddrinfo = socket.getaddrinfo

        def pinned_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                normalized_host = self._normalize_hostname(str(host))
            except PermissionDenied:
                normalized_host = str(host).lower().rstrip(".")
            if normalized_host == hostname:
                return [
                    (
                        family,
                        socktype,
                        proto,
                        canonname,
                        self._sockaddr_with_port(sockaddr, port),
                    )
                    for family, socktype, proto, canonname, sockaddr in pinned_sockaddrs
                ]
            return original_getaddrinfo(host, port, *args, **kwargs)

        with _DNS_PIN_LOCK:
            socket.getaddrinfo = cast(Any, pinned_getaddrinfo)
            try:
                return self._open_network_request(request, data, timeout)
            finally:
                socket.getaddrinfo = original_getaddrinfo

    def _pinned_getaddrinfo_results(
        self,
        hostname: str,
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> list[tuple[Any, Any, Any, str, tuple[Any, ...]]]:
        results: list[tuple[Any, Any, Any, str, tuple[Any, ...]]] = []
        for address in sorted(addresses, key=lambda item: str(item)):
            family = socket.AF_INET6 if address.version == 6 else socket.AF_INET
            sockaddr: tuple[Any, ...]
            if address.version == 6:
                sockaddr = (str(address), 0, 0, 0)
            else:
                sockaddr = (str(address), 0)
            results.append((family, socket.SOCK_STREAM, 6, hostname, sockaddr))
        return results

    def _sockaddr_with_port(self, sockaddr: tuple[Any, ...], port: Any) -> tuple[Any, ...]:
        if len(sockaddr) >= 4:
            return (sockaddr[0], port, *sockaddr[2:])
        return (sockaddr[0], port)

    def _validate_network_target(self, url: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            raise PermissionDenied(f"network target is invalid: {url}")
        hostname = self._normalize_hostname(hostname)
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            pass
        else:
            raise PermissionDenied("IP literal network targets are not allowed")
        try:
            addresses = self._resolve_host_addresses(hostname)
        except OSError as exc:
            raise PermissionDenied(f"network target cannot be resolved: {hostname}") from exc
        if not addresses:
            raise PermissionDenied(f"network target cannot be resolved: {hostname}")
        for address in addresses:
            if self._is_blocked_address(address):
                raise PermissionDenied(f"network target resolves to a blocked address: {hostname}")
        return addresses

    def _reject_unsafe_network_target(self, url: str) -> None:
        self._validate_network_target(url)

    def _reject_dns_rebinding(
        self,
        url: str,
        first_addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address],
    ) -> None:
        second_addresses = self._validate_network_target(url)
        if second_addresses != first_addresses:
            raise PermissionDenied("DNS rebinding detected")

    def _raise_network_redirect_denied(
        self,
        metadata: PluginMetadata,
        original_url: str,
        method: str,
        exc: HTTPError,
    ) -> None:
        if not 300 <= exc.code < 400:
            return
        location = exc.headers.get("Location") if exc.headers else None
        if not location:
            raise PermissionDenied("network redirect denied: missing Location header") from exc
        redirect_url = urljoin(original_url, str(location))
        try:
            redirect_url = self._network_url({"url": redirect_url})
            if not self._url_allowed(metadata, redirect_url, method):
                raise PermissionDenied(f"network redirect target is not whitelisted: {redirect_url}")
            self._validate_network_target(redirect_url)
        except PermissionDenied as redirect_exc:
            raise PermissionDenied(f"network redirect denied: {redirect_exc}") from exc
        raise PermissionDenied(f"network redirect denied: {redirect_url}") from exc

    def _resolve_host_addresses(self, hostname: str) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
        hostname = self._normalize_hostname(hostname)
        try:
            return {ipaddress.ip_address(hostname)}
        except ValueError:
            pass
        addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            if family not in {socket.AF_INET, socket.AF_INET6}:
                continue
            addresses.add(ipaddress.ip_address(sockaddr[0]))
        return addresses

    def _is_blocked_address(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        if address in METADATA_SERVICE_ADDRESSES:
            return True
        return any(
            [
                address.is_loopback,
                address.is_private,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
                address.is_unspecified,
            ]
        )

    def _normalize_hostname(self, hostname: str, *, allow_wildcard: bool = False) -> str:
        hostname = hostname.strip().lower().rstrip(".")
        if not hostname:
            raise PermissionDenied("network target hostname is required")
        if len(hostname) > 253:
            raise PermissionDenied("network target hostname exceeds length limit")
        if any(ord(char) <= 32 or ord(char) == 127 for char in hostname):
            raise PermissionDenied("network target hostname contains unsafe characters")
        if any(char in hostname for char in ["/", "\\", "@"]):
            raise PermissionDenied("network target hostname contains unsafe characters")
        if "*" in hostname and not allow_wildcard:
            raise PermissionDenied("network target hostname wildcards are not allowed")
        if not re.match(r"^[a-z0-9.*:-]+$", hostname):
            raise PermissionDenied("network target hostname contains unsupported characters")
        return hostname

    def _addresses_to_strings(self, addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address]) -> list[str]:
        return sorted(str(address) for address in addresses)

    def _plugin_data_path(self, metadata: PluginMetadata, relative_path: str, *, for_write: bool) -> Path:
        clean = Path(relative_path)
        if clean.is_absolute() or ".." in clean.parts:
            raise PermissionDenied(f"unsafe plugin data path: {relative_path}")
        base = (self.data_dir / metadata.name / "data").resolve()
        raw_target = base / clean
        existing_parent = self._existing_parent_without_links(raw_target, base)
        if raw_target.exists() or raw_target.is_symlink():
            if raw_target.is_symlink():
                raise PermissionDenied(f"plugin data path uses a symlink: {relative_path}")
            target = raw_target.resolve()
            self._reject_hardlink(raw_target, relative_path)
        else:
            if not for_write:
                raise FileNotFoundError(raw_target)
            target = (existing_parent / raw_target.relative_to(existing_parent)).resolve()
        if base != target and base not in target.parents:
            raise PermissionDenied(f"path escapes plugin data directory: {relative_path}")
        return target

    def _existing_parent_without_links(self, path: Path, base: Path) -> Path:
        current = base
        current.mkdir(parents=True, exist_ok=True)
        relative_parts = path.relative_to(base).parts
        parent_parts = relative_parts[:-1]
        for part in parent_parts:
            current = current / part
            if current.is_symlink():
                raise PermissionDenied(f"plugin data path uses a symlink: {path.relative_to(base).as_posix()}")
            if current.exists() and not current.is_dir():
                raise PermissionDenied(f"plugin data path parent is not a directory: {path.relative_to(base).as_posix()}")
        return current

    def _reject_hardlink(self, path: Path, relative_path: str) -> None:
        try:
            stat_result = path.stat()
        except FileNotFoundError:
            return
        if getattr(stat_result, "st_nlink", 1) > 1:
            raise PermissionDenied(f"plugin data path uses a hardlink: {relative_path}")

    def _write_plugin_file_without_following_links(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.is_symlink():
                raise PermissionDenied(f"plugin data path uses a symlink: {path.name}")
            self._reject_hardlink(path, path.name)
            path.unlink()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise

    def _as_installed(self, plugin: InstalledPlugin | PluginMetadata) -> InstalledPlugin:
        if isinstance(plugin, InstalledPlugin):
            return plugin
        return InstalledPlugin(
            metadata=plugin,
            path=str(self.data_dir / plugin.name),
            status=PluginStatus.ENABLED,
            granted_permissions=plugin.permissions,
        )

    def _audited_response(
        self,
        response: dict[str, Any],
        *,
        event: str,
        request_id: str,
        plugin: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        status = str(response.get("status", "unknown"))
        audit_details = dict(details or {})
        installed = self.plugins.get(plugin)
        if installed is None:
            sandbox = self.active_sandboxes.get(plugin)
            if sandbox is not None:
                installed = sandbox.installed_plugin
        if installed is not None:
            audit_details.setdefault("version", installed.metadata.version)
        if status != "success" and "error" in response:
            audit_details["error"] = str(response["error"])
        self.audit_logger.record(
            event,
            "success" if status == "success" else "error",
            request_id=request_id,
            plugin=plugin,
            action=action,
            details=audit_details,
        )
        response.setdefault("request_id", request_id)
        return response

    def _audit_network_decision(
        self,
        metadata: PluginMetadata,
        *,
        request_id: str,
        url: str,
        method: str,
        resolved_ips: list[str],
        decision: str,
        reason: str,
    ) -> None:
        self.audit_logger.record(
            "plugin.network_decision",
            "success" if decision == "allow" else "error",
            request_id=request_id,
            plugin=metadata.name,
            action=PermissionName.NETWORK_OUTBOUND.value,
            details={
                "plugin_id": metadata.name,
                "url": url,
                "method": method,
                "resolved_ips": resolved_ips,
                "decision": decision,
                "reason": reason,
                "request_id": request_id,
            },
        )

    def _network_denial_reason(self, exc: Exception) -> str:
        if isinstance(exc, PermissionDenied):
            message = str(exc)
            normalized = re.sub(r"[^a-z0-9]+", "_", message.lower()).strip("_")
            return normalized[:160] or "permission_denied"
        return type(exc).__name__


class GatewayClient:
    """Client injected into plugin calls so plugins can request approved capabilities."""

    def __init__(self, gateway: PluginGateway, metadata: PluginMetadata, request_id: str | None = None):
        self._gateway = gateway
        self._metadata = metadata
        self._request_id = request_id

    def set_request_id(self, request_id: str | None) -> None:
        self._request_id = request_id

    def read_memory(self, key: str) -> Any:
        return self._gateway.read_memory(self._metadata, key, request_id=self._request_id)

    def write_memory(self, key: str, value: Any) -> None:
        self._gateway.write_memory(self._metadata, key, value, request_id=self._request_id)

    def read_config(self, key: str) -> Any:
        return self._gateway.read_config(self._metadata, key, request_id=self._request_id)

    def network_request(self, url: str, method: str = "GET", **kwargs: Any) -> Any:
        return self._gateway.network_request(
            self._metadata,
            {"url": url, "method": method, **kwargs},
            request_id=self._request_id,
        )

    def read_file(self, path: str) -> str:
        return self._gateway.read_plugin_file(self._metadata, path, request_id=self._request_id)

    def write_file(self, path: str, content: str) -> None:
        self._gateway.write_plugin_file(self._metadata, path, content, request_id=self._request_id)

    def publish_event(self, event: str, data: Any = None) -> list[Any]:
        return self._gateway.event_bus.publish(event, data, source=self._metadata.name)

    def send_output(
        self,
        content: Any,
        channel: str = "default",
        content_type: str = "text/plain",
    ) -> dict[str, Any]:
        return self._gateway.send_output(
            self._metadata,
            {
                "content": content,
                "channel": channel,
                "content_type": content_type,
            },
            request_id=self._request_id,
        )


global_gateway = PluginGateway()
