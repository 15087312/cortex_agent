from .audit import (
    AuditLogger,
    AuditLogIntegrityError,
    AuditRecord,
    AuditCheckpoint,
    LocalCheckpointAnchor,
    LocalHashChainAuditSink,
    NullAuditLogger,
    global_audit_logger,
    new_request_id,
    create_audit_checkpoint,
    verify_audit_log,
)
from .config import PluginConfig, PluginConfigError, PluginConfigManager
from .dependency import DependencyEnvironment, DependencyManager, PluginDependencyError
from .engine import PluginCircuitBreakerError, PluginEngine, PluginMemoryProviderError, PluginMiddlewareError
from .event_bus import Event, EventBus, global_event_bus
from .gateway import GatewayClient, PermissionDenied, PluginGateway, global_gateway
from .loader import PluginLoader, PluginPackageError, build_file_integrity_manifest, integrity_path_excluded
from .marketplace import (
    PluginRegistryClient,
    PluginRegistryError,
    RegistryEntry,
    RegistryIndex,
    RegistryInstallResult,
    load_registry_index,
)
from .models import (
    ExtensionDecl,
    ExtensionType,
    InstalledPlugin,
    PERMISSION_DESCRIPTIONS,
    PERMISSION_LEVELS,
    PERMISSION_RISK_LABELS,
    PermissionName,
    PluginStatus,
    PluginMetadata,
    PluginToolArgumentError,
    RequirementsDecl,
    RunMode,
    RuntimeDecl,
    ToolParamDecl,
    TrustLevel,
    permission_risk,
    permission_risks,
)
from .sandbox import SandboxManager, SandboxViolation
from .sandbox_backend import (
    BubblewrapBackend,
    EXTERNAL_SANDBOX_ATTESTATION_ENV,
    ExternalEnforcedBackend,
    SandboxBackend,
    SandboxBackendReport,
    WindowsJobBackend,
    create_sandbox_backend,
)
from .schema_validation import SchemaDefinitionError, SchemaValidationError, validate_json_schema, validate_json_value
from .signing import PluginSignatureError
from .sbom import PluginSbomError, generate_sbom, write_sbom
from .tool_adapter import (
    PluginToolRegistryBridge,
    plugin_model_tool_whitelist,
    plugin_tool_policy,
    plugin_tool_name_visible_to_model,
    plugin_tool_registry_entries,
    plugin_tool_visible_to_model,
    registered_tool_name,
    tool_params_for_api,
    tool_parameters_json_schema,
)
from .tool_result import PluginToolResultError, sanitize_tool_result, sanitize_tool_result_with_report
from .tool_security import RequestPermissionRegistry, ToolPermissionScope
from .policy import PluginPolicy, PolicyDecision, PolicyEngine, PolicyError
from .scanner import (
    Finding,
    OfflineLicenseScanner,
    OfflineVulnerabilityScanner,
    PluginScanError,
    ScanPolicy,
    ScanReport,
)


def run_tool_selftest(*args, **kwargs):
    from .tool_selftest import run_tool_selftest as _run_tool_selftest

    return _run_tool_selftest(*args, **kwargs)


def run_doctor(*args, **kwargs):
    from .doctor import run_doctor as _run_doctor

    return _run_doctor(*args, **kwargs)


def doctor_report(*args, **kwargs):
    from .doctor import doctor_report as _doctor_report

    return _doctor_report(*args, **kwargs)


def create_plugin_scaffold(*args, **kwargs):
    from .scaffold import create_plugin_scaffold as _create_plugin_scaffold

    return _create_plugin_scaffold(*args, **kwargs)


_LLM_TOOL_EXPORTS = {
    "LLMToolCatalog",
    "LLMToolExposure",
    "LLMToolRiskLevel",
    "LLMToolRuntime",
    "LLMToolSpec",
    "ToolExposureDecision",
    "ToolExposurePolicy",
    "llm_model_tool_name",
    "sanitize_tool_description",
    "tool_parameters_schema",
}

_PROVIDER_TOOL_EXPORTS = {
    "ModelToolBridge",
    "ModelToolCall",
    "ProviderName",
    "ProviderToolCallError",
    "ProviderToolExportOptions",
    "ProviderToolResponse",
    "ProviderToolSpec",
    "ToolNameMapping",
    "create_provider_tool_response",
    "export_provider_tools",
    "parse_model_tool_call",
    "parse_model_tool_calls",
    "provider_tool_name_valid",
}

