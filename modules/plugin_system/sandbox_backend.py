from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


STRICT_SANDBOX_REQUIRED_CAPABILITIES = (
    "process_containment",
    "resource_limits",
    "filesystem_isolation",
    "network_isolation",
)

SANDBOX_CAPABILITY_DEFAULTS = {
    "process_containment": False,
    "resource_limits": False,
    "filesystem_isolation": False,
    "network_isolation": False,
    "system_call_filtering": False,
    "language_runtime_guards": False,
}

EXTERNAL_SANDBOX_ATTESTATION_ENV = "HUMANOID_AGI_EXTERNAL_SANDBOX_ATTESTATION"
EXTERNAL_SANDBOX_REQUIRED_TOKENS = frozenset(
    {
        "process_containment",
        "resource_limits",
        "filesystem_isolation",
        "network_isolation",
    }
)


def sandbox_capabilities(**overrides: bool) -> dict[str, bool]:
    capabilities = dict(SANDBOX_CAPABILITY_DEFAULTS)
    capabilities.update(overrides)
    return capabilities


class ProcessLike(Protocol):
    pid: int | None


@dataclass
class SandboxBackendReport:
    name: str
    enforced: bool
    platform: str
    details: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    capabilities: dict[str, bool] = field(default_factory=sandbox_capabilities)

    def missing_capabilities(
        self,
        required: tuple[str, ...] = STRICT_SANDBOX_REQUIRED_CAPABILITIES,
    ) -> list[str]:
        return [capability for capability in required if not self.capabilities.get(capability, False)]


class SandboxBackend:
    name = "python_guard"
    requires_subprocess_launcher = False

    def __init__(self, memory_mb: int, cpu_seconds: int):
        self.memory_mb = memory_mb
        self.cpu_seconds = cpu_seconds
        self.report = SandboxBackendReport(
            name=self.name,
            enforced=False,
            platform=sys.platform,
            details={
                "memory_mb": memory_mb,
                "cpu_seconds": cpu_seconds,
            },
        )

    def attach_process(self, process: ProcessLike) -> SandboxBackendReport:
        self.report.details["process_id"] = process.pid
        return self.report

    def prepare_subprocess(
        self,
        command: list[str],
        *,
        plugin_dir: Path,
        project_root: Path,
    ) -> list[str]:
        return command

    def terminate(self, process: ProcessLike | None = None) -> None:
        return None

    def close(self) -> None:
        return None


class PythonGuardBackend(SandboxBackend):
    name = "python_guard"

    def __init__(self, memory_mb: int, cpu_seconds: int):
        super().__init__(memory_mb, cpu_seconds)
        self.report.capabilities["language_runtime_guards"] = True


