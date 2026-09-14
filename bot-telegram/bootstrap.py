"""First-run bootstrap for commercial deployments."""
from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path

from auth import create_account, password_digest


def _write_account(path: Path, account: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(account, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)


def _account_matches(account: dict, username: str, password: str) -> bool:
    try:
        if account.get("username") != username:
            return False
        salt = bytes.fromhex(account["salt"])
        expected = account["digest"]
        actual = password_digest(password, salt)
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def ensure_panel_account(data_dir: Path) -> bool:
    """Create the panel account and keep test credentials in sync.

    In normal commercial mode the account remains first-run only.
    In COMMERCIAL_TEST_MODE, explicitly configured bootstrap credentials are
    authoritative so changing them on Railway refreshes panel-account.json on
    the next container start. This avoids stale test hashes between redeploys.
    """
    path = data_dir / "panel-account.json"
    username = os.getenv("PANEL_BOOTSTRAP_USERNAME", "").strip()
    password = os.getenv("PANEL_BOOTSTRAP_PASSWORD", "")
    test_mode = os.getenv("COMMERCIAL_TEST_MODE", "0") == "1"

    if path.exists():
        if not (test_mode and username and password):
            return False
        try:
            account = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            account = {}
        if _account_matches(account, username, password):
            return False
        try:
            refreshed = create_account(username, password)
        except ValueError as exc:
            raise SystemExit(f"Compte panel invalide : {exc}") from exc
        _write_account(path, refreshed)
        print("Compte panel test resynchronisé avec les variables Railway.", flush=True)
        return True

    if not username or not password:
        if not test_mode:
            raise SystemExit(
                "Premier démarrage : définis PANEL_BOOTSTRAP_USERNAME et "
                "PANEL_BOOTSTRAP_PASSWORD (15 caractères minimum)."
            )
        username = "testadmin"
        password = secrets.token_urlsafe(24)
        print(f"TEST PANEL LOGIN: username={username} password={password}", flush=True)

    try:
        account = create_account(username, password)
    except ValueError as exc:
        raise SystemExit(f"Compte panel invalide : {exc}") from exc
    _write_account(path, account)
    return True