_TOOL_GOVERNANCE_EXPORTS = {
    "ConfirmationDecision",
    "ConfirmationProvider",
    "ConfirmationRequirement",
    "ConfirmationRequest",
    "ConfirmationStatus",
    "ExternalGovernanceStore",
    "GovernanceStoreMetadata",
    "IdempotencyRecord",
    "FileToolCallSessionStore",
    "LocalConfirmationProvider",
    "ToolCallBudget",
    "ToolCallSession",
    "ToolCallSessionStore",
    "ToolExecutionDecision",
    "ToolExecutionMode",
    "ToolGovernanceController",
    "ToolGovernancePolicy",
    "ToolRiskDecision",
    "governance_failure_envelope",
    "governance_preview_envelope",
    "governance_store_metadata",
    "run_governance_selftest",
    "safe_governance_error_message",
    "stable_json_hash",
}

_TOOL_SERVICE_EXPORTS = {
    "PluginToolService",
    "run_tool_service_selftest",
    "service_contracts_schema",
}

_TOOL_MANAGER_ADAPTER_EXPORTS = {
    "PluginToolManagerAdapter",
    "run_tool_manager_adapter_selftest",
}

_MODEL_LOOP_ADAPTER_EXPORTS = {
    "ModelLoopToolAdapter",
    "run_model_loop_adapter_selftest",
}

_STATUS_EXPORTS = {
    "PluginSystemStatusProvider",
}

_PRODUCTION_POLICY_CHECK_EXPORTS = {
    "ProductionPolicyFinding",
    "run_production_policy_check",
}

_TOOL_CONTRACT_EXPORTS = {
    "TOOL_SERVICE_CONTRACT_VERSION",
    "RequestAuditSummary",
    "ToolInvocationResponse",
    "ToolListResponse",
    "ToolServiceCapabilities",
    "ToolServiceHealth",
    "ToolServiceMetrics",
    "ToolTraceContext",
}

_TOOL_ERROR_EXPORTS = {
    "ToolErrorCategory",
    "ToolErrorInfo",
    "normalize_tool_error_code",
    "safe_tool_error_message",
    "tool_error_catalog",
    "tool_error_info",
    "tool_error_payload",
}


def __getattr__(name):
    if name in _LLM_TOOL_EXPORTS:
        from . import llm_tools as _llm_tools

        return getattr(_llm_tools, name)
    if name in _PROVIDER_TOOL_EXPORTS:
        from . import provider_tools as _provider_tools

        return getattr(_provider_tools, name)
    if name in _TOOL_GOVERNANCE_EXPORTS:
        from . import tool_governance as _tool_governance

        return getattr(_tool_governance, name)
    if name in _TOOL_SERVICE_EXPORTS:
        from . import tool_service as _tool_service

        return getattr(_tool_service, name)
    if name in _TOOL_MANAGER_ADAPTER_EXPORTS:
        from . import tool_manager_adapter as _tool_manager_adapter

        return getattr(_tool_manager_adapter, name)
    if name in _MODEL_LOOP_ADAPTER_EXPORTS:
        from . import model_loop_adapter as _model_loop_adapter

        return getattr(_model_loop_adapter, name)
    if name in _STATUS_EXPORTS:
        from . import status as _status

        return getattr(_status, name)
    if name in _PRODUCTION_POLICY_CHECK_EXPORTS:
        from . import production_policy_check as _production_policy_check

        return getattr(_production_policy_check, name)
    if name in _TOOL_CONTRACT_EXPORTS:
        from . import tool_contracts as _tool_contracts

        return getattr(_tool_contracts, name)
    if name in _TOOL_ERROR_EXPORTS:
        from . import tool_errors as _tool_errors

        return getattr(_tool_errors, name)
    if name == "DoctorCheck":
        from .doctor import DoctorCheck as _DoctorCheck

        return _DoctorCheck
    if name == "PluginScaffoldError":
        from .scaffold import PluginScaffoldError as _PluginScaffoldError

        return _PluginScaffoldError
    raise AttributeError(name)

