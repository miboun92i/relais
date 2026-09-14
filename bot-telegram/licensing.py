"""Signed commercial licenses for Relais.

The vendor keeps the Ed25519 private key offline. Deployments receive only the
public key, so a customer cannot mint or alter licenses.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


class LicenseError(ValueError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def installation_id() -> str:
    explicit = os.getenv("INSTALLATION_ID", "").strip()
    if explicit:
        return explicit
    raw = f"{platform.node()}:{uuid.getnode()}".encode()
    return hashlib.sha256(raw).hexdigest()[:32]


@dataclass(frozen=True)
class LicenseClaims:
    license_id: str
    customer_id: str
    plan: str
    max_accounts: int
    expires_at: str | None
    installation_id: str | None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LicenseClaims":
        required = {"license_id", "customer_id", "plan", "max_accounts"}
        missing = required - set(data)
        if missing:
            raise LicenseError(f"Licence incomplète: {', '.join(sorted(missing))}")
        max_accounts = data["max_accounts"]
        if type(max_accounts) is not int or not 1 <= max_accounts <= 100:
            raise LicenseError("Nombre de comptes autorisés invalide.")
        return cls(str(data["license_id"]), str(data["customer_id"]), str(data["plan"]), max_accounts,
                   data.get("expires_at"), data.get("installation_id"))

    def assert_valid(self, current_installation_id: str | None = None) -> None:
        if self.expires_at:
            try:
                expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise LicenseError("Date d'expiration de licence invalide.") from exc
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= expiry.astimezone(timezone.utc):
                raise LicenseError("Licence expirée.")
        if self.installation_id and current_installation_id and self.installation_id != current_installation_id:
            raise LicenseError("Cette licence appartient à une autre installation.")


def sign_license(payload: dict[str, Any], private_key_b64: str) -> str:
    try:
        key = Ed25519PrivateKey.from_private_bytes(_b64d(private_key_b64))
    except Exception as exc:
        raise LicenseError("Clé privée de licence invalide.") from exc
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    body64 = _b64e(body)
    return f"{body64}.{_b64e(key.sign(body64.encode()))}"


def verify_license(token: str, public_key_b64: str, bind_installation: bool = True) -> LicenseClaims:
    try:
        body64, signature64 = token.split(".", 1)
        public_key = Ed25519PublicKey.from_public_bytes(_b64d(public_key_b64))
        public_key.verify(_b64d(signature64), body64.encode())
    except (ValueError, InvalidSignature) as exc:
        raise LicenseError("Signature ou format de licence invalide.") from exc
    except Exception as exc:
        raise LicenseError("Clé publique de licence invalide.") from exc
    try:
        data = json.loads(_b64d(body64))
    except Exception as exc:
        raise LicenseError("Contenu de licence invalide.") from exc
    claims = LicenseClaims.from_dict(data)
    claims.assert_valid(installation_id() if bind_installation else None)
    return claims


def load_license_from_env(required: bool = False) -> LicenseClaims | None:
    token = os.getenv("LICENSE_TOKEN", "").strip()
    public_key = os.getenv("LICENSE_PUBLIC_KEY", "").strip()
    if not token and not required:
        return None
    if not token or not public_key:
        raise LicenseError("LICENSE_TOKEN et LICENSE_PUBLIC_KEY sont requis.")
    return verify_license(token, public_key, os.getenv("LICENSE_BIND_INSTALLATION", "1") != "0")
