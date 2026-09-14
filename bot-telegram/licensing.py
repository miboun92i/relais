"""Commercial license primitives for Relais.

Licenses are signed with HMAC-SHA256. The signing secret belongs to the vendor and
must never be shipped to customers. Customer deployments only need the signed
license token and validate it with LICENSE_VERIFY_SECRET in the hosted SaaS setup.
For a fully distributed/offline edition, replace HMAC with an asymmetric signature.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import platform
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class LicenseError(ValueError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def installation_id() -> str:
    """Stable non-secret identifier used to bind a license to one installation."""
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
        if type(max_accounts) is not int or max_accounts < 1 or max_accounts > 100:
            raise LicenseError("Nombre de comptes autorisés invalide.")
        return cls(
            license_id=str(data["license_id"]),
            customer_id=str(data["customer_id"]),
            plan=str(data["plan"]),
            max_accounts=max_accounts,
            expires_at=data.get("expires_at"),
            installation_id=data.get("installation_id"),
        )

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
        if self.installation_id and current_installation_id:
            if not hmac.compare_digest(self.installation_id, current_installation_id):
                raise LicenseError("Cette licence appartient à une autre installation.")


def sign_license(payload: dict[str, Any], secret: str) -> str:
    """Vendor-side helper. Do not expose the signing secret to customers."""
    if len(secret) < 32:
        raise LicenseError("Le secret de signature doit contenir au moins 32 caractères.")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    body64 = _b64e(body)
    signature = hmac.new(secret.encode(), body64.encode(), hashlib.sha256).digest()
    return f"{body64}.{_b64e(signature)}"


def verify_license(token: str, secret: str, bind_installation: bool = True) -> LicenseClaims:
    try:
        body64, supplied_sig = token.split(".", 1)
    except ValueError as exc:
        raise LicenseError("Format de licence invalide.") from exc
    expected = hmac.new(secret.encode(), body64.encode(), hashlib.sha256).digest()
    try:
        supplied = _b64d(supplied_sig)
    except Exception as exc:
        raise LicenseError("Signature de licence invalide.") from exc
    if not hmac.compare_digest(expected, supplied):
        raise LicenseError("Signature de licence invalide.")
    try:
        data = json.loads(_b64d(body64))
    except Exception as exc:
        raise LicenseError("Contenu de licence invalide.") from exc
    claims = LicenseClaims.from_dict(data)
    claims.assert_valid(installation_id() if bind_installation else None)
    return claims


def load_license_from_env(required: bool = False) -> LicenseClaims | None:
    token = os.getenv("LICENSE_TOKEN", "").strip()
    secret = os.getenv("LICENSE_VERIFY_SECRET", "").strip()
    if not token and not required:
        return None
    if not token or not secret:
        raise LicenseError("LICENSE_TOKEN et LICENSE_VERIFY_SECRET sont requis.")
    return verify_license(token, secret, bind_installation=os.getenv("LICENSE_BIND_INSTALLATION", "1") != "0")