class WindowsJobBackend(SandboxBackend):
    """Windows Job Object resource limiter.

    This is not a complete security sandbox: it does not provide filesystem
    isolation, network isolation, or syscall filtering.
    """

    name = "windows_job_object"

    def __init__(self, memory_mb: int, cpu_seconds: int):
        super().__init__(memory_mb, cpu_seconds)
        self._kernel32: Any = None
        self._job_handle: int | None = None
        self._closed = False
        if sys.platform != "win32":
            self.report.warnings.append("Windows Job Object backend is only available on Windows")
            return
        try:
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._configure_api()
            self._job_handle = self._kernel32.CreateJobObjectW(None, None)
            if not self._job_handle:
                raise OSError(ctypes.get_last_error(), "CreateJobObjectW failed")
            self._configure_limits()
            self.report.enforced = True
            self.report.capabilities.update(
                {
                    "process_containment": True,
                    "resource_limits": True,
                }
            )
        except Exception as exc:
            self.report.enforced = False
            self.report.warnings.append(f"Windows Job Object unavailable: {exc}")
            self.close()

    def attach_process(self, process: ProcessLike) -> SandboxBackendReport:
        super().attach_process(process)
        if not self._kernel32 or not self._job_handle or not process.pid:
            return self.report
        process_handle = None
        try:
            process_handle = self._kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, process.pid)
            if not process_handle:
                raise OSError(ctypes.get_last_error(), f"OpenProcess failed for pid {process.pid}")
            if not self._kernel32.AssignProcessToJobObject(self._job_handle, process_handle):
                raise OSError(ctypes.get_last_error(), f"AssignProcessToJobObject failed for pid {process.pid}")
            self.report.details["assigned_to_job"] = True
        except Exception as exc:
            self.report.enforced = False
            self.report.warnings.append(str(exc))
        finally:
            if process_handle:
                self._kernel32.CloseHandle(process_handle)
        return self.report

    def terminate(self, process: ProcessLike | None = None) -> None:
        if self._kernel32 and self._job_handle:
            self._kernel32.TerminateJobObject(self._job_handle, 1)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._kernel32 and self._job_handle:
            self._kernel32.CloseHandle(self._job_handle)
        self._job_handle = None

    def _configure_api(self) -> None:
        assert self._kernel32 is not None
        self._kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        self._kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        self._kernel32.SetInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
        ]
        self._kernel32.SetInformationJobObject.restype = ctypes.c_bool
        self._kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self._kernel32.AssignProcessToJobObject.restype = ctypes.c_bool
        self._kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self._kernel32.TerminateJobObject.restype = ctypes.c_bool
        self._kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        self._kernel32.OpenProcess.restype = ctypes.c_void_p
        self._kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        self._kernel32.CloseHandle.restype = ctypes.c_bool

    def _configure_limits(self) -> None:
        assert self._kernel32 is not None
        assert self._job_handle is not None
        limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        limits.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | JOB_OBJECT_LIMIT_JOB_MEMORY
        )
        limits.ProcessMemoryLimit = self.memory_mb * 1024 * 1024
        limits.JobMemoryLimit = self.memory_mb * 1024 * 1024
        if not self._kernel32.SetInformationJobObject(
            self._job_handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        self.report.details.update(
            {
                "kill_on_job_close": True,
                "process_memory_limit_bytes": limits.ProcessMemoryLimit,
                "job_memory_limit_bytes": limits.JobMemoryLimit,
            }
        )


class ExternalEnforcedBackend(SandboxBackend):
    """Trust an externally enforced sandbox only when explicit attestation is present.

    This backend is for deployments where the plugin runner is already launched inside
    a container, micro-VM, restricted service account, or another OS-managed boundary.
    It does not create that boundary by itself.
    """

    name = "external_enforced"

    def __init__(self, memory_mb: int, cpu_seconds: int):
        super().__init__(memory_mb, cpu_seconds)
        raw_attestation = os.environ.get(EXTERNAL_SANDBOX_ATTESTATION_ENV, "")
        tokens = {
            token.strip().lower()
            for token in raw_attestation.replace(";", ",").split(",")
            if token.strip()
        }
        missing = sorted(EXTERNAL_SANDBOX_REQUIRED_TOKENS - tokens)
        self.report.details.update(
            {
                "attestation_env": EXTERNAL_SANDBOX_ATTESTATION_ENV,
                "attested_capabilities": sorted(tokens),
                "required_attestation": sorted(EXTERNAL_SANDBOX_REQUIRED_TOKENS),
            }
        )
        if missing:
            self.report.warnings.append(
                "external sandbox attestation is incomplete; missing " + ", ".join(missing)
            )
            return
        self.report.enforced = True
        self.report.capabilities.update(
            {
                "process_containment": True,
                "resource_limits": True,
                "filesystem_isolation": True,
                "network_isolation": True,
            }
        )


class BubblewrapBackend(SandboxBackend):
    """Launch stdio plugin workers through Linux bubblewrap."""

    name = "bubblewrap"
    requires_subprocess_launcher = True

    def __init__(self, memory_mb: int, cpu_seconds: int):
        super().__init__(memory_mb, cpu_seconds)
        self._binary = shutil.which("bwrap")
        resource_limits_available = _resource_limits_available()
        self.report.details.update(
            {
                "binary": self._binary,
                "requires_subprocess_launcher": True,
                "resource_limits_source": "worker_rlimit" if resource_limits_available else None,
                "environment": "clearenv_with_minimal_path_pythonpath_home_tmpdir",
            }
        )
        if sys.platform != "linux":
            self.report.warnings.append("bubblewrap backend is only available on Linux")
            return
        if not self._binary:
            self.report.warnings.append("bubblewrap executable not found on PATH")
            return
        probe = _probe_bubblewrap(self._binary)
        self.report.details["probe"] = probe
        if not probe["ok"]:
            self.report.warnings.append(f"bubblewrap probe failed: {probe['error']}")
            return
        self.report.enforced = True
        self.report.capabilities.update(
            {
                "process_containment": True,
                "resource_limits": resource_limits_available,
                "filesystem_isolation": True,
                "network_isolation": True,
            }
        )
        if not resource_limits_available:
            self.report.warnings.append("Python resource module is unavailable; CPU and memory rlimits are disabled")

    def prepare_subprocess(
        self,
        command: list[str],
        *,
        plugin_dir: Path,
        project_root: Path,
    ) -> list[str]:
        if not self.report.enforced or not self._binary:
            raise RuntimeError("; ".join(self.report.warnings) or "bubblewrap backend is unavailable")

        plugin_path = plugin_dir.resolve()
        project_path = project_root.resolve()
        data_dir = (plugin_path / "data").resolve()
        data_dir.mkdir(parents=True, exist_ok=True)

        runtime_root = (plugin_path.parent / "_sandbox_runtime" / plugin_path.name).resolve()
        runtime_plugin_system_dir = _prepare_runtime_package(project_path, runtime_root)
        runtime_modules_package_init = runtime_root / "modules" / "__init__.py"
        stdio_worker = runtime_plugin_system_dir / "sandbox_stdio_worker.py"
        plugin_source_dir = plugin_path / "src"
        runtime_python_binds = _python_runtime_bind_paths(command[0])
        readonly_system_paths = [
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc/ssl"),
            Path("/etc/ca-certificates"),
        ]
        readonly_binds = _dedupe_existing_paths(
            [
                *readonly_system_paths,
                runtime_modules_package_init,
                runtime_plugin_system_dir,
                plugin_source_dir,
                *runtime_python_binds,
                plugin_path / "plugin.yaml",
                plugin_path / "requirements.lock",
                plugin_path / "wheels",
                plugin_path / ".venv",
            ]
        )
        blocked_host_paths = _dedupe_existing_paths(
            [
                Path.home(),
                project_path / ".env",
                project_path / ".git",
                project_path / "data",
                project_path / "tests",
                project_path / "SPECIFICATION",
                project_path / "cli.py",
            ]
        )
        wrapped = [
            self._binary,
            "--die-with-parent",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-net",
            "--unshare-uts",
            "--new-session",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--clearenv",
        ]
        wrapped.extend(_bwrap_dir_args(plugin_path.parent))
        wrapped.extend(_bwrap_dir_args(data_dir.parent))
        wrapped.extend(_bwrap_dir_args(runtime_plugin_system_dir))
        wrapped.extend(_bwrap_dir_args(plugin_path))
        for bind_path in runtime_python_binds:
            wrapped.extend(_bwrap_dir_args(bind_path))
        for bind_path in readonly_binds:
            wrapped.extend(["--ro-bind", str(bind_path), str(bind_path)])
        wrapped.extend(
            [
                "--bind",
                str(data_dir),
                str(data_dir),
                "--setenv",
                "PATH",
                "/usr/local/bin:/usr/bin:/bin",
                "--setenv",
                "PYTHONPATH",
                str(runtime_root),
                "--setenv",
                "HOME",
                str(data_dir),
                "--setenv",
                "TMPDIR",
                "/tmp",
                "--chdir",
                str(data_dir),
                "--",
                *command,
            ]
        )
        self.report.details.update(
            {
                "wrapped_command": True,
                "readonly_binds": [str(path) for path in readonly_binds],
                "writable_binds": [str(data_dir)],
                "blocked_host_paths": [str(path) for path in blocked_host_paths],
                "pythonpath": str(runtime_root),
                "runtime_python_binds": [str(path) for path in runtime_python_binds],
                "runtime_root": str(runtime_root),
                "worker": str(stdio_worker),
                "network": "unshared",
                "tmp": "private_tmpfs",
                "home": str(data_dir),
                "tmpdir": "/tmp",
                "data_dir": str(data_dir),
                "code_dir": str(plugin_source_dir),
                "cwd": str(data_dir),
                "wrapped_argv": list(wrapped),
                "inner_argv": list(command),
            }
        )
        return wrapped


def create_sandbox_backend(memory_mb: int, cpu_seconds: int, requested: str = "auto") -> SandboxBackend:
    if requested == "python_guard":
        return PythonGuardBackend(memory_mb, cpu_seconds)
    if requested == "windows_job_object":
        return WindowsJobBackend(memory_mb, cpu_seconds)
    if requested == "bubblewrap":
        return BubblewrapBackend(memory_mb, cpu_seconds)
    if requested == "external_enforced":
        return ExternalEnforcedBackend(memory_mb, cpu_seconds)
    if requested != "auto":
        backend = PythonGuardBackend(memory_mb, cpu_seconds)
        backend.report.warnings.append(f"unknown sandbox backend requested: {requested}")
        return backend
    if sys.platform == "linux":
        return BubblewrapBackend(memory_mb, cpu_seconds)
    if sys.platform == "win32":
        return WindowsJobBackend(memory_mb, cpu_seconds)
    backend = PythonGuardBackend(memory_mb, cpu_seconds)
    backend.report.warnings.append("OS-level sandbox backend is not configured for this platform")
    return backend


def _resource_limits_available() -> bool:
    try:
        import resource  # noqa: F401
    except Exception:
        return False
    return True


def _probe_bubblewrap(binary: str) -> dict[str, Any]:
    true_binary = Path("/usr/bin/true") if Path("/usr/bin/true").exists() else Path("/bin/true")
    if not true_binary.exists():
        return {"ok": False, "error": "true executable not found"}
    command = _bubblewrap_probe_command(binary, true_binary)
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3,
        )
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if result.returncode != 0:
        return {
            "ok": False,
            "error": (result.stderr or result.stdout or f"exit {result.returncode}").strip(),
        }
    return {"ok": True}


