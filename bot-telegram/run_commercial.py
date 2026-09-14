"""Licensed production entrypoint.

Use this instead of `python main.py` for commercial deployments.
"""
import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

from licensing import LicenseError, load_license_from_env
import main as relay


ROOT = Path(__file__).resolve().parent


def validate_license():
    load_dotenv(ROOT / ".env")
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


if __name__ == "__main__":
    try:
        validate_license()
        asyncio.run(relay.main())
    except LicenseError as exc:
        raise SystemExit(f"Licence refusée : {exc}") from exc
    except KeyboardInterrupt:
        print("\nRelais arrêté.")
