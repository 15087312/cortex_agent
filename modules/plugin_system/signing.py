from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

SIGNATURE_ALGORITHM = "Ed25519-SHA256"
LEGACY_SIGNATURE_ALGORITHM = "HMAC-SHA256"  # TECH DEBT: 生产模式已阻断 HMAC 签名插件，dev 模式仍接受
SUPPORTED_SIGNATURE_ALGORITHMS = {SIGNATURE_ALGORITHM, LEGACY_SIGNATURE_ALGORITHM}
SIGNING_KEY_ENV = "PLUGIN_SIGNING_KEY"
PUBLIC_KEY_ENV = "PLUGIN_SIGNING_PUBLIC_KEY"
TRUST_STORE_VERSION = 1


class PluginSignatureError(ValueError):
    """Raised when a plugin package signature is missing or invalid."""


class TrustStore:
    """JSON trust store for publisher Ed25519 public keys."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def add_key(self, publisher: str, public_key: str | Path) -> str:
        publisher = normalize_publisher(publisher)
        public = _load_public_key(public_key)
        public_pem = public_key_pem(public)
        key_id = public_key_id(public)
        payload = self._read()
        publisher_entry = payload.setdefault("publishers", {}).setdefault(
            publisher,
            {
                "keys": {},
            },
        )
        publisher_entry.setdefault("keys", {})[key_id] = {
            "public_key": public_pem,
            "algorithm": SIGNATURE_ALGORITHM,
            "status": "trusted",
            "added_at": datetime.now(UTC).isoformat(),
        }
        self._write(payload)
        return key_id

    def revoke_key(self, publisher: str, key_id: str) -> None:
        publisher = normalize_publisher(publisher)
        payload = self._read()
        key_entry = self._key_entry(payload, publisher, key_id)
        key_entry["status"] = "revoked"
        key_entry["revoked_at"] = datetime.now(UTC).isoformat()
        self._write(payload)

    def trusted_public_key(self, publisher: str, key_id: str) -> str:
        publisher = normalize_publisher(publisher)
        payload = self._read()
        key_entry = self._key_entry(payload, publisher, key_id)
        if key_entry.get("status") != "trusted":
            raise PluginSignatureError(f"publisher key is not trusted: {publisher}/{key_id}")
        if key_entry.get("algorithm") != SIGNATURE_ALGORITHM:
            raise PluginSignatureError(f"unsupported publisher key algorithm: {key_entry.get('algorithm')}")
        public_key = str(key_entry.get("public_key", ""))
        if not public_key:
            raise PluginSignatureError(f"publisher key is missing public key material: {publisher}/{key_id}")
        return public_key

    def entries(self) -> dict[str, Any]:
        return self._read().get("publishers", {})

    def _key_entry(self, payload: dict[str, Any], publisher: str, key_id: str) -> dict[str, Any]:
        try:
            key_entry = payload["publishers"][publisher]["keys"][key_id]
        except KeyError as exc:
            raise PluginSignatureError(f"publisher key is not trusted: {publisher}/{key_id}") from exc
        if not isinstance(key_entry, dict):
            raise PluginSignatureError(f"publisher key entry is invalid: {publisher}/{key_id}")
        return key_entry

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "version": TRUST_STORE_VERSION,
                "publishers": {},
            }
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PluginSignatureError(f"invalid trust store: {exc}") from exc
        if not isinstance(payload, dict):
            raise PluginSignatureError("trust store must be a JSON object")
        if payload.get("version") != TRUST_STORE_VERSION:
            raise PluginSignatureError(f"unsupported trust store version: {payload.get('version')}")
        if not isinstance(payload.get("publishers"), dict):
            raise PluginSignatureError("trust store publishers must be an object")
        return payload

    def _write(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def generate_keypair(
    private_key_path: str | Path,
    public_key_path: str | Path,
    *,
    overwrite: bool = False,
) -> tuple[Path, Path]:
    private_path = Path(private_key_path).resolve()
    public_path = Path(public_key_path).resolve()
    if not overwrite:
        for path in [private_path, public_path]:
            if path.exists():
                raise PluginSignatureError(f"key file already exists: {path}")
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    private_path.parent.mkdir(parents=True, exist_ok=True)
    public_path.parent.mkdir(parents=True, exist_ok=True)
    private_path.write_bytes(
        private_key.private_bytes(
            encoding=Encoding.PEM,
            format=PrivateFormat.PKCS8,
            encryption_algorithm=NoEncryption(),
        )
    )
    public_path.write_bytes(
        public_key.public_bytes(
            encoding=Encoding.PEM,
            format=PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def resolve_signing_key(key: str | None = None) -> bytes:
    signing_key = key or os.environ.get(SIGNING_KEY_ENV)
    if not signing_key:
        raise PluginSignatureError(f"missing signing key; pass --hmac-key or set {SIGNING_KEY_ENV}")
    return signing_key.encode("utf-8")


def resolve_public_key(key: str | None = None) -> str:
    public_key = key or os.environ.get(PUBLIC_KEY_ENV)
    if not public_key:
        raise PluginSignatureError(f"missing public key; pass --public-key or set {PUBLIC_KEY_ENV}")
    return public_key


def normalize_publisher(publisher: str | None) -> str:
    value = " ".join(str(publisher or "").strip().split())
    if not value:
        raise PluginSignatureError("publisher is required")
    if len(value) > 128 or any(char in value for char in ["/", "\\", "\n", "\r", "\t"]):
        raise PluginSignatureError(f"invalid publisher: {publisher}")
    return value


def public_key_pem(public_key: Ed25519PublicKey) -> str:
    return public_key.public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")


def public_key_id(public_key: Ed25519PublicKey) -> str:
    key_bytes = public_key.public_bytes(
        encoding=Encoding.DER,
        format=PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(key_bytes).hexdigest()


def sign_package(
    package: str | Path,
    key: str | None = None,
    signature_path: str | Path | None = None,
    *,
    private_key: str | Path | None = None,
    publisher: str | None = None,
    key_id: str | None = None,
) -> Path:
    if private_key is None:
        return sign_package_hmac(package, key=key, signature_path=signature_path, publisher=publisher)

    package_path = _package_path(package)
    digest = sha256_file(package_path)
    signing_key = _load_private_key(private_key)
    publisher_name = normalize_publisher(publisher)
    signing_key_id = key_id or public_key_id(signing_key.public_key())
    signature = signing_key.sign(digest.encode("ascii"))
    output_path = Path(signature_path).resolve() if signature_path else Path(str(package_path) + ".sig")
    payload = {
        "algorithm": SIGNATURE_ALGORITHM,
        "hash": digest,
        "key_id": signing_key_id,
        "publisher": publisher_name,
        "signature": base64.b64encode(signature).decode("ascii"),
        "signature_encoding": "base64",
        "created_at": datetime.now(UTC).isoformat(),
    }
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def sign_package_hmac(
    package: str | Path,
    key: str | None = None,
    signature_path: str | Path | None = None,
    *,
    publisher: str | None = None,
) -> Path:
    package_path = _package_path(package)
    digest = sha256_file(package_path)
    signature = hmac.new(resolve_signing_key(key), digest.encode("ascii"), hashlib.sha256).hexdigest()
    output_path = Path(signature_path).resolve() if signature_path else Path(str(package_path) + ".sig")
    payload = {
        "algorithm": LEGACY_SIGNATURE_ALGORITHM,
        "hash": digest,
        "signature": signature,
        "created_at": datetime.now(UTC).isoformat(),
    }
    if publisher:
        payload["publisher"] = publisher
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return output_path


def verify_signature(
    package: str | Path,
    signature: str | Path | None = None,
    key: str | None = None,
    *,
    public_key: str | Path | None = None,
    trust_store: str | Path | None = None,
) -> dict[str, Any]:
    package_path = _package_path(package)
    signature_path = Path(signature).resolve() if signature else Path(str(package_path) + ".sig")
    if not signature_path.exists() or not signature_path.is_file():
        raise PluginSignatureError(f"signature file not found: {signature_path}")
    payload = _read_signature_payload(signature_path)
    algorithm = payload.get("algorithm")
    if algorithm == LEGACY_SIGNATURE_ALGORITHM:
        return _verify_hmac_payload(package_path, payload, key)
    if algorithm == SIGNATURE_ALGORITHM:
        return _verify_ed25519_payload(package_path, payload, public_key, trust_store)
    raise PluginSignatureError(f"unsupported signature algorithm: {algorithm}")


def verify_signature_data(
    data: bytes,
    signature: str | Path | bytes,
    key: str | None = None,
    *,
    public_key: str | Path | None = None,
    trust_store: str | Path | None = None,
) -> dict[str, Any]:
    payload = _read_signature_bytes(signature) if isinstance(signature, bytes) else _read_signature_payload(Path(signature).resolve())
    expected_hash = hashlib.sha256(data).hexdigest()
    algorithm = payload.get("algorithm")
    if algorithm == LEGACY_SIGNATURE_ALGORITHM:
        return _verify_hmac_digest(expected_hash, payload, key)
    if algorithm == SIGNATURE_ALGORITHM:
        return _verify_ed25519_digest(expected_hash, payload, public_key, trust_store)
    raise PluginSignatureError(f"unsupported signature algorithm: {algorithm}")


def _verify_hmac_payload(package_path: Path, payload: dict[str, Any], key: str | None) -> dict[str, Any]:
    expected_hash = sha256_file(package_path)
    return _verify_hmac_digest(expected_hash, payload, key)


def _verify_hmac_digest(expected_hash: str, payload: dict[str, Any], key: str | None) -> dict[str, Any]:
    if payload.get("hash") != expected_hash:
        raise PluginSignatureError("package hash does not match signature payload")
    expected_signature = hmac.new(resolve_signing_key(key), expected_hash.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(str(payload.get("signature", "")), expected_signature):
        raise PluginSignatureError("invalid package signature")
    return payload


def _verify_ed25519_payload(
    package_path: Path,
    payload: dict[str, Any],
    public_key: str | Path | None,
    trust_store: str | Path | None,
) -> dict[str, Any]:
    expected_hash = sha256_file(package_path)
    return _verify_ed25519_digest(expected_hash, payload, public_key, trust_store)


def _verify_ed25519_digest(
    expected_hash: str,
    payload: dict[str, Any],
    public_key: str | Path | None,
    trust_store: str | Path | None,
) -> dict[str, Any]:
    if payload.get("hash") != expected_hash:
        raise PluginSignatureError("package hash does not match signature payload")
    if payload.get("signature_encoding") != "base64":
        raise PluginSignatureError("unsupported signature encoding")
    try:
        signature = base64.b64decode(str(payload.get("signature", "")), validate=True)
    except ValueError as exc:
        raise PluginSignatureError("invalid package signature encoding") from exc
    verification_key = _load_public_key(resolve_verification_public_key(payload, public_key, trust_store))
    try:
        verification_key.verify(signature, expected_hash.encode("ascii"))
    except InvalidSignature as exc:
        raise PluginSignatureError("invalid package signature") from exc
    return payload


def resolve_verification_public_key(
    payload: dict[str, Any],
    public_key: str | Path | None,
    trust_store: str | Path | None,
) -> str:
    if public_key is not None:
        return str(public_key)
    if trust_store is not None:
        publisher = normalize_publisher(str(payload.get("publisher", "")))
        key_id = str(payload.get("key_id", "")).strip()
        if not key_id:
            raise PluginSignatureError("signature payload is missing key_id")
        return TrustStore(trust_store).trusted_public_key(publisher, key_id)
    return resolve_public_key(None)


def _package_path(package: str | Path) -> Path:
    package_path = Path(package).resolve()
    if not package_path.exists() or not package_path.is_file():
        raise PluginSignatureError(f"package not found: {package_path}")
    return package_path


def _read_signature_payload(signature_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(signature_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PluginSignatureError(f"invalid signature payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise PluginSignatureError("signature payload must be a JSON object")
    return payload


def _read_signature_bytes(signature: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(signature.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PluginSignatureError(f"invalid signature payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise PluginSignatureError("signature payload must be a JSON object")
    return payload


def _load_private_key(private_key: str | Path) -> Ed25519PrivateKey:
    key_bytes = _read_key_material(private_key)
    try:
        key = load_pem_private_key(key_bytes, password=None)
    except ValueError as exc:
        raise PluginSignatureError(f"invalid Ed25519 private key: {exc}") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise PluginSignatureError("private key must be an Ed25519 key")
    return key


def _load_public_key(public_key: str | Path) -> Ed25519PublicKey:
    key_bytes = _read_key_material(public_key)
    try:
        key = load_pem_public_key(key_bytes)
    except ValueError as exc:
        raise PluginSignatureError(f"invalid Ed25519 public key: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise PluginSignatureError("public key must be an Ed25519 key")
    return key


def _read_key_material(value: str | Path) -> bytes:
    text = str(value)
    path = Path(text)
    if path.exists() and path.is_file():
        return path.read_bytes()
    if "BEGIN" in text:
        return text.encode("utf-8")
    raise PluginSignatureError(f"key file not found: {text}")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