def _bubblewrap_probe_command(binary: str, true_binary: Path) -> list[str]:
    readonly_system_paths = _dedupe_existing_paths(
        [
            Path("/usr"),
            Path("/bin"),
            Path("/lib"),
            Path("/lib64"),
            Path("/etc/ssl"),
            Path("/etc/ca-certificates"),
        ]
    )
    command = [
        binary,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-net",
        "--unshare-uts",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
        "--clearenv",
    ]
    for bind_path in readonly_system_paths:
        command.extend(["--ro-bind", str(bind_path), str(bind_path)])
    command.extend(
        [
            "--setenv",
            "PATH",
            "/usr/local/bin:/usr/bin:/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "TMPDIR",
            "/tmp",
        ]
    )
    command.extend(
        [
            "--",
            str(true_binary),
        ]
    )
    return command


def _prepare_runtime_package(project_root: Path, runtime_root: Path) -> Path:
    source_modules = project_root / "modules"
    source_plugin_system = source_modules / "plugin_system"
    if not source_plugin_system.is_dir():
        raise RuntimeError(f"plugin system runtime source not found: {source_plugin_system}")

    runtime_modules = runtime_root / "modules"
    runtime_plugin_system = runtime_modules / "plugin_system"
    runtime_root.mkdir(parents=True, exist_ok=True)
    if runtime_plugin_system.exists():
        shutil.rmtree(runtime_plugin_system)
    runtime_plugin_system.mkdir(parents=True)

    runtime_modules.mkdir(parents=True, exist_ok=True)
    source_init = source_modules / "__init__.py"
    if source_init.exists():
        shutil.copy2(source_init, runtime_modules / "__init__.py")
    else:
        (runtime_modules / "__init__.py").write_text("", encoding="utf-8")

    (runtime_plugin_system / "__init__.py").write_text("", encoding="utf-8")
    runtime_files = [
        "audit.py",
        "config.py",
        "dependency.py",
        "event_bus.py",
        "gateway.py",
        "models.py",
        "sandbox.py",
        "sandbox_backend.py",
        "sandbox_stdio_worker.py",
        "signing.py",
    ]
    for filename in runtime_files:
        source = source_plugin_system / filename
        if not source.exists():
            raise RuntimeError(f"plugin system runtime file not found: {source}")
        shutil.copy2(source, runtime_plugin_system / filename)
    return runtime_plugin_system


def _python_runtime_bind_paths(python_executable: str) -> list[Path]:
    executable = Path(python_executable)
    parents = list(executable.parents)
    venv_root = next((parent for parent in parents if (parent / "pyvenv.cfg").exists()), None)
    if venv_root is not None:
        return _dedupe_existing_paths([venv_root])
    return _dedupe_existing_paths([executable])


def _dedupe_existing_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    existing: list[Path] = []
    for path in paths:
        if not path.exists():
            continue
        candidate = path if path.is_absolute() else path.resolve()
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        existing.append(candidate)
    return existing


def _bwrap_dir_args(path: Path) -> list[str]:
    resolved = path.resolve()
    parts = list(resolved.parents)
    parts.reverse()
    parts.append(resolved)
    args: list[str] = []
    seen: set[str] = set()
    for item in parts:
        if item == item.parent:
            continue
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        args.extend(["--dir", key])
    return args


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_uint32),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_uint32),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", ctypes.c_uint32),
        ("SchedulingClass", ctypes.c_uint32),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


JobObjectExtendedLimitInformation = 9
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROCESS_TERMINATE = 0x0001
PROCESS_SET_QUOTA = 0x0100
