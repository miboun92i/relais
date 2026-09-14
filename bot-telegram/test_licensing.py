import base64
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from licensing import LicenseError, sign_license, verify_license


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


PRIVATE = Ed25519PrivateKey.generate()
PRIVATE_B64 = b64(PRIVATE.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()))
PUBLIC_B64 = b64(PRIVATE.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def payload(**overrides):
    value = {
        "license_id": "lic_test",
        "customer_id": "cus_test",
        "plan": "pro",
        "max_accounts": 2,
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
    }
    value.update(overrides)
    return value


def test_valid_license():
    token = sign_license(payload(), PRIVATE_B64)
    claims = verify_license(token, PUBLIC_B64, bind_installation=False)
    assert claims.plan == "pro"
    assert claims.max_accounts == 2


def test_tampered_license_is_rejected():
    token = sign_license(payload(), PRIVATE_B64)
    body, signature = token.split(".", 1)
    with pytest.raises(LicenseError):
        verify_license(body + "A." + signature, PUBLIC_B64, bind_installation=False)


def test_expired_license_is_rejected():
    token = sign_license(payload(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()), PRIVATE_B64)
    with pytest.raises(LicenseError, match="expirée"):
        verify_license(token, PUBLIC_B64, bind_installation=False)


def test_account_limit_is_validated():
    token = sign_license(payload(max_accounts=0), PRIVATE_B64)
    with pytest.raises(LicenseError):
        verify_license(token, PUBLIC_B64, bind_installation=False)
