from datetime import datetime, timedelta, timezone

import pytest

from licensing import LicenseError, sign_license, verify_license

SECRET = "x" * 48


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
    token = sign_license(payload(), SECRET)
    claims = verify_license(token, SECRET, bind_installation=False)
    assert claims.plan == "pro"
    assert claims.max_accounts == 2


def test_tampered_license_is_rejected():
    token = sign_license(payload(), SECRET)
    body, signature = token.split(".", 1)
    with pytest.raises(LicenseError):
        verify_license(body + "A." + signature, SECRET, bind_installation=False)


def test_expired_license_is_rejected():
    token = sign_license(
        payload(expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()),
        SECRET,
    )
    with pytest.raises(LicenseError, match="expirée"):
        verify_license(token, SECRET, bind_installation=False)


def test_account_limit_is_validated():
    token = sign_license(payload(max_accounts=0), SECRET)
    with pytest.raises(LicenseError):
        verify_license(token, SECRET, bind_installation=False)
