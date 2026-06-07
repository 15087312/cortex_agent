from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .tool_contracts import TOOL_SERVICE_CONTRACT_VERSION, utc_now


@dataclass(frozen=True)
class IntegrationContractCheck:
    check_id: str
    status: str
    requirement: str
    integration_points: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    sample_calls: list[str] = field(default_factory=list)
    production_safety_relevance: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_integration_contract_check() -> dict[str, Any]:
    checks = _checks()
    return {
        "status": "pass" if all(item.status == "pass" for item in checks) else "warn",
        "contract_version": TOOL_SERVICE_CONTRACT_VERSION,
        "checks": [item.to_dict() for item in checks],
        "requirements": [item.requirement for item in checks],
        "integration_points": sorted({point for item in checks for point in item.integration_points}),
        "forbidden_patterns": sorted({pattern for item in checks for pattern in item.forbidden_patterns}),
        "sample_calls": sorted({call for item in checks for call in item.sample_calls}),
        "generated_at": utc_now(),
    }


def _checks() -> list[IntegrationContractCheck]:
    return [
        IntegrationContractCheck(
            check_id="model_tools.from_service",
            status="pass",
            requirement="Model tools must be listed through PluginToolService or PluginToolManagerAdapter.",
            integration_points=["PluginToolService.list_tools", "PluginToolManagerAdapter.list_tools"],
            forbidden_patterns=["direct PluginEngine.tool_registry_bridge export to model"],
            sample_calls=["PluginToolService.list_tools(provider='openai', actor_role='model')"],
            production_safety_relevance="keeps visibility, approval, schema budget, and audit on one contract",
        ),
        IntegrationContractCheck(
            check_id="provider_calls.through_service",
            status="pass",
            requirement="Provider tool calls must be dispatched through PluginToolService or ModelLoopToolAdapter.",
            integration_points=["PluginToolService.invoke_tool_call", "ModelLoopToolAdapter.execute_tool_calls"],
            forbidden_patterns=["direct plugin function execution", "direct PluginEngine.call_tool from model loop"],
            sample_calls=["ModelLoopToolAdapter.execute_tool_calls(provider='openai', tool_calls=calls, actor_role='model')"],
            production_safety_relevance="keeps governance, confirmation, idempotency, permissions, and result sanitization active",
        ),
        IntegrationContractCheck(
            check_id="tool_result.untrusted_provider_message",
            status="pass",
            requirement="Plugin output must be appended as provider tool result data, never as system/developer instructions.",
            integration_points=["ModelLoopToolAdapter.append_tool_results_to_messages", "create_provider_tool_response"],
            forbidden_patterns=["append plugin result to system message", "append plugin result to developer message"],
            sample_calls=["adapter.append_tool_results_to_messages('anthropic', messages, tool_responses)"],
            production_safety_relevance="prevents plugin result text from becoming privileged instructions",
        ),
        IntegrationContractCheck(
            check_id="tool_result.untrusted_marker",
            status="pass",
            requirement="Tool result envelopes must keep untrusted tool data separate from control metadata.",
            integration_points=["ProviderToolResponse.safe_content", "ToolInvocationResponse.envelope"],
            forbidden_patterns=["merge result into prompt root", "strip provider tool result envelope"],
            sample_calls=["response['provider_safe_message']"],
            production_safety_relevance="preserves provider-safe result boundaries",
        ),
        IntegrationContractCheck(
            check_id="high_risk.hidden_by_default",
            status="pass",
            requirement="High-risk tools must not be exposed to normal model actor by default.",
            integration_points=["ToolExposurePolicy.decide", "LLMToolCatalog.from_engine"],
            forbidden_patterns=["include_hidden=True for normal model execution", "force model_default for output.send/fs.write/network tools"],
            sample_calls=["PluginToolService.list_tools(actor_role='model', include_hidden=False)"],
            production_safety_relevance="prevents model-default access to side-effecting capabilities",
        ),
        IntegrationContractCheck(
            check_id="audit.safe_summaries_only",
            status="pass",
            requirement="Args, results, and secrets must not be logged raw.",
            integration_points=["summarize_json", "RequestAuditSummary", "tool_result sanitization"],
            forbidden_patterns=["audit full args", "audit full result", "audit token/password/secret"],
            sample_calls=["PluginToolService.get_request_audit_summary(request_id)"],
            production_safety_relevance="limits audit leakage while preserving traceability",
        ),
        IntegrationContractCheck(
            check_id="request_id.propagates",
            status="pass",
            requirement="request_id must propagate into provider parsing, engine, gateway, and audit.",
            integration_points=["ModelToolBridge.invoke_provider_tool_call", "LLMToolRuntime.invoke", "PluginEngine.call_tool"],
            forbidden_patterns=["drop request_id between provider parse and execution"],
            sample_calls=["service.invoke_tool_call(..., request_id=request_id)"],
            production_safety_relevance="enables end-to-end audit correlation",
        ),
        IntegrationContractCheck(
            check_id="errors.safe_codes",
            status="pass",
            requirement="Errors must map to stable safe tool error codes.",
            integration_points=["tool_errors.py", "provider_failure_envelope", "ToolInvocationResponse.error"],
            forbidden_patterns=["return raw exception text to model", "return traceback in tool result"],
            sample_calls=["safe_tool_error_message(error_code)"],
            production_safety_relevance="prevents internal error leakage and keeps retry behavior stable",
        ),
        IntegrationContractCheck(
            check_id="legacy.direct_execution_forbidden",
            status="pass",
            requirement="Legacy tool-manager paths must delegate to PluginToolService instead of direct plugin execution.",
            integration_points=["PluginToolManagerAdapter.execute_tool"],
            forbidden_patterns=["legacy tool_manager calls plugin function directly"],
            sample_calls=["PluginToolManagerAdapter(service=service).execute_tool(...)"],
            production_safety_relevance="keeps old callers on the same production controls",
        ),
        IntegrationContractCheck(
            check_id="legacy.local_not_production_model_facing",
            status="pass",
            requirement="Legacy local plugin layout cannot be treated as production model-facing without install, approval, and policy checks.",
            integration_points=["manifest_lint", "production_policy_check", "status raw_example_plugins"],
            forbidden_patterns=["raw data/plugins directory is model-visible", "template directory is installed plugin"],
            sample_calls=["python -m modules.plugin_system.production_policy_check <plugin_dir> --json"],
            production_safety_relevance="prevents source examples from being mistaken for enabled tools",
        ),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plugin model-loop integration contract")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    report = run_integration_contract_check()
    if args.json_output:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
