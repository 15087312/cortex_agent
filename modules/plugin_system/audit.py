from __future__ import annotations

import base64
import json
import hashlib
import threading
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

from .signing import public_key_id


def new_request_id() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class AuditRecord:
    event: str
    result: str
    request_id: str
    plugin: str | None = None
    action: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    timestamp: str | None = None
    plugin_id: str | None = None
    plugin_version: str | None = None
    resource: str | None = None
    permission: str | None = None
    decision: str | None = None
    reason: str | None = None
    actor: str | None = None
    previous_hash: str | None = None
    current_hash: str | None = None

    def __post_init__(self) -> None:
        details = self.details if isinstance(self.details, dict) else {}
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", self.created_at)
        if self.plugin_id is None and self.plugin is not None:
            object.__setattr__(self, "plugin_id", self.plugin)
        if self.plugin_version is None and details.get("version") is not None:
            object.__setattr__(self, "plugin_version", str(details.get("version")))
        if self.resource is None:
            resource = _infer_resource(details)
            if resource is not None:
                object.__setattr__(self, "resource", resource)
        if self.permission is None:
            permission = _infer_permission(self.action, details)
            if permission is not None:
                object.__setattr__(self, "permission", permission)
        if self.decision is None:
            object.__setattr__(self, "decision", _infer_decision(self.result, details))
        if self.reason is None:
            reason = _infer_reason(details)
            if reason is not None:
                object.__setattr__(self, "reason", reason)
        if self.actor is None:
            object.__setattr__(self, "actor", str(details.get("actor") or details.get("reviewer") or "system"))


class AuditLogIntegrityError(ValueError):
    """Raised when the audit JSONL hash chain cannot be verified."""


@dataclass(frozen=True)
class AuditCheckpoint:
    latest_sequence: int
    latest_hash: str
    timestamp: str
    audit_log_path: str
    signer: str | None = None
    key_id: str | None = None
    signature: str | None = None
    signature_encoding: str | None = None


class AuditSink(Protocol):
    def append(self, event: dict[str, Any]) -> AuditRecord:
        ...

    def flush(self) -> None:
        ...

    def verify(self) -> dict[str, Any]:
        ...

    def checkpoint(self) -> AuditCheckpoint:
        ...


class CheckpointAnchor(Protocol):
    def write_checkpoint(self, checkpoint: AuditCheckpoint) -> None:
        ...

    def read_latest_checkpoint(self) -> AuditCheckpoint | None:
        ...

    def verify_checkpoint(self, *, public_key: str | Path | None = None) -> dict[str, Any]:
        ...


class AuditLogger:
    """Append-only JSONL audit trail for plugin lifecycle and capability access."""

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path).resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(
        self,
        event: str,
        result: str,
        *,
        request_id: str | None = None,
        plugin: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
        plugin_id: str | None = None,
        plugin_version: str | None = None,
        resource: str | None = None,
        permission: str | None = None,
        decision: str | None = None,
        reason: str | None = None,
        actor: str | None = None,
    ) -> AuditRecord:
        with self._lock:
            previous_hash = self._last_current_hash()
            record = AuditRecord(
                event=event,
                result=result,
                request_id=request_id or new_request_id(),
                plugin=plugin,
                action=action,
                details=details or {},
                plugin_id=plugin_id,
                plugin_version=plugin_version,
                resource=resource,
                permission=permission,
                decision=decision,
                reason=reason,
                actor=actor,
                previous_hash=previous_hash,
            )
            record = replace(record, current_hash=_hash_record(record))
            line = json.dumps(asdict(record), sort_keys=True, separators=(",", ":"))
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        return record

    def read_records(self) -> list[AuditRecord]:
        if not self.log_path.exists():
            return []
        records: list[AuditRecord] = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            records.append(AuditRecord(**payload))
        return records

    def verify(
        self,
        *,
        anchor: CheckpointAnchor | None = None,
        public_key: str | Path | None = None,
    ) -> dict[str, Any]:
        return verify_audit_log(self.log_path, anchor=anchor, public_key=public_key)

    def _last_current_hash(self) -> str:
        if not self.log_path.exists():
            return ""
        try:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return ""
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                return ""
            current_hash = payload.get("current_hash")
            return current_hash if isinstance(current_hash, str) else ""
        return ""


class NullAuditLogger:
    def record(
        self,
        event: str,
        result: str,
        *,
        request_id: str | None = None,
        plugin: str | None = None,
        action: str | None = None,
        details: dict[str, Any] | None = None,
        plugin_id: str | None = None,
        plugin_version: str | None = None,
        resource: str | None = None,
        permission: str | None = None,
        decision: str | None = None,
        reason: str | None = None,
        actor: str | None = None,
    ) -> AuditRecord:
        return AuditRecord(
            event=event,
            result=result,
            request_id=request_id or new_request_id(),
            plugin=plugin,
            action=action,
            details=details or {},
            plugin_id=plugin_id,
            plugin_version=plugin_version,
            resource=resource,
            permission=permission,
            decision=decision,
            reason=reason,
            actor=actor,
        )

    def read_records(self) -> list[AuditRecord]:
        return []