__all__ = [
    "Event",
    "EventBus",
    "AuditLogger",
    "AuditLogIntegrityError",
    "AuditRecord",
    "AuditCheckpoint",
    "LocalCheckpointAnchor",
    "LocalHashChainAuditSink",
    "DependencyEnvironment",
    "DependencyManager",
    "ExtensionDecl",
    "ExtensionType",
    "GatewayClient",
    "InstalledPlugin",
    "PERMISSION_DESCRIPTIONS",
    "PERMISSION_LEVELS",
    "PERMISSION_RISK_LABELS",
    "PermissionDenied",
    "PermissionName",
    "PluginConfig",
    "PluginConfigError",
    "PluginConfigManager",
    "PluginCircuitBreakerError",
    "PluginEngine",
    "PluginMiddlewareError",
    "PluginMemoryProviderError",
    "PluginRegistryClient",
    "PluginRegistryError",
    "PluginDependencyError",
    "PluginLoader",
    "LLMToolCatalog",
    "LLMToolExposure",
    "LLMToolRiskLevel",
    "LLMToolRuntime",
    "LLMToolSpec",
    "ModelToolBridge",
    "ModelToolCall",
    "PluginToolService",
    "PluginToolManagerAdapter",
    "ModelLoopToolAdapter",
    "PluginSystemStatusProvider",
    "ProductionPolicyFinding",
    "PluginMetadata",
    "PluginPackageError",
    "PluginGateway",
    "PluginSignatureError",
    "PluginSbomError",
    "PluginScaffoldError",
    "PluginStatus",
    "PluginToolArgumentError",
    "PluginToolResultError",
    "PluginToolRegistryBridge",
    "RequirementsDecl",
    "RegistryEntry",
    "RegistryIndex",
    "RegistryInstallResult",
    "RunMode",
    "RuntimeDecl",
    "SandboxManager",
    "SandboxViolation",
    "SandboxBackend",
    "SandboxBackendReport",
    "SchemaDefinitionError",
    "SchemaValidationError",
    "TrustLevel",
    "ToolParamDecl",
    "ToolExposureDecision",
    "ToolExposurePolicy",
    "ToolPermissionScope",
    "ToolNameMapping",
    "ToolCallBudget",
    "ToolErrorCategory",
    "ToolErrorInfo",
    "FileToolCallSessionStore",
    "RequestAuditSummary",
    "ToolInvocationResponse",
    "ToolListResponse",
    "ToolServiceCapabilities",
    "ToolServiceHealth",
    "ToolServiceMetrics",
    "ToolTraceContext",
    "ToolCallSession",
    "ToolCallSessionStore",
    "ToolExecutionDecision",
    "ToolExecutionMode",
    "ToolGovernanceController",
    "ToolGovernancePolicy",
    "ToolRiskDecision",
    "ConfirmationDecision",
    "ConfirmationProvider",
    "ConfirmationRequirement",
    "ConfirmationRequest",
    "ConfirmationStatus",
    "ExternalGovernanceStore",
    "GovernanceStoreMetadata",
    "LocalConfirmationProvider",
    "IdempotencyRecord",
    "TOOL_SERVICE_CONTRACT_VERSION",
    "NullAuditLogger",
    "EXTERNAL_SANDBOX_ATTESTATION_ENV",
    "BubblewrapBackend",
    "ExternalEnforcedBackend",
    "WindowsJobBackend",
    "create_sandbox_backend",
    "build_file_integrity_manifest",
    "create_plugin_scaffold",
    "global_event_bus",
    "global_audit_logger",
    "global_gateway",
    "integrity_path_excluded",
    "load_registry_index",
    "llm_model_tool_name",
    "new_request_id",
    "ProviderName",
    "ProviderToolCallError",
    "ProviderToolExportOptions",
    "ProviderToolResponse",
    "ProviderToolSpec",
    "create_audit_checkpoint",
    "verify_audit_log",
    "PluginPolicy",
    "PolicyDecision",
    "PolicyEngine",
    "PolicyError",
    "Finding",
    "OfflineLicenseScanner",
    "OfflineVulnerabilityScanner",
    "PluginScanError",
    "ScanPolicy",
    "ScanReport",
    "permission_risk",
    "permission_risks",
    "plugin_tool_policy",
    "plugin_tool_name_visible_to_model",
    "plugin_tool_registry_entries",
    "plugin_tool_visible_to_model",
    "plugin_model_tool_whitelist",
    "parse_model_tool_call",
    "registered_tool_name",
    "run_tool_selftest",
    "RequestPermissionRegistry",
    "sanitize_tool_result",
    "sanitize_tool_description",
    "sanitize_tool_result_with_report",
    "tool_params_for_api",
    "tool_parameters_schema",
    "tool_parameters_json_schema",
    "create_provider_tool_response",
    "export_provider_tools",
    "provider_tool_name_valid",
    "governance_failure_envelope",
    "governance_preview_envelope",
    "run_governance_selftest",
    "safe_governance_error_message",
    "stable_json_hash",
    "normalize_tool_error_code",
    "safe_tool_error_message",
    "service_contracts_schema",
    "run_tool_service_selftest",
    "run_tool_manager_adapter_selftest",
    "run_model_loop_adapter_selftest",
    "run_production_policy_check",
    "tool_error_catalog",
    "tool_error_info",
    "tool_error_payload",
    "generate_sbom",
    "write_sbom",
    "DoctorCheck",
    "run_doctor",
    "doctor_report",
    "validate_json_schema",
    "validate_json_value",
]
