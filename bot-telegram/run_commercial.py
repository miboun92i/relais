"""Licensed production entrypoint for commercial deployments."""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from bootstrap import ensure_panel_account
from runtime_config import data_directory
from licensing import LicenseError, load_license_from_env
import commercial_main as relay

ROOT = Path(__file__).resolve().parent


def prepare_deployment():
    data_dir = data_directory(ROOT)
    created = ensure_panel_account(data_dir)
    if created:
        print("Compte panel initialisé automatiquement.", flush=True)
    required = os.getenv("LICENSE_REQUIRED", "1") != "0"
    claims = load_license_from_env(required=required)
    if claims:
        print(
            f"Licence valide : {claims.license_id} · client={claims.customer_id} · "
            f"plan={claims.plan} · comptes={claims.max_accounts}",
            flush=True,
        )
    elif required:
        raise LicenseError("Licence commerciale requise.")
    return claims


if __name__ == "__main__":
    try:
        prepare_deployment()
        asyncio.run(relay.main())
    except LicenseError as exc:
        raise SystemExit(f"Licence refusée : {exc}") from exc
    except KeyboardInterrupt:
        print("\nRelais arrêté.")

