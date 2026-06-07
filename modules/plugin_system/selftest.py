from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import sys
import traceback
from typing import Any, Callable

from .tool_contracts import TOOL_SERVICE_CONTRACT_VERSION, utc_now


def run_plugin_system_selftest() -> dict[str, Any]:
    """Run the plugin-system internal end-to-end selftest suite."""

    from .model_loop_adapter import run_model_loop_adapter_selftest
    from .evidence_adapters import adapters_status
    from .integration_contract import run_integration_contract_check
    from .production_policy_check import run_production_policy_check
    from .schema_validation import run_schema_validation_selftest
    from .status import PluginSystemStatusProvider
    from .test_plan import test_plan_report
    from .tool_manager_adapter import run_tool_manager_adapter_selftest
    from .tool_selftest import run_tool_selftest as run_core_tool_selftest
    from .tool_service import run_tool_service_selftest

    suites: dict[str, dict[str, Any]] = {
        "schema_validation": run_schema_validation_selftest(),
        "tool_selftest": run_core_tool_selftest(),
        "tool_service": run_tool_service_selftest(),
        "tool_manager_adapter": run_tool_manager_adapter_selftest(),
        "model_loop_adapter": run_model_loop_adapter_selftest(),
    }
    status_snapshot = PluginSystemStatusProvider().get_full_status()
    policy_check = run_production_policy_check(production_mode=False)
    evidence_adapters = adapters_status()
    integration_contract = run_integration_contract_check()
    test_plan = test_plan_report()
    template_check = _template_check()
    checks_by_name = {
        "schema_validation_success": suites["schema_validation"].get("status") == "success",
        "tool_selftest_success": suites["tool_selftest"].get("status") == "success",
        "tool_service_success": suites["tool_service"].get("status") == "success",
        "tool_manager_adapter_success": suites["tool_manager_adapter"].get("status") == "success",
        "model_loop_adapter_success": suites["model_loop_adapter"].get("status") == "success",
        "status_snapshot_json_safe": _json_safe(status_snapshot)
        and status_snapshot.get("status") == "success",
        "status_has_required_sections": _has_required_status_sections(status_snapshot),
        "production_policy_dev_mode_runs": policy_check.get("status") in {"pass", "warn", "fail"}
        and policy_check.get("production_mode") is False,
        "production_policy_has_recommendations": isinstance(policy_check.get("recommendations"), list),
        "evidence_adapters_available": evidence_adapters.get("status") == "success",
        "integration_contract_success": integration_contract.get("status") == "pass",
        "test_plan_available": test_plan.get("status") == "success",
        "templates_valid": template_check.get("status") == "success",
        "json_output_clean": _json_output_clean_check(),
    }
    failed = sorted(name for name, ok in checks_by_name.items() if not ok)
    return {
        "status": "success" if not failed else "fail",
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "checks": _checks_list(checks_by_name),
        "checks_by_name": checks_by_name,
        "failed_checks": failed,
        "warnings": [],
        "suites": suites,
        "status_snapshot": status_snapshot,
        "production_policy_check": policy_check,
        "evidence_adapters": evidence_adapters,
        "integration_contract": integration_contract,
        "test_plan": test_plan,
        "templates": template_check,
        "logs_suppressed": False,
        "generated_at": utc_now(),
    }


def render_text(report: dict[str, Any]) -> str:
    if "suites" not in report:
        from .tool_selftest import render_text as render_tool_text

        return render_tool_text(report)
    failed = report.get("failed_checks") or []
    if failed:
        return f"Plugin system selftest failed: {', '.join(str(item) for item in failed)}"
    return "Plugin system selftest passed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run plugin system aggregate selftest")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--quiet-json", action="store_true")
    args = parser.parse_args(argv)
    json_mode = bool(args.json_output or args.quiet_json)
    if json_mode:
        report = _run_selftest_for_json()
        emit_json_stdout_only(report)
    else:
        report = run_plugin_system_selftest()
        print(render_text(report))
    return 0 if report.get("status") == "success" else 1


def _run_selftest_for_json() -> dict[str, Any]:
    try:
        report, captured = capture_selftest_logs(run_plugin_system_selftest)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return {
            "status": "fail",
            "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
            "checks": [],
            "checks_by_name": {},
            "failed_checks": ["selftest_internal_error"],
            "warnings": [],
            "error": {
                "code": "SELFTEST_INTERNAL_ERROR",
                "message": str(exc),
                "type": type(exc).__name__,
            },
            "logs_suppressed": True,
            "generated_at": utc_now(),
        }
    payload = dict(report)
    payload["logs_suppressed"] = True
    if captured.get("stdout") or captured.get("stderr"):
        payload["suppressed_log_bytes"] = {
            "stdout": len(captured.get("stdout", "").encode("utf-8", errors="replace")),
            "stderr": len(captured.get("stderr", "").encode("utf-8", errors="replace")),
        }
    return payload


@contextlib.contextmanager
def suppress_noisy_loggers() -> Any:
    previous_disable = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        yield
    finally:
        logging.disable(previous_disable)


