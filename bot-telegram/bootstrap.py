"""First-run bootstrap for commercial deployments."""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from auth import create_account


def ensure_panel_account(data_dir: Path) -> bool:
    """Create the panel account on first boot from secret environment variables.

    In COMMERCIAL_TEST_MODE, generate a strong temporary credential when no explicit
    bootstrap credential is supplied. This is for isolated test deployments only.
    """
    path = data_dir / "panel-account.json"
    if path.exists():
        return False
    username = os.getenv("PANEL_BOOTSTRAP_USERNAME", "").strip()
    password = os.getenv("PANEL_BOOTSTRAP_PASSWORD", "")
    test_mode = os.getenv("COMMERCIAL_TEST_MODE", "0") == "1"
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
    data_dir.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(account, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    return True
