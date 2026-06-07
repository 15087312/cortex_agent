from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from typing import Any

from .tool_contracts import utc_now


@dataclass(frozen=True)
class RecommendedTest:
    test_name: str
    target_module: str
    scenario: str
    expected_result: str
    production_safety_relevance: str
    suggested_file: str
    priority: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def recommended_tests() -> list[RecommendedTest]:
    return [
        RecommendedTest("provider_openai_parsing", "provider_tools.py", "OpenAI chat/function/responses tool calls parse into one ModelToolCall.", "valid calls parse; malformed JSON and multiple single-entry calls return safe errors", "provider payload compatibility without unsafe args injection", "tests/plugin_system/test_provider_tools.py", "P0"),
        RecommendedTest("provider_anthropic_parsing", "provider_tools.py", "Anthropic tool_use content parses and ignores thinking fields.", "tool args exclude thinking/reasoning metadata", "prevents chain-of-thought/provider metadata entering plugin args", "tests/plugin_system/test_provider_tools.py", "P0"),
        RecommendedTest("provider_export_golden_schema", "provider_tools.py", "OpenAI/Anthropic/generic exports retain object schemas and mapping metadata.", "schemas are provider-valid and names map back to plugin/tool ids", "stable tool listing for model providers", "tests/plugin_system/test_provider_export.py", "P0"),
        RecommendedTest("schema_validation_failures", "schema_validation.py", "Params and returns reject missing required, bad types, format/pattern violations.", "PARAM_SCHEMA_ERROR or RETURN_SCHEMA_ERROR with safe path", "blocks malformed model args and unsafe plugin outputs", "tests/plugin_system/test_schema_validation.py", "P0"),
        RecommendedTest("permission_denied_gateway", "engine.py", "Tool requesting network/fs/output without grant is denied.", "PERMISSION_DENIED and no raw exception text", "keeps runtime permissions scoped per tool", "tests/plugin_system/test_permissions.py", "P0"),
        RecommendedTest("confirmation_token_binding", "tool_governance.py", "High-risk tool confirmation token is bound to role/conversation/tool/args.", "changed args or role rejects token", "prevents replay of human approval", "tests/plugin_system/test_governance.py", "P0"),
        RecommendedTest("governance_budget_rate_idempotency", "tool_governance.py", "Budgets, rate limits, loop detection, idempotency cache all enforce.", "second/duplicate/over-budget calls return stable governance errors", "prevents tool storms and duplicate side effects", "tests/plugin_system/test_governance.py", "P0"),
        RecommendedTest("audit_sanitization", "audit.py", "Audit records include summaries and hashes, not full args/results/secrets.", "secret markers are absent from persisted audit records", "reduces audit data leakage", "tests/plugin_system/test_audit_safety.py", "P0"),
        RecommendedTest("sandbox_evidence_parser", "production_evidence.py", "Target Linux/self-hosted sandbox evidence passes only with all required checks.", "GitHub-hosted diagnostic and Windows Job Object never clear production blocker", "prevents weak sandbox evidence from being accepted", "tests/plugin_system/test_production_evidence.py", "P1"),
        RecommendedTest("scanner_evidence_adapter", "evidence_adapters.py", "pip-audit/OSV/Grype/Safety/generic reports normalize to ScannerEvidence.", ".example/offline reports are not production evidence; high/critical findings fail", "keeps scanner input machine-readable without fake passes", "tests/plugin_system/test_evidence_adapters.py", "P1"),
        RecommendedTest("status_snapshot", "status.py", "Status includes evidence adapters, templates, governance, sandbox, integration contract sections.", "missing external services produce warnings, not production ready", "operator visibility for launch readiness", "tests/plugin_system/test_status.py", "P1"),
        RecommendedTest("model_loop_adapter_e2e", "model_loop_adapter.py", "Build tools, parse model response, execute, append provider result.", "result is provider tool message, not system/developer text", "keeps model-loop integration safe", "tests/plugin_system/test_model_loop_adapter.py", "P0"),
        RecommendedTest("tool_manager_adapter_bridge", "tool_manager_adapter.py", "Legacy adapter list/execute delegates to PluginToolService.", "adapter metadata says delegates_to and direct execution is absent", "prevents bypass by older callers", "tests/plugin_system/test_tool_manager_adapter.py", "P0"),
        RecommendedTest("production_policy_check", "production_policy_check.py", "Policy check consumes evidence bundle and legacy evidence with warnings.", "missing/weak production evidence is warn/fail as configured", "production gate behavior stays fail-closed", "tests/plugin_system/test_production_policy_check.py", "P1"),
        RecommendedTest("manifest_lint_templates", "manifest_lint.py", "All shipped templates lint with no structural errors.", "business templates export expected actor visibility and risk warnings", "keeps examples usable and safe", "tests/plugin_system/test_templates.py", "P1"),
    ]


def test_plan_report() -> dict[str, Any]:
    tests = recommended_tests()
    return {
        "status": "success",
        "recommended_tests": [item.to_dict() for item in tests],
        "total": len(tests),
        "by_priority": {
            priority: sum(1 for item in tests if item.priority == priority)
            for priority in sorted({item.priority for item in tests})
        },
        "generated_at": utc_now(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recommended plugin-system test migration plan")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    report = test_plan_report()
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