def capture_selftest_logs(func: Callable[[], dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str]]:
    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    with suppress_noisy_loggers():
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            report = func()
    return report, {"stdout": stdout_buffer.getvalue(), "stderr": stderr_buffer.getvalue()}


def emit_json_stdout_only(report: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(report, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _checks_list(checks: dict[str, bool]) -> list[dict[str, Any]]:
    return [{"name": name, "ok": bool(ok)} for name, ok in sorted(checks.items())]


def _json_safe(value: Any) -> bool:
    try:
        json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return True


def _has_required_status_sections(status_snapshot: dict[str, Any]) -> bool:
    required = {
        "platform",
        "plugins",
        "tools",
        "security",
        "governance",
        "audit",
        "supply_chain",
        "llm_tool_service",
        "template_status",
        "evidence_adapters",
        "integration_contract",
    }
    return required <= set(status_snapshot)


def _json_output_clean_check() -> bool:
    def noisy_report() -> dict[str, Any]:
        print("this stdout should be suppressed")
        print("this stderr should be suppressed", file=sys.stderr)
        logging.getLogger("plugin_system.selftest.noisy").warning("this logger should be suppressed")
        return {"status": "success", "generated_at": utc_now()}

    report, captured = capture_selftest_logs(noisy_report)
    rendered = json.dumps(report, sort_keys=True)
    try:
        json.loads(rendered)
    except json.JSONDecodeError:
        return False
    return "this stdout" in captured["stdout"] and "this stderr" in captured["stderr"]


def _template_check() -> dict[str, Any]:
    from pathlib import Path

    from .llm_tools import LLMToolCatalog
    from .manifest_lint import lint_plugin_manifest
    from .provider_tools import ProviderToolExportOptions, export_provider_tools

    expectations = {
        "hello_world": {"actor_role": "model", "exported_min": 1, "expected_exposure": "model_default"},
        "read_only_memory_tool": {"actor_role": "model", "exported_min": 1, "expected_exposure": "model_default"},
        "network_lookup_tool": {"actor_role": "expert", "exported_min": 1, "expected_exposure": "expert_only"},
        "admin_only_output_tool": {"actor_role": "admin", "exported_min": 1, "expected_exposure": "admin_only"},
        "production_plugin_package": {
            "actor_role": "model",
            "exported_min": 1,
            "expected_exposure": "model_default",
        },
        "readonly_retrieval_plugin": {
            "actor_role": "model",
            "exported_min": 1,
            "expected_exposure": "model_default",
        },
        "controlled_network_plugin": {
            "actor_role": "expert",
            "exported_min": 1,
            "expected_exposure": "expert_only",
        },
        "file_summary_plugin": {
            "actor_role": "expert",
            "exported_min": 1,
            "expected_exposure": "expert_only",
        },
    }
    templates_dir = Path(__file__).resolve().parent / "templates"
    template_results: dict[str, Any] = {}
    checks: dict[str, bool] = {}
    for template_name, expectation in expectations.items():
        plugin_dir = templates_dir / template_name
        lint = lint_plugin_manifest(plugin_dir, production_mode=False)
        catalog = LLMToolCatalog.from_plugin_dir(
            plugin_dir,
            actor_role=str(expectation["actor_role"]),
            production_mode=False,
            approved=True,
            include_hidden=True,
        )
        export = export_provider_tools(
            catalog,
            options=ProviderToolExportOptions(
                provider="openai",
                actor_role=str(expectation["actor_role"]),
                production_mode=False,
                include_hidden=False,
            ),
        )
        exposures = sorted({str(spec.exposure) for spec in catalog.specs})
        result = {
            "lint_status": lint.get("status"),
            "lint_error_count": len(lint.get("errors", [])),
            "warning_count": len(lint.get("warnings", [])),
            "actor_role": expectation["actor_role"],
            "exported_count": len(export.get("tools", [])),
            "exposures": exposures,
            "warnings": export.get("warnings", []),
        }
        exported_min_value = expectation["exported_min"]
        exported_min = exported_min_value if isinstance(exported_min_value, int) else int(str(exported_min_value))
        template_results[template_name] = result
        checks[f"{template_name}_lint_no_errors"] = result["lint_error_count"] == 0
        checks[f"{template_name}_exports"] = result["exported_count"] >= exported_min
        checks[f"{template_name}_exposure"] = str(expectation["expected_exposure"]) in exposures
    failed = sorted(name for name, ok in checks.items() if not ok)
    return {
        "status": "success" if not failed else "error",
        "checks": checks,
        "failed_checks": failed,
        "templates": template_results,
        "generated_at": utc_now(),
    }


def run_tool_selftest() -> dict[str, Any]:
    from .tool_selftest import run_tool_selftest as run_core_tool_selftest

    return run_core_tool_selftest()


__all__ = [
    "capture_selftest_logs",
    "emit_json_stdout_only",
    "main",
    "render_text",
    "run_plugin_system_selftest",
    "run_tool_selftest",
    "suppress_noisy_loggers",
]


if __name__ == "__main__":
    raise SystemExit(main())
