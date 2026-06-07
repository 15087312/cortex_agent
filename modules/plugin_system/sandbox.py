from __future__ import annotations

import ast
import base64
import builtins
import importlib
import json
import multiprocessing
import os
import pickle
import queue
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from multiprocessing.connection import Connection

from .dependency import DependencyManager
from .gateway import GatewayClient, PluginGateway, global_gateway
from .models import (
    DANGEROUS_CALLS,
    DANGEROUS_IMPORTS,
    InstalledPlugin,
    PermissionName,
    PluginMetadata,
    PluginStatus,
    RunMode,
    TrustLevel,
)
from .sandbox_backend import SandboxBackend, create_sandbox_backend


class SandboxViolation(PermissionError):
    pass


class SandboxStartupError(RuntimeError):
    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class SandboxProtocolError(RuntimeError):
    pass


MAX_IPC_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_IPC_STRING_FIELD_CHARS = 4096
ALLOWED_CHILD_MESSAGE_KINDS = {"lifecycle", "gateway_request", "result", "protocol_error"}
ALLOWED_GATEWAY_REQUEST_TYPES = {
    "memory.read",
    "memory.write",
    "config.read",
    "network.outbound",
    "fs.read",
    "fs.write",
    "event.publish",
    "output.send",
}

ALLOWED_DYNAMIC_IMPORT_ROOTS = {
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "functools",
    "itertools",
    "json",
    "math",
    "re",
    "statistics",
    "string",
    "time",
    "typing",
}

BLOCKED_HOST_IMPORT_ROOTS = {"modules"}
_ENTRY_LOAD_LOCK = threading.RLock()


@dataclass
class SandboxReport:
    plugin: str
    run_mode: RunMode
    process_id: int | None
    os_limits: dict[str, Any]
    static_scan_passed: bool


