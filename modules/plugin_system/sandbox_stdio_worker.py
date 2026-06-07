from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any, TextIO

from .models import PluginMetadata
from .sandbox import (
    MAX_IPC_MESSAGE_BYTES,
    SandboxViolation,
    _json_ipc_payload,
    _apply_os_resource_limits,
    _call_event_entry,
    _call_entry,
    _call_memory_provider_entry,
    _call_middleware_entry,
    _install_child_runtime_guards,
    _install_post_load_runtime_guards,
    _load_entry,
)


class StdioGatewayProxy:
    def __init__(self, protocol_out: TextIO, protocol_in: TextIO, plugin_name: str):
        self._out = protocol_out
        self._in = protocol_in
        self._plugin_name = plugin_name
        self._request_id: str | None = None

    def set_request_id(self, request_id: str | None) -> None:
        self._request_id = request_id

    def request(self, request_type: str, payload: dict[str, Any]) -> Any:
        _send(
            self._out,
            {
                "kind": "gateway_request",
                "plugin": self._plugin_name,
                "request_type": request_type,
                "request_id": self._request_id,
                "payload": payload,
            },
        )
        line = self._in.readline()
        if not line:
            raise SandboxViolation("gateway channel closed")
        response = json.loads(line)
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
        return self.request("network.outbound", {"url": url, "method": method, **kwargs})

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


def main(argv: list[str] | None = None) -> int:
    argv = argv or sys.argv[1:]
    if len(argv) != 2:
        _send(sys.stdout, {"kind": "lifecycle", "status": "error", "error": "invalid worker arguments"})
        return 2

    protocol_out = sys.stdout
    protocol_in = sys.stdin
    sys.stdout = sys.stderr

    plugin_dir = Path(argv[0]).resolve()
    worker_payload = json.loads(base64.b64decode(argv[1]).decode("utf-8"))
    if "metadata" in worker_payload:
        metadata_payload = worker_payload["metadata"]
        granted_permissions = set(worker_payload.get("granted_permissions", []))
    else:
        metadata_payload = worker_payload
        metadata = PluginMetadata(**metadata_payload)
        granted_permissions = metadata.requested_permissions
    metadata = PluginMetadata(**metadata_payload)
    os_report = _apply_os_resource_limits(metadata.runtime.memory_mb, metadata.runtime.cpu_seconds)

    try:
        _install_child_runtime_guards(metadata, plugin_dir, granted_permissions)
        functions = {
            tool_name: _load_entry(plugin_dir, entry)
            for tool_name, entry in metadata.tool_entries().items()
        }
        middleware_entries = metadata.middleware_entries()
        middleware_functions = {
            middleware_name: _load_entry(plugin_dir, entry)
            for middleware_name, entry in middleware_entries.items()
        }
        memory_provider_entries = metadata.memory_provider_entries()
        memory_provider_functions = {
            provider_name: _load_entry(plugin_dir, entry)
            for provider_name, entry in memory_provider_entries.items()
        }
        event_entries = metadata.event_listener_entries()
        event_functions = {
            entry: _load_entry(plugin_dir, entry)
            for entries_for_event in event_entries.values()
            for entry in entries_for_event
        }
        _install_post_load_runtime_guards()
        api = StdioGatewayProxy(protocol_out, protocol_in, metadata.name)
        _send(protocol_out, {"kind": "lifecycle", "status": "ready", "os_limits": os_report})

        while True:
            line = protocol_in.readline(MAX_IPC_MESSAGE_BYTES + 1)
            if not line:
                break
            if len(line.encode("utf-8")) > MAX_IPC_MESSAGE_BYTES:
                raise SandboxViolation(f"stdio IPC message exceeds {MAX_IPC_MESSAGE_BYTES} bytes")
            if not line.strip():
                continue
            message = json.loads(line)
            if message.get("action") == "shutdown":
                _send(protocol_out, {"kind": "lifecycle", "status": "stopped"})
                break
            if message.get("action") != "execute_tool":
                if message.get("action") == "run_middleware":
                    payload = message.get("payload", {})
                    middleware_name = payload.get("middleware_name")
                    if middleware_name not in middleware_functions:
                        _send(
                            protocol_out,
                            {
                                "kind": "result",
                                "status": "error",
                                "error": f"unknown middleware: {middleware_name}",
                            },
                        )
                        continue
                    try:
                        api.set_request_id(message.get("request_id"))
                        result = _call_middleware_entry(
                            middleware_functions[middleware_name],
                            payload.get("context", {}),
                            api,
                        )
                        _send(protocol_out, {"kind": "result", "status": "success", "data": result})
                    except Exception as exc:
                        _send(
                            protocol_out,
                            {
                                "kind": "result",
                                "status": "error",
                                "error": str(exc),
                                "traceback": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    continue
                if message.get("action") == "call_memory_provider":
                    payload = message.get("payload", {})
                    provider_name = payload.get("provider_name")
                    if provider_name not in memory_provider_functions:
                        _send(
                            protocol_out,
                            {
                                "kind": "result",
                                "status": "error",
                                "error": f"unknown memory provider: {provider_name}",
                            },
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
                        _send(protocol_out, {"kind": "result", "status": "success", "data": result})
                    except Exception as exc:
                        _send(
                            protocol_out,
                            {
                                "kind": "result",
                                "status": "error",
                                "error": str(exc),
                                "traceback": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    continue
                if message.get("action") == "handle_event":
                    payload = message.get("payload", {})
                    event_name = payload.get("event", {}).get("name")
                    listeners = event_entries.get(event_name, [])
                    try:
                        api.set_request_id(message.get("request_id"))
                        results = [
                            _call_event_entry(event_functions[entry], payload.get("event", {}), api)
                            for entry in listeners
                        ]
                        _send(protocol_out, {"kind": "result", "status": "success", "data": results})
                    except Exception as exc:
                        _send(
                            protocol_out,
                            {
                                "kind": "result",
                                "status": "error",
                                "error": str(exc),
                                "traceback": f"{type(exc).__name__}: {exc}",
                            },
                        )
                    continue
                _send(protocol_out, {"kind": "result", "status": "error", "error": "unknown sandbox action"})
                continue
            payload = message.get("payload", {})
            tool_name = payload.get("tool_name")
            if tool_name not in functions:
                _send(protocol_out, {"kind": "result", "status": "error", "error": f"unknown tool: {tool_name}"})
                continue
            try:
                api.set_request_id(message.get("request_id"))
                result = _call_entry(functions[tool_name], payload.get("args", {}), api)
                _send(protocol_out, {"kind": "result", "status": "success", "data": result})
            except Exception as exc:
                _send(
                    protocol_out,
                    {
                        "kind": "result",
                        "status": "error",
                        "error": str(exc),
                        "traceback": f"{type(exc).__name__}: {exc}",
                    },
                )
    except BaseException as exc:
        _send(
            protocol_out,
            {
                "kind": "lifecycle",
                "status": "error",
                "error": str(exc),
                "traceback": f"{type(exc).__name__}: {exc}",
                "os_limits": os_report,
            },
        )
        return 1
    return 0


def _send(stream: TextIO, payload: dict[str, Any]) -> None:
    stream.write(_json_ipc_payload(payload) + "\n")
    stream.flush()


if __name__ == "__main__":
    raise SystemExit(main())