global_audit_logger = NullAuditLogger()


class LocalHashChainAuditSink:
    """JSONL hash-chain audit sink with optional local checkpoint anchoring.

    The local sink is tamper-evident, not tamper-proof. Production should anchor
    checkpoints into append-only storage, SIEM, WORM buckets, or transparency logs.
    """

    def __init__(
        self,
        log_path: str | Path,
        *,
        anchor: CheckpointAnchor | None = None,
        checkpoint_interval: int = 0,
    ) -> None:
        self.logger = AuditLogger(log_path)
        self.anchor = anchor
        self.checkpoint_interval = max(0, checkpoint_interval)

    @property
    def log_path(self) -> Path:
        return self.logger.log_path

    def append(self, event: dict[str, Any]) -> AuditRecord:
        record = self.logger.record(
            str(event.get("event", "audit.event")),
            str(event.get("result", "success")),
            request_id=event.get("request_id"),
            plugin=event.get("plugin"),
            action=event.get("action"),
            details=event.get("details") if isinstance(event.get("details"), dict) else {},
            plugin_id=event.get("plugin_id"),
            plugin_version=event.get("plugin_version"),
            resource=event.get("resource"),
            permission=event.get("permission"),
            decision=event.get("decision"),
            reason=event.get("reason"),
            actor=event.get("actor"),
        )
        if self.anchor and self.checkpoint_interval:
            sequence = _audit_log_state(self.log_path)["records"]
            if sequence and sequence % self.checkpoint_interval == 0:
                self.checkpoint()
        return record

    def flush(self) -> None:
        if self.anchor:
            self.checkpoint()

    def verify(self) -> dict[str, Any]:
        return verify_audit_log(self.log_path, anchor=self.anchor)

    def checkpoint(self) -> AuditCheckpoint:
        checkpoint = create_audit_checkpoint(self.log_path)
        if self.anchor:
            self.anchor.write_checkpoint(checkpoint)
        return checkpoint