class PluginStaticAnalyzer(ast.NodeVisitor):
    """Reject imports and calls that are outside the supported sandbox profile."""

    def __init__(self, metadata: PluginMetadata):
        self.metadata = metadata
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_import(alias.name, node.lineno)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self._check_import(node.module, node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        call_name = self._call_name(node.func)
        if call_name in DANGEROUS_CALLS:
            self.violations.append(f"line {node.lineno}: call to {call_name} is not allowed")
        if call_name in {"os.system", "subprocess.run", "subprocess.Popen", "subprocess.call"}:
            self.violations.append(f"line {node.lineno}: process execution is not allowed")
        self.generic_visit(node)

    def _check_import(self, import_name: str, lineno: int) -> None:
        root = import_name.split(".", 1)[0]
        if root in BLOCKED_HOST_IMPORT_ROOTS:
            self.violations.append(f"line {lineno}: import of host module {import_name} is not allowed")
            return
        if root in {"requests", "httpx", "urllib", "socket", "http", "aiohttp"}:
            if self.metadata.has_permission(PermissionName.NETWORK_OUTBOUND):
                self.violations.append(
                    f"line {lineno}: direct network imports are not allowed; use the gateway API"
                )
                return
        if root in DANGEROUS_IMPORTS:
            self.violations.append(f"line {lineno}: import of {root} is not allowed")

    def _call_name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            parent = self._call_name(node.value)
            return f"{parent}.{node.attr}" if parent else node.attr
        return ""


def scan_plugin_source(plugin_dir: str | Path, metadata: PluginMetadata) -> list[str]:
    src_dir = Path(plugin_dir) / "src"
    analyzer = PluginStaticAnalyzer(metadata)
    for file_path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        except SyntaxError as exc:
            analyzer.violations.append(f"{file_path}: syntax error: {exc}")
            continue
        analyzer.visit(tree)
    return analyzer.violations


def _apply_os_resource_limits(
    memory_mb: int,
    cpu_seconds: int,
    *,
    max_processes: int = 32,
    max_open_files: int = 64,
    max_file_size_mb: int = 16,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "memory_mb": memory_mb,
        "cpu_seconds": cpu_seconds,
        "max_processes": max_processes,
        "max_open_files": max_open_files,
        "max_file_size_mb": max_file_size_mb,
        "resource_module": False,
        "platform": sys.platform,
        "warnings": [],
    }
    try:
        import resource

        max_bytes = memory_mb * 1024 * 1024
        file_size_bytes = max_file_size_mb * 1024 * 1024
        _set_resource_limit(report, resource, "RLIMIT_AS", max_bytes)
        _set_resource_limit(report, resource, "RLIMIT_CPU", cpu_seconds)
        _set_resource_limit(report, resource, "RLIMIT_NOFILE", max_open_files)
        _set_resource_limit(report, resource, "RLIMIT_NPROC", max_processes)
        _set_resource_limit(report, resource, "RLIMIT_FSIZE", file_size_bytes)
        report["resource_module"] = True
    except Exception as exc:
        report["warnings"].append(f"OS resource limits unavailable: {exc}")
    return report


def _set_resource_limit(report: dict[str, Any], resource: Any, name: str, value: int) -> None:
    limit = getattr(resource, name, None)
    if limit is None:
        report["warnings"].append(f"{name} is unavailable")
        return
    try:
        hard_limit = resource.getrlimit(limit)[1]
        effective = value if hard_limit < 0 else min(value, hard_limit)
        resource.setrlimit(limit, (effective, effective))
        report.setdefault("resource_limits", {})[name] = effective
    except Exception as exc:
        report["warnings"].append(f"{name} unavailable: {exc}")


def _json_ipc_payload(payload: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(payload, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise SandboxProtocolError(f"IPC payload is not JSON serializable: {exc}") from exc
    if len(encoded.encode("utf-8")) > MAX_IPC_MESSAGE_BYTES:
        raise SandboxProtocolError(f"IPC message exceeds {MAX_IPC_MESSAGE_BYTES} bytes")
    return encoded


def _validate_child_message(message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        raise SandboxProtocolError("child IPC message must be an object")
    kind = message.get("kind")
    if not isinstance(kind, str) or not kind:
        raise SandboxProtocolError("child IPC message is missing kind")
    if len(kind) > 64:
        raise SandboxProtocolError("child IPC message kind is too long")
    if kind not in ALLOWED_CHILD_MESSAGE_KINDS:
        raise SandboxProtocolError(f"unsupported child IPC message kind: {kind}")
    if kind == "gateway_request":
        _validate_optional_string_field(message, "plugin")
        _validate_optional_string_field(message, "request_id")
        request_type = message.get("request_type")
        if not isinstance(request_type, str) or request_type not in ALLOWED_GATEWAY_REQUEST_TYPES:
            raise SandboxProtocolError(f"unsupported gateway request type: {request_type}")
        if not isinstance(message.get("payload"), dict):
            raise SandboxProtocolError("gateway request payload must be an object")
    elif kind == "result":
        status = message.get("status")
        if status not in {"success", "error"}:
            raise SandboxProtocolError("result message status must be success or error")
        if status == "error":
            _validate_optional_string_field(message, "error")
            _validate_optional_string_field(message, "traceback")
    elif kind == "lifecycle":
        status = message.get("status")
        if status not in {"ready", "stopped", "error"}:
            raise SandboxProtocolError("lifecycle message status must be ready, stopped, or error")
        if "os_limits" in message and not isinstance(message["os_limits"], dict):
            raise SandboxProtocolError("lifecycle os_limits must be an object")
        _validate_optional_string_field(message, "error")
        _validate_optional_string_field(message, "traceback")
    elif kind == "protocol_error":
        status = message.get("status")
        if status != "error":
            raise SandboxProtocolError("protocol_error message status must be error")
        _validate_optional_string_field(message, "error")
    return message


def _validate_optional_string_field(message: dict[str, Any], field: str) -> None:
    value = message.get(field)
    if value is None:
        return
    if not isinstance(value, str):
        raise SandboxProtocolError(f"IPC field {field} must be a string")
    if len(value) > MAX_IPC_STRING_FIELD_CHARS:
        raise SandboxProtocolError(f"IPC field {field} is too long")


def _read_text_excerpt(path: Path | None, *, max_chars: int = 4096) -> str:
    if path is None or not path.exists():
        return ""
    try:
        handle = path.open("r", encoding="utf-8", errors="replace")
        try:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - max_chars))
            return handle.read(max_chars)
        finally:
            handle.close()
    except Exception:
        return ""


def _classify_stdio_failure(exc: BaseException | None, stderr_excerpt: str) -> str | None:
    if exc is None and not stderr_excerpt:
        return None
    lowered = stderr_excerpt.lower()
    if isinstance(exc, subprocess.TimeoutExpired):
        return "Timeout"
    if isinstance(exc, PermissionError) or "permission denied" in lowered:
        return "PermissionDenied"
    if isinstance(exc, json.JSONDecodeError):
        return "WorkerJsonDecodeError"
    if isinstance(exc, SandboxProtocolError):
        return "WorkerJsonDecodeError"
    if isinstance(exc, queue.Empty):
        if "modulenotfounderror" in lowered or "importerror" in lowered:
            return "WorkerImportError"
        if "bwrap:" in lowered or "bubblewrap" in lowered:
            return "BwrapLaunchError"
        if "no such file or directory" in lowered or "cannot open" in lowered:
            return "RuntimeMountError"
        return "WorkerNoOutput"
    if "modulenotfounderror" in lowered or "importerror" in lowered:
        return "WorkerImportError"
    if "bwrap:" in lowered or "bubblewrap" in lowered:
        return "BwrapLaunchError"
    if "no such file or directory" in lowered or "cannot open" in lowered:
        return "RuntimeMountError"
    return type(exc).__name__ if exc is not None else "WorkerNoOutput"


class _ChildGatewayProxy:
    def __init__(self, conn: Connection, plugin_name: str):
        self._conn = conn
        self._plugin_name = plugin_name
        self._request_id: str | None = None

    def set_request_id(self, request_id: str | None) -> None:
        self._request_id = request_id

    def request(self, request_type: str, payload: dict[str, Any]) -> Any:
        self._conn.send(
            {
                "kind": "gateway_request",
                "plugin": self._plugin_name,
                "request_type": request_type,
                "request_id": self._request_id,
                "payload": payload,
            }
        )
        response = self._conn.recv()
        if response.get("status") != "success":
            raise SandboxViolation(response.get("error", "gateway request denied"))
        return response.get("data")

    def read_memory(self, key: str) -> Any:
        return self.request("memory.read", {"key": key})

    def write_memory(self, key: str, value: Any) -> None:
        self.request("memory.write", {"key": key, "value": value})

    def read_config(self, key: str) -> Any:
        return self.request("config.read", {"key": key})

    def network_request(self, url: str, method: str = "GET", **kwargs: Any) -> Any:
        payload = {"url": url, "method": method, **kwargs}
        return self.request("network.outbound", payload)

    def read_file(self, path: str) -> str:
        return self.request("fs.read", {"path": path})

    def write_file(self, path: str, content: str) -> None:
        self.request("fs.write", {"path": path, "content": content})

    def publish_event(self, event: str, data: Any = None) -> Any:
        return self.request("event.publish", {"event": event, "data": data})

    def send_output(
        self,
        content: Any,
        channel: str = "default",
        content_type: str = "text/plain",
    ) -> Any:
        return self.request(
            "output.send",
            {
                "content": content,
                "channel": channel,
                "content_type": content_type,
            },
        )


def _install_child_runtime_guards(metadata: PluginMetadata, plugin_dir: Path, granted_permissions: set[str] | None = None) -> None:
    original_open = builtins.open
    data_dir = (plugin_dir / "data").resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    allowed_permissions = granted_permissions if granted_permissions is not None else metadata.requested_permissions

    def reject_link_path(target: Path, original: Any) -> None:
        if target.is_symlink():
            raise SandboxViolation(f"file access through symlink is not allowed: {original}")
        if target.exists():
            try:
                if getattr(target.stat(), "st_nlink", 1) > 1:
                    raise SandboxViolation(f"file access through hardlink is not allowed: {original}")
            except FileNotFoundError:
                pass

    def reject_link_parents(target: Path, original: Any) -> None:
        current = data_dir
        try:
            relative_parts = target.relative_to(data_dir).parts[:-1]
        except ValueError:
            raise SandboxViolation(f"file access outside plugin data directory: {original}") from None
        for part in relative_parts:
            current = current / part
            if current.is_symlink():
                raise SandboxViolation(f"file access through symlink is not allowed: {original}")
            if current.exists() and not current.is_dir():
                raise SandboxViolation(f"file parent is not a directory: {original}")

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        path = Path(file)
        raw_target = path if path.is_absolute() else data_dir / path
        reject_link_parents(raw_target, file)
        if raw_target.is_symlink():
            raise SandboxViolation(f"file access through symlink is not allowed: {file}")
        target = raw_target.resolve()
        if data_dir != target and data_dir not in target.parents:
            raise SandboxViolation(f"file access outside plugin data directory: {file}")
        reject_link_path(raw_target, file)
        if any(flag in mode for flag in ["w", "a", "x", "+"]):
            if PermissionName.FS_WRITE.value not in allowed_permissions:
                raise SandboxViolation("plugin does not have fs.write")
            target.parent.mkdir(parents=True, exist_ok=True)
        else:
            if PermissionName.FS_READ.value not in allowed_permissions:
                raise SandboxViolation("plugin does not have fs.read")
        return original_open(target, mode, *args, **kwargs)

    def blocked_eval(*_: Any, **__: Any) -> Any:
        raise SandboxViolation("eval is blocked in plugin sandbox")

    def blocked_input(*_: Any, **__: Any) -> Any:
        raise SandboxViolation("input is blocked in plugin sandbox")

    builtins.open = guarded_open
    builtins.eval = blocked_eval
    builtins.input = blocked_input


def _install_post_load_runtime_guards() -> None:
    original_import = builtins.__import__

    def blocked_exec(*_: Any, **__: Any) -> Any:
        raise SandboxViolation("exec is blocked in plugin sandbox")

    def blocked_compile(*_: Any, **__: Any) -> Any:
        raise SandboxViolation("compile is blocked in plugin sandbox")

    def guarded_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        root = name.split(".", 1)[0]
        if root == "src":
            return original_import(name, globals, locals, fromlist, level)
        if root in DANGEROUS_IMPORTS or root not in ALLOWED_DYNAMIC_IMPORT_ROOTS:
            raise SandboxViolation(f"dynamic import of {root} is not allowed")
        return original_import(name, globals, locals, fromlist, level)

    builtins.exec = blocked_exec
    builtins.compile = blocked_compile
    builtins.__import__ = guarded_import


def _load_entry(plugin_dir: Path, entry: str) -> Any:
    module_name, function_name = entry.split(":", 1)
    module_root = module_name.split(".", 1)[0]
    plugin_path = str(plugin_dir)

    def guarded_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        root = name.split(".", 1)[0]
        if root in BLOCKED_HOST_IMPORT_ROOTS:
            raise SandboxViolation(f"host module import is not allowed: {name}")
        return original_import(name, globals, locals, fromlist, level)

    with _ENTRY_LOAD_LOCK:
        original_import = builtins.__import__
        original_sys_path = list(sys.path)
        replaced_modules = {
            name: sys.modules[name]
            for name in list(sys.modules)
            if name == module_root or name.startswith(f"{module_root}.")
        }
        for name in replaced_modules:
            sys.modules.pop(name, None)
        if plugin_path in sys.path:
            sys.path.remove(plugin_path)
        sys.path.insert(0, plugin_path)
        builtins.__import__ = guarded_import
        try:
            module = importlib.import_module(module_name)
            return getattr(module, function_name)
        finally:
            for name in list(sys.modules):
                if name == module_root or name.startswith(f"{module_root}."):
                    sys.modules.pop(name, None)
            sys.modules.update(replaced_modules)
            sys.path[:] = original_sys_path
            builtins.__import__ = original_import


def _call_entry(function: Any, args: dict[str, Any], api: Any) -> Any:
    try:
        return function(args, api)
    except TypeError as exc:
        message = str(exc)
        if "positional" in message or "argument" in message:
            return function(args)
        raise


def _call_event_entry(function: Any, event: dict[str, Any], api: Any) -> Any:
    try:
        return function(event, api)
    except TypeError as exc:
        message = str(exc)
        if "positional" in message or "argument" in message:
            return function(event)
        raise


def _call_middleware_entry(function: Any, context: dict[str, Any], api: Any) -> Any:
    try:
        return function(context, api)
    except TypeError as exc:
        message = str(exc)
        if "positional" in message or "argument" in message:
            return function(context)
        raise


def _call_memory_provider_entry(function: Any, request: dict[str, Any], api: Any) -> Any:
    try:
        return function(request, api)
    except TypeError as exc:
        message = str(exc)
        if "positional" in message or "argument" in message:
            return function(request)
        raise


def _isolated_worker(
    child_conn: Connection,
    metadata_payload: dict[str, Any],
    granted_permissions_payload: list[str],
    plugin_dir: str,
) -> None:
    metadata = PluginMetadata(**metadata_payload)
    granted_permissions = set(granted_permissions_payload)
    plugin_path = Path(plugin_dir).resolve()
    os_report = _apply_os_resource_limits(metadata.runtime.memory_mb, metadata.runtime.cpu_seconds)
    try:
        _install_child_runtime_guards(metadata, plugin_path, granted_permissions)
        entries = metadata.tool_entries()
        functions = {tool_name: _load_entry(plugin_path, entry) for tool_name, entry in entries.items()}
        middleware_entries = metadata.middleware_entries()
        middleware_functions = {
            middleware_name: _load_entry(plugin_path, entry)
            for middleware_name, entry in middleware_entries.items()
        }
        memory_provider_entries = metadata.memory_provider_entries()
        memory_provider_functions = {
            provider_name: _load_entry(plugin_path, entry)
            for provider_name, entry in memory_provider_entries.items()
        }
        event_entries = metadata.event_listener_entries()
        event_functions = {
            entry: _load_entry(plugin_path, entry)
            for entries_for_event in event_entries.values()
            for entry in entries_for_event
        }
        _install_post_load_runtime_guards()
        api = _ChildGatewayProxy(child_conn, metadata.name)
        child_conn.send({"kind": "lifecycle", "status": "ready", "os_limits": os_report})

        while True:
            message = child_conn.recv()
            if message.get("action") == "shutdown":
                child_conn.send({"kind": "lifecycle", "status": "stopped"})
                break
            if message.get("action") != "execute_tool":
                if message.get("action") == "run_middleware":
                    payload = message.get("payload", {})
                    middleware_name = payload.get("middleware_name")
                    if middleware_name not in middleware_functions:
                        child_conn.send(
                            {
                                "kind": "result",
                                "status": "error",
                                "error": f"unknown middleware: {middleware_name}",
                            }
                        )
                        continue
                    try:
                        api.set_request_id(message.get("request_id"))
                        result = _call_middleware_entry(
                            middleware_functions[middleware_name],
                            payload.get("context", {}),
                            api,
                        )
                        child_conn.send({"kind": "result", "status": "success", "data": result})
                    except Exception as exc:
                        child_conn.send(
                            {
                                "kind": "result",
                                "status": "error",
                                "error": str(exc),
                                "traceback": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    continue
                if message.get("action") == "call_memory_provider":
                    payload = message.get("payload", {})
                    provider_name = payload.get("provider_name")
                    if provider_name not in memory_provider_functions:
                        child_conn.send(
                            {
                                "kind": "result",
                                "status": "error",
                                "error": f"unknown memory provider: {provider_name}",
                            }
                        )
                        continue
                    try:
                        api.set_request_id(message.get("request_id"))
                        request = {
                            "operation": payload.get("operation"),
                            "payload": payload.get("payload", {}),
                        }
                        result = _call_memory_provider_entry(
                            memory_provider_functions[provider_name],
                            request,
                            api,
                        )
                        child_conn.send({"kind": "result", "status": "success", "data": result})
                    except Exception as exc:
                        child_conn.send(
                            {
                                "kind": "result",
                                "status": "error",
                                "error": str(exc),
                                "traceback": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    continue
                if message.get("action") == "handle_event":
                    payload = message.get("payload", {})
                    event_name = payload.get("event", {}).get("name")
                    listeners = event_entries.get(event_name, [])
                    results = []
                    try:
                        api.set_request_id(message.get("request_id"))
                        for entry in listeners:
                            results.append(_call_event_entry(event_functions[entry], payload.get("event", {}), api))
                        child_conn.send({"kind": "result", "status": "success", "data": results})
                    except Exception as exc:
                        child_conn.send(
                            {
                                "kind": "result",
                                "status": "error",
                                "error": str(exc),
                                "traceback": f"{type(exc).__name__}: {exc}",
                            }
                        )
                    continue
                child_conn.send({"kind": "result", "status": "error", "error": "unknown sandbox action"})
                continue
            payload = message.get("payload", {})
            tool_name = payload.get("tool_name")
            if tool_name not in functions:
                child_conn.send({"kind": "result", "status": "error", "error": f"unknown tool: {tool_name}"})
                continue
            try:
                api.set_request_id(message.get("request_id"))
                result = _call_entry(functions[tool_name], payload.get("args", {}), api)
                child_conn.send({"kind": "result", "status": "success", "data": result})
            except Exception as exc:
                child_conn.send(
                    {
                        "kind": "result",
                        "status": "error",
                        "error": str(exc),
                        "traceback": f"{type(exc).__name__}: {exc}",
                    }
                )
    except BaseException as exc:
        try:
            child_conn.send(
                {
                    "kind": "lifecycle",
                    "status": "error",
                    "error": str(exc),
                    "traceback": f"{type(exc).__name__}: {exc}",
                    "os_limits": os_report,
                }
            )
        except Exception:
            pass
    finally:
        child_conn.close()


class SandboxManager:
    """Run a plugin in the selected runtime and enforce timeout/capability boundaries."""

    def __init__(
        self,
        plugin: PluginMetadata | InstalledPlugin,
        plugins_dir: str | Path = "data/plugins",
        gateway: PluginGateway | None = None,
        sandbox_backend: str = "auto",
        require_enforced_sandbox: bool = False,
    ):
        self.installed_plugin = self._as_installed(plugin, plugins_dir)
        self.meta = self.installed_plugin.metadata
        self.plugins_dir = Path(plugins_dir).resolve()
        self.gateway = gateway or global_gateway
        self.process: multiprocessing.Process | subprocess.Popen[str] | None = None
        self.parent_conn: Connection | None = None
        self.plugin_dir = self.plugins_dir / self.meta.name
        self.run_mode = self.meta.effective_run_mode
        self.static_violations: list[str] = []
        self.os_limits: dict[str, Any] = {}
        self._in_process_functions: dict[str, Any] = {}
        self._in_process_middleware_functions: dict[str, Any] = {}
        self._in_process_memory_provider_functions: dict[str, Any] = {}
        self._in_process_event_functions: dict[str, Any] = {}
        self.transport: str | None = None
        self._stdio_queue: queue.Queue[dict[str, Any]] | None = None
        self._stdio_reader_thread: threading.Thread | None = None
        self._stdio_stdout_path: Path | None = None
        self._stdio_stderr_path: Path | None = None
        self._stdio_stdout_handle: Any = None
        self._stdio_stderr_handle: Any = None
        self._stdio_command: list[str] | None = None
        self._stdio_env_keys: list[str] | None = None
        self._sandbox_backend_name = sandbox_backend
        self._sandbox_backend: SandboxBackend | None = None
        self.require_enforced_sandbox = require_enforced_sandbox
        self.dependency_manager = DependencyManager(self.plugins_dir)
        self.runtime_python = self.dependency_manager.runtime_python(self.plugin_dir, self.meta)

    def start(self) -> bool:
        if self.installed_plugin.status != PluginStatus.ENABLED:
            raise SandboxViolation(f"plugin is not enabled: {self.meta.name}")
        if self.run_mode == RunMode.IN_PROCESS:
            return self._start_in_process()
        return self._start_sub_process()

    def execute_with_timeout(
        self,
        action: str,
        payload: dict[str, Any],
        timeout: float = 3.0,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if self.run_mode == RunMode.IN_PROCESS:
            return self._execute_in_process(action, payload, request_id)
        return self._execute_sub_process(action, payload, timeout, request_id)

    def stop(self) -> None:
        if self.parent_conn:
            try:
                self.parent_conn.send({"action": "shutdown"})
                if self.parent_conn.poll(0.2):
                    self.parent_conn.recv()
            except Exception:
                pass
            try:
                self.parent_conn.close()
            except Exception:
                pass
        if self.process:
            backend_terminated = False
            if isinstance(self.process, subprocess.Popen):
                if self.process.poll() is None:
                    try:
                        self._stdio_write({"action": "shutdown"})
                    except Exception:
                        pass
                    try:
                        self.process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        backend_terminated = self._terminate_with_backend()
                        if not backend_terminated:
                            self.process.terminate()
                        try:
                            self.process.wait(timeout=0.5)
                        except subprocess.TimeoutExpired:
                            if not backend_terminated:
                                self.process.kill()
                self._close_stdio_pipes()
            elif self.process.is_alive():
                backend_terminated = self._terminate_with_backend()
                if not backend_terminated:
                    self.process.terminate()
                self.process.join(timeout=0.5)
                if self.process.is_alive():
                    if not backend_terminated:
                        self.process.kill()
        self._close_sandbox_backend()
        self.process = None
        self.parent_conn = None
        self._in_process_functions.clear()
        self._in_process_middleware_functions.clear()
        self._in_process_memory_provider_functions.clear()
        self._in_process_event_functions.clear()
        self.transport = None

    def report(self) -> SandboxReport:
        return SandboxReport(
            plugin=self.meta.name,
            run_mode=self.run_mode,
            process_id=self.process.pid if self.process else None,
            os_limits=self.os_limits,
            static_scan_passed=not self.static_violations,
        )

    def _start_in_process(self) -> bool:
        self._scan_or_raise()
        self.gateway.register_plugin(self.installed_plugin)
        self._in_process_functions = {
            tool_name: _load_entry(self.plugin_dir, entry)
            for tool_name, entry in self.meta.tool_entries().items()
        }
        self._in_process_middleware_functions = {
            middleware_name: _load_entry(self.plugin_dir, entry)
            for middleware_name, entry in self.meta.middleware_entries().items()
        }
        self._in_process_memory_provider_functions = {
            provider_name: _load_entry(self.plugin_dir, entry)
            for provider_name, entry in self.meta.memory_provider_entries().items()
        }
        self._in_process_event_functions = {
            entry: _load_entry(self.plugin_dir, entry)
            for entries in self.meta.event_listener_entries().values()
            for entry in entries
        }
        return True

    def _start_sub_process(self) -> bool:
        if isinstance(self.process, multiprocessing.Process) and self.process.is_alive():
            return True
        self._scan_or_raise()
        self.gateway.register_plugin(self.installed_plugin)
        if self.runtime_python != sys.executable:
            return self._start_stdio_sub_process("plugin dependency environment")
        if self._backend_requires_subprocess_launcher():
            return self._start_stdio_sub_process(f"{self._sandbox_backend_name} launcher")
        try:
            parent_conn, child_conn = multiprocessing.Pipe()
            self.parent_conn = cast(Connection, parent_conn)
        except PermissionError as exc:
            return self._start_stdio_sub_process(str(exc))
        self.process = multiprocessing.Process(
            target=_isolated_worker,
            args=(
                child_conn,
                self.meta.model_dump(mode="json"),
                sorted(self.installed_plugin.granted_permission_names),
                str(self.plugin_dir),
            ),
            name=f"PluginSandbox-{self.meta.name}",
            daemon=True,
        )
        try:
            self.process.start()
        except PermissionError as exc:
            self.parent_conn = None
            self.process = None
            return self._start_stdio_sub_process(str(exc))
        try:
            self._attach_new_sandbox_backend()
        except Exception:
            self.stop()
            raise
        self.transport = "pipe"
        if self.parent_conn is None or not self.parent_conn.poll(timeout=5.0):
            self.stop()
            return False
        message = self._pipe_read()
        self._merge_os_limits(message.get("os_limits", {}))
        if message.get("status") == "ready":
            return True
        self.stop()
        raise SandboxStartupError(message.get("error", "plugin sandbox failed to start"))

    def _start_stdio_sub_process(self, fallback_reason: str) -> bool:
        payload_json = json.dumps(
            {
                "metadata": self.meta.model_dump(mode="json"),
                "granted_permissions": sorted(self.installed_plugin.granted_permission_names),
            },
            separators=(",", ":"),
        )
        payload_blob = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
        command = [
            self.runtime_python,
            "-m",
            "modules.plugin_system.sandbox_stdio_worker",
            str(self.plugin_dir),
            payload_blob,
        ]
        backend = self._create_sandbox_backend()
        try:
            command = backend.prepare_subprocess(
                command,
                plugin_dir=self.plugin_dir,
                project_root=Path(__file__).resolve().parents[2],
            )
        except Exception as exc:
            self._sandbox_backend = backend
            self._record_backend_report(backend.report)
            if self._requires_enforced_backend():
                raise SandboxStartupError(
                    f"strict sandbox could not prepare backend {backend.report.name}: {exc}"
                ) from exc
            backend.report.warnings.append(str(exc))
        data_dir = self.plugin_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._stdio_stdout_path = data_dir / "stdio-worker.stdout.log"
        self._stdio_stderr_path = data_dir / "stdio-worker.stderr.log"
        self._stdio_stdout_handle = self._stdio_stdout_path.open("w+", encoding="utf-8")
        self._stdio_stderr_handle = self._stdio_stderr_path.open("w+", encoding="utf-8")
        env = self._subprocess_env()
        self._stdio_command = list(command)
        self._stdio_env_keys = sorted(env.keys())
        self.os_limits["stdio_diagnostics"] = self._stdio_diagnostics(
            error_type=None,
            error=None,
            command=command,
            env=env,
            worker_started=False,
            json_result_received=False,
        )
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self._stdio_stderr_handle,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
        except Exception as exc:
            self._sandbox_backend = backend
            self._record_backend_report(backend.report)
            diagnostics = self._stdio_diagnostics(
                error_type=_classify_stdio_failure(exc, ""),
                error=str(exc),
                command=command,
                env=env,
                worker_started=False,
                json_result_received=False,
            )
            self.os_limits["stdio_diagnostics"] = diagnostics
            self._close_stdio_pipes()
            raise SandboxStartupError(f"plugin stdio sandbox launch failed: {exc}", diagnostics) from exc
        try:
            self._attach_sandbox_backend(backend)
        except Exception:
            self.stop()
            raise
        self.transport = "stdio"
        self._stdio_queue = queue.Queue()
        self._stdio_reader_thread = threading.Thread(
            target=self._stdio_reader,
            name=f"PluginSandboxReader-{self.meta.name}",
            daemon=True,
        )
        self._stdio_reader_thread.start()
        try:
            message = self._stdio_read(timeout=5.0)
        except queue.Empty as exc:
            diagnostics = self._stdio_diagnostics(
                error_type=_classify_stdio_failure(exc, self._read_stdio_stderr_excerpt()),
                error="plugin stdio worker did not emit a startup message",
                command=command,
                env=env,
                worker_started=False,
                json_result_received=False,
            )
            self.os_limits["stdio_diagnostics"] = diagnostics
            self.stop()
            raise SandboxStartupError("plugin stdio worker produced no startup message", diagnostics) from exc
        except SandboxProtocolError as exc:
            diagnostics = self._stdio_diagnostics(
                error_type="WorkerJsonDecodeError",
                error=str(exc),
                command=command,
                env=env,
                worker_started=True,
                json_result_received=False,
            )
            self.os_limits["stdio_diagnostics"] = diagnostics
            self.stop()
            raise SandboxStartupError(f"plugin stdio worker sent invalid startup JSON: {exc}", diagnostics) from exc
        self._merge_os_limits(message.get("os_limits", {}))
        self.os_limits["transport_fallback"] = fallback_reason
        self.os_limits["runtime_python"] = self.runtime_python
        if message.get("status") == "ready":
            self.os_limits["stdio_diagnostics"] = self._stdio_diagnostics(
                error_type=None,
                error=None,
                command=command,
                env=env,
                worker_started=True,
                json_result_received=True,
            )
            return True
        diagnostics = self._stdio_diagnostics(
            error_type=str(message.get("error_type") or "WorkerStartupError"),
            error=str(message.get("error") or "plugin stdio sandbox failed to start"),
            command=command,
            env=env,
            worker_started=True,
            json_result_received=True,
        )
        self.os_limits["stdio_diagnostics"] = diagnostics
        self.stop()
        raise SandboxStartupError(message.get("error", "plugin stdio sandbox failed to start"), diagnostics)

    def _execute_in_process(self, action: str, payload: dict[str, Any], request_id: str | None) -> dict[str, Any]:
        if action == "run_middleware":
            return self._run_middleware_in_process(payload, request_id)
        if action == "call_memory_provider":
            return self._call_memory_provider_in_process(payload, request_id)
        if action == "handle_event":
            return self._handle_event_in_process(payload, request_id)
        if action != "execute_tool":
            return {"status": "error", "error": "unknown sandbox action"}
        tool_name = str(payload.get("tool_name", ""))
        function = self._in_process_functions.get(tool_name)
        if not function:
            return {"status": "error", "error": f"unknown tool: {tool_name}"}
        try:
            result = _call_entry(
                function,
                payload.get("args", {}),
                GatewayClient(self.gateway, self.meta, request_id=request_id),
            )
            return {"status": "success", "data": result}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "traceback": traceback.format_exc(limit=8)}

    def _run_middleware_in_process(self, payload: dict[str, Any], request_id: str | None) -> dict[str, Any]:
        middleware_name = str(payload.get("middleware_name", ""))
        function = self._in_process_middleware_functions.get(middleware_name)
        if not function:
            return {"status": "error", "error": f"unknown middleware: {middleware_name}"}
        try:
            result = _call_middleware_entry(
                function,
                payload.get("context", {}),
                GatewayClient(self.gateway, self.meta, request_id=request_id),
            )
            return {"status": "success", "data": result}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "traceback": traceback.format_exc(limit=8)}

    def _call_memory_provider_in_process(self, payload: dict[str, Any], request_id: str | None) -> dict[str, Any]:
        provider_name = str(payload.get("provider_name", ""))
        function = self._in_process_memory_provider_functions.get(provider_name)
        if not function:
            return {"status": "error", "error": f"unknown memory provider: {provider_name}"}
        try:
            request = {
                "operation": payload.get("operation"),
                "payload": payload.get("payload", {}),
            }
            result = _call_memory_provider_entry(
                function,
                request,
                GatewayClient(self.gateway, self.meta, request_id=request_id),
            )
            return {"status": "success", "data": result}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "traceback": traceback.format_exc(limit=8)}

    def _handle_event_in_process(self, payload: dict[str, Any], request_id: str | None) -> dict[str, Any]:
        event = payload.get("event", {})
        event_name = event.get("name")
        listeners = self.meta.event_listener_entries().get(event_name, [])
        try:
            results = [
                _call_event_entry(
                    self._in_process_event_functions[entry],
                    event,
                    GatewayClient(self.gateway, self.meta, request_id=request_id),
                )
                for entry in listeners
            ]
            return {"status": "success", "data": results}
        except Exception as exc:
            return {"status": "error", "error": str(exc), "traceback": traceback.format_exc(limit=8)}

    def _execute_sub_process(
        self,
        action: str,
        payload: dict[str, Any],
        timeout: float,
        request_id: str | None,
    ) -> dict[str, Any]:
        if self.transport == "stdio":
            return self._execute_stdio_sub_process(action, payload, timeout, request_id)
        if not isinstance(self.process, multiprocessing.Process) or not self.process.is_alive() or not self.parent_conn:
            return {"status": "error", "error": "sandbox is not running"}
        try:
            self._pipe_write({"action": action, "payload": payload, "request_id": request_id})
            while True:
                if not self.parent_conn.poll(timeout):
                    self.stop()
                    return {"status": "error", "error": "plugin execution timed out"}
                message = self._pipe_read()
                if message.get("kind") == "gateway_request":
                    claimed_plugin = str(message.get("plugin", ""))
                    response = self.gateway.handle_sandbox_request(
                        self.meta.name,
                        message.get("request_type", ""),
                        message.get("payload", {}),
                        message.get("request_id"),
                    )
                    if claimed_plugin and claimed_plugin != self.meta.name:
                        self._audit_spoofed_gateway_identity(claimed_plugin, message.get("request_id"))
                        response.setdefault("security_warnings", []).append(
                            f"ignored spoofed gateway plugin identity: {claimed_plugin}"
                        )
                    self._pipe_write(response)
                    continue
                return {key: value for key, value in message.items() if key != "kind"}
        except (EOFError, BrokenPipeError):
            return {"status": "error", "error": "sandbox process terminated unexpectedly"}
        except Exception as exc:
            return {"status": "error", "error": f"sandbox communication error: {exc}"}

    def _execute_stdio_sub_process(
        self,
        action: str,
        payload: dict[str, Any],
        timeout: float,
        request_id: str | None,
    ) -> dict[str, Any]:
        if not isinstance(self.process, subprocess.Popen) or self.process.poll() is not None:
            return {"status": "error", "error": "sandbox is not running"}
        try:
            self._stdio_write({"action": action, "payload": payload, "request_id": request_id})
            while True:
                message = self._stdio_read(timeout=timeout)
                if message.get("kind") == "gateway_request":
                    claimed_plugin = str(message.get("plugin", ""))
                    response = self.gateway.handle_sandbox_request(
                        self.meta.name,
                        message.get("request_type", ""),
                        message.get("payload", {}),
                        message.get("request_id"),
                    )
                    if claimed_plugin and claimed_plugin != self.meta.name:
                        self._audit_spoofed_gateway_identity(claimed_plugin, message.get("request_id"))
                        response.setdefault("security_warnings", []).append(
                            f"ignored spoofed gateway plugin identity: {claimed_plugin}"
                        )
                    self._stdio_write(response)
                    continue
                return {key: value for key, value in message.items() if key != "kind"}
        except queue.Empty:
            diagnostics = self._stdio_diagnostics(
                error_type="Timeout",
                error="plugin execution timed out",
                command=self._stdio_command,
                env_keys=self._stdio_env_keys,
                worker_started=True,
                json_result_received=False,
            )
            self.os_limits["stdio_diagnostics"] = diagnostics
            self.stop()
            return {"status": "error", "error": "plugin execution timed out", "diagnostics": diagnostics}
        except Exception as exc:
            diagnostics = self._stdio_diagnostics(
                error_type=_classify_stdio_failure(exc, self._read_stdio_stderr_excerpt()),
                error=str(exc),
                command=self._stdio_command,
                env_keys=self._stdio_env_keys,
                worker_started=True,
                json_result_received=False,
            )
            self.os_limits["stdio_diagnostics"] = diagnostics
            return {
                "status": "error",
                "error": f"sandbox stdio communication error: {exc}",
                "diagnostics": diagnostics,
            }

    def _stdio_reader(self) -> None:
        if not isinstance(self.process, subprocess.Popen) or self.process.stdout is None:
            return
        while True:
            line = self.process.stdout.readline(MAX_IPC_MESSAGE_BYTES + 1)
            if not line:
                return
            if self._stdio_stdout_handle:
                try:
                    self._stdio_stdout_handle.write(line)
                    self._stdio_stdout_handle.flush()
                except Exception:
                    pass
            if len(line.encode("utf-8")) > MAX_IPC_MESSAGE_BYTES:
                message = {
                    "kind": "protocol_error",
                    "status": "error",
                    "error": f"stdio IPC message exceeds {MAX_IPC_MESSAGE_BYTES} bytes",
                }
                if self._stdio_queue:
                    self._stdio_queue.put(message)
                return
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                message = {"kind": "protocol_error", "status": "error", "error": str(exc), "line": line[:200]}
            try:
                message = _validate_child_message(message)
            except SandboxProtocolError as exc:
                message = {"kind": "protocol_error", "status": "error", "error": str(exc)}
            if self._stdio_queue:
                self._stdio_queue.put(message)

    def _stdio_read(self, timeout: float) -> dict[str, Any]:
        if not self._stdio_queue:
            raise RuntimeError("stdio sandbox queue is not initialized")
        message = self._stdio_queue.get(timeout=timeout)
        if message.get("kind") == "protocol_error":
            raise SandboxProtocolError(str(message.get("error", "sandbox protocol error")))
        return message

    def _stdio_write(self, payload: dict[str, Any]) -> None:
        if not isinstance(self.process, subprocess.Popen) or self.process.stdin is None:
            raise RuntimeError("stdio sandbox is not writable")
        self.process.stdin.write(_json_ipc_payload(payload) + "\n")
        self.process.stdin.flush()

    def _pipe_read(self) -> dict[str, Any]:
        if not self.parent_conn:
            raise SandboxProtocolError("pipe sandbox is not initialized")
        try:
            raw_message = self.parent_conn.recv_bytes(MAX_IPC_MESSAGE_BYTES)
        except OSError as exc:
            raise SandboxProtocolError(f"pipe IPC message exceeds {MAX_IPC_MESSAGE_BYTES} bytes") from exc
        try:
            message = pickle.loads(raw_message)
        except Exception as exc:
            raise SandboxProtocolError(f"invalid pipe IPC payload: {exc}") from exc
        return _validate_child_message(message)

    def _pipe_write(self, payload: dict[str, Any]) -> None:
        if not self.parent_conn:
            raise SandboxProtocolError("pipe sandbox is not initialized")
        try:
            raw_message = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception as exc:
            raise SandboxProtocolError(f"IPC payload is not serializable: {exc}") from exc
        if len(raw_message) > MAX_IPC_MESSAGE_BYTES:
            raise SandboxProtocolError(f"IPC message exceeds {MAX_IPC_MESSAGE_BYTES} bytes")
        self.parent_conn.send_bytes(raw_message)

    def _audit_spoofed_gateway_identity(self, claimed_plugin: str, request_id: Any) -> None:
        self.gateway.audit_logger.record(
            "plugin.gateway_identity_spoofed",
            "error",
            request_id=str(request_id or ""),
            plugin=self.meta.name,
            action="gateway_request",
            details={"claimed_plugin": claimed_plugin},
        )

    def _close_stdio_pipes(self) -> None:
        if not isinstance(self.process, subprocess.Popen):
            for stream in [self._stdio_stdout_handle, self._stdio_stderr_handle]:
                try:
                    if stream:
                        stream.close()
                except Exception:
                    pass
            self._stdio_stdout_handle = None
            self._stdio_stderr_handle = None
            return
        for stream in [self.process.stdin, self.process.stdout, self._stdio_stdout_handle, self._stdio_stderr_handle]:
            try:
                if stream:
                    stream.close()
            except Exception:
                pass
        self._stdio_stdout_handle = None
        self._stdio_stderr_handle = None

    def _stdio_diagnostics(
        self,
        *,
        error_type: str | None,
        error: str | None,
        command: list[str] | None,
        env: dict[str, str] | None = None,
        env_keys: list[str] | None = None,
        worker_started: bool,
        json_result_received: bool,
    ) -> dict[str, Any]:
        process = self.process if isinstance(self.process, subprocess.Popen) else None
        backend = self.os_limits.get("sandbox_backend", {})
        backend_details = backend.get("details", {}) if isinstance(backend, dict) else {}
        argv = list(command or self._stdio_command or [])
        executable = argv[0] if argv else self.runtime_python
        stderr_excerpt = self._read_stdio_stderr_excerpt()
        stdout_excerpt = self._read_stdio_stdout_excerpt()
        classified_error_type = error_type or _classify_stdio_failure(None, stderr_excerpt)
        return {
            "status": "error" if classified_error_type else "ready",
            "error": error,
            "error_type": classified_error_type,
            "returncode": process.poll() if process is not None else None,
            "stdout_excerpt": stdout_excerpt,
            "stderr_excerpt": stderr_excerpt,
            "stderr_path": str(self._stdio_stderr_path) if self._stdio_stderr_path else None,
            "stdout_path": str(self._stdio_stdout_path) if self._stdio_stdout_path else None,
            "wrapped_command": backend_details.get("wrapped_argv") or argv,
            "argv": argv,
            "cwd": backend_details.get("cwd"),
            "executable": executable,
            "python_version": sys.version,
            "env_keys": sorted(env.keys()) if env is not None else env_keys,
            "import_probe": None,
            "runtime_probe": None,
            "worker_started": worker_started,
            "json_result_received": json_result_received,
        }

    def _read_stdio_stderr_excerpt(self) -> str:
        return _read_text_excerpt(self._stdio_stderr_path)

    def _read_stdio_stdout_excerpt(self) -> str:
        return _read_text_excerpt(self._stdio_stdout_path)

    def _subprocess_env(self) -> dict[str, str]:
        env = dict(os.environ)
        project_root = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = project_root if not existing else os.pathsep.join([project_root, existing])
        return env

    def _scan_or_raise(self) -> None:
        self.static_violations = scan_plugin_source(self.plugin_dir, self.meta)
        if self.static_violations:
            joined = "; ".join(self.static_violations[:5])
            raise SandboxViolation(f"plugin failed static safety scan: {joined}")

    def _attach_new_sandbox_backend(self) -> None:
        if not self.process:
            return
        self._attach_sandbox_backend(self._create_sandbox_backend())

    def _create_sandbox_backend(self) -> SandboxBackend:
        return create_sandbox_backend(
            self.meta.runtime.memory_mb,
            self.meta.runtime.cpu_seconds,
            self._sandbox_backend_name,
        )

    def _attach_sandbox_backend(self, backend: SandboxBackend) -> None:
        if not self.process:
            return
        self._sandbox_backend = backend
        backend_report = self._sandbox_backend.attach_process(self.process)  # type: ignore[arg-type]
        self._record_backend_report(backend_report)
        if self._requires_enforced_backend() and backend_report.missing_capabilities():
            self._raise_missing_backend_capabilities(backend_report)

    def _record_backend_report(self, backend_report: Any) -> None:
        missing_capabilities = backend_report.missing_capabilities()
        self.os_limits["sandbox_backend"] = {
            "name": backend_report.name,
            "enforced": backend_report.enforced,
            "platform": backend_report.platform,
            "details": backend_report.details,
            "warnings": backend_report.warnings,
            "capabilities": backend_report.capabilities,
            "missing_capabilities": missing_capabilities,
        }
        self.os_limits["runtime_python"] = self.runtime_python

    def _raise_missing_backend_capabilities(self, backend_report: Any) -> None:
        missing_capabilities = backend_report.missing_capabilities()
        warning_text = "; ".join(backend_report.warnings)
        suffix = f"; warnings: {warning_text}" if warning_text else ""
        raise SandboxStartupError(
            "strict sandbox requires production isolation capabilities for "
            f"third-party plugin {self.meta.name}; backend {backend_report.name} is missing "
            f"{', '.join(missing_capabilities)}{suffix}"
        )

    def _backend_requires_subprocess_launcher(self) -> bool:
        backend = self._create_sandbox_backend()
        requires_launcher = backend.requires_subprocess_launcher
        backend.close()
        return requires_launcher

    def _requires_enforced_backend(self) -> bool:
        return (
            self.require_enforced_sandbox
            and self.run_mode == RunMode.SUB_PROCESS
            and self.meta.runtime.trust == TrustLevel.THIRD_PARTY
        )

    def _merge_os_limits(self, worker_limits: dict[str, Any]) -> None:
        sandbox_backend = self.os_limits.get("sandbox_backend")
        self.os_limits.update(worker_limits)
        if sandbox_backend:
            self.os_limits["sandbox_backend"] = sandbox_backend

    def _terminate_with_backend(self) -> bool:
        if not self._sandbox_backend:
            return False
        try:
            self._sandbox_backend.terminate(self.process)  # type: ignore[arg-type]
            return self._sandbox_backend.report.enforced
        except Exception as exc:
            self.os_limits.setdefault("sandbox_backend", {}).setdefault("warnings", []).append(str(exc))
            return False

    def _close_sandbox_backend(self) -> None:
        if not self._sandbox_backend:
            return
        try:
            self._sandbox_backend.close()
        finally:
            self._sandbox_backend = None

    def _as_installed(self, plugin: PluginMetadata | InstalledPlugin, plugins_dir: str | Path) -> InstalledPlugin:
        if isinstance(plugin, InstalledPlugin):
            return plugin
        return InstalledPlugin(
            metadata=plugin,
            path=str(Path(plugins_dir).resolve() / plugin.name),
            status=PluginStatus.ENABLED,
            granted_permissions=plugin.permissions,
        )


class SandboxManagerRegistry:
    def __init__(self):
        self._sandboxes: dict[str, SandboxManager] = {}

    def add(self, sandbox: SandboxManager) -> None:
        self._sandboxes[sandbox.meta.name] = sandbox

    def get(self, name: str) -> SandboxManager | None:
        return self._sandboxes.get(name)

    def stop_all(self) -> None:
        for sandbox in list(self._sandboxes.values()):
            sandbox.stop()
        self._sandboxes.clear()