class LocalCheckpointAnchor:
    """Local checkpoint anchor for tests and deployment smoke checks.

    Local files do not provide real append-only guarantees. This anchor detects
    rollback when the checkpoint file is preserved separately from the log.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        private_key: str | Path | None = None,
        public_key: str | Path | None = None,
        signer: str | None = None,
    ) -> None:
        self.path = Path(path).resolve()
        self.private_key = Path(private_key).resolve() if private_key is not None else None
        self.public_key = Path(public_key).resolve() if public_key is not None else None
        self.signer = signer

    def write_checkpoint(self, checkpoint: AuditCheckpoint) -> None:
        payload = asdict(checkpoint)
        if self.private_key is not None:
            private_key = _load_ed25519_private_key(self.private_key)
            unsigned = AuditCheckpoint(
                latest_sequence=checkpoint.latest_sequence,
                latest_hash=checkpoint.latest_hash,
                timestamp=checkpoint.timestamp,
                audit_log_path=checkpoint.audit_log_path,
                signer=checkpoint.signer or self.signer or "local-anchor",
                key_id=public_key_id(private_key.public_key()),
            )
            signature = private_key.sign(_canonical_checkpoint_bytes(unsigned))
            payload = asdict(unsigned)
            payload["signature"] = base64.b64encode(signature).decode("ascii")
            payload["signature_encoding"] = "base64"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def read_latest_checkpoint(self) -> AuditCheckpoint | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise AuditLogIntegrityError(f"invalid audit checkpoint: {exc}") from exc
        if not isinstance(payload, dict):
            raise AuditLogIntegrityError("audit checkpoint must be a JSON object")
        return AuditCheckpoint(**payload)

    def verify_checkpoint(self, *, public_key: str | Path | None = None) -> dict[str, Any]:
        checkpoint = self.read_latest_checkpoint()
        if checkpoint is None:
            return {"status": "skipped", "reason": "checkpoint_missing"}
        key_path = Path(public_key).resolve() if public_key is not None else self.public_key
        if checkpoint.signature:
            if key_path is None:
                raise AuditLogIntegrityError("audit checkpoint signature requires a public key")
            public = _load_ed25519_public_key(key_path)
            try:
                signature = base64.b64decode(checkpoint.signature, validate=True)
                public.verify(signature, _canonical_checkpoint_bytes(replace(checkpoint, signature=None, signature_encoding=None)))
            except (ValueError, InvalidSignature) as exc:
                raise AuditLogIntegrityError("audit checkpoint signature verification failed") from exc
        return {
            "status": "success",
            "latest_sequence": checkpoint.latest_sequence,
            "latest_hash": checkpoint.latest_hash,
            "timestamp": checkpoint.timestamp,
            "audit_log_path": checkpoint.audit_log_path,
            "signed": bool(checkpoint.signature),
        }


def verify_audit_log(
    log_path: str | Path,
    *,
    anchor: CheckpointAnchor | None = None,
    public_key: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(log_path).resolve()
    previous_hash = ""
    records = 0
    checkpoint_hashes: dict[int, str] = {}
    if not path.exists():
        records_report = {"status": "success", "records": 0, "last_hash": ""}
        return _verify_against_checkpoint(path, records_report, checkpoint_hashes, anchor, public_key)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AuditLogIntegrityError(f"audit log line {line_number} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise AuditLogIntegrityError(f"audit log line {line_number} must be a JSON object")
        if payload.get("previous_hash") != previous_hash:
            raise AuditLogIntegrityError(f"audit log hash chain break at line {line_number}")
        current_hash = payload.get("current_hash")
        if not isinstance(current_hash, str) or len(current_hash) != 64:
            raise AuditLogIntegrityError(f"audit log line {line_number} is missing current_hash")
        record = AuditRecord(**payload)
        expected_hash = _hash_record(record)
        if current_hash != expected_hash:
            raise AuditLogIntegrityError(f"audit log hash mismatch at line {line_number}")
        previous_hash = current_hash
        records += 1
        checkpoint_hashes[records] = current_hash
    records_report = {"status": "success", "records": records, "last_hash": previous_hash}
    return _verify_against_checkpoint(path, records_report, checkpoint_hashes, anchor, public_key)


def create_audit_checkpoint(log_path: str | Path) -> AuditCheckpoint:
    path = Path(log_path).resolve()
    state = _audit_log_state(path)
    return AuditCheckpoint(
        latest_sequence=state["records"],
        latest_hash=state["last_hash"],
        timestamp=datetime.now(UTC).isoformat(),
        audit_log_path=str(path),
    )


def _hash_record(record: AuditRecord) -> str:
    payload = asdict(record)
    payload["current_hash"] = None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _audit_log_state(path: Path) -> dict[str, Any]:
    report = verify_audit_log(path)
    return {"records": int(report["records"]), "last_hash": str(report["last_hash"])}


def _verify_against_checkpoint(
    path: Path,
    report: dict[str, Any],
    checkpoint_hashes: dict[int, str],
    anchor: CheckpointAnchor | None,
    public_key: str | Path | None,
) -> dict[str, Any]:
    if anchor is None:
        report["checkpoint"] = {"status": "skipped", "reason": "anchor_not_configured"}
        return report
    checkpoint_status = anchor.verify_checkpoint(public_key=public_key)
    checkpoint = anchor.read_latest_checkpoint()
    if checkpoint is None:
        report["checkpoint"] = checkpoint_status
        return report
    if Path(checkpoint.audit_log_path).resolve() != path:
        raise AuditLogIntegrityError("audit checkpoint references a different audit log")
    if report["records"] < checkpoint.latest_sequence:
        raise AuditLogIntegrityError(
            "audit log rollback detected: checkpoint sequence is ahead of audit log"
        )
    if checkpoint.latest_sequence == 0:
        expected_hash = ""
    else:
        expected_hash = checkpoint_hashes.get(checkpoint.latest_sequence, "")
    if expected_hash != checkpoint.latest_hash:
        raise AuditLogIntegrityError("audit log rollback detected: checkpoint hash does not match log sequence")
    report["checkpoint"] = checkpoint_status
    return report


def _canonical_checkpoint_bytes(checkpoint: AuditCheckpoint) -> bytes:
    payload = asdict(checkpoint)
    payload["signature"] = None
    payload["signature_encoding"] = None
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _load_ed25519_private_key(path: Path) -> Ed25519PrivateKey:
    key = load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise AuditLogIntegrityError("audit checkpoint private key must be Ed25519")
    return key


def _load_ed25519_public_key(path: Path) -> Ed25519PublicKey:
    key = load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise AuditLogIntegrityError("audit checkpoint public key must be Ed25519")
    return key


def _infer_resource(details: dict[str, Any]) -> str | None:
    for key in ["resource", "url", "path", "key", "channel", "event"]:
        if details.get(key) is not None:
            return str(details[key])
    return None


def _infer_permission(action: str | None, details: dict[str, Any]) -> str | None:
    if details.get("permission") is not None:
        return str(details["permission"])
    if action and "." in action:
        return action
    return None


def _infer_decision(result: str, details: dict[str, Any]) -> str:
    decision = details.get("decision")
    if decision in {"allow", "deny"}:
        return str(decision)
    return "allow" if result in {"success", "skipped"} else "deny"


def _infer_reason(details: dict[str, Any]) -> str | None:
    for key in ["reason", "error", "error_type"]:
        if details.get(key) is not None:
            return str(details[key])
    return None
