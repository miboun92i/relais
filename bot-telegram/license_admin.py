"""Vendor-only CLI to create signing keys and issue Relais licenses."""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from licensing import LicenseError, sign_license


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def build_parser():
    parser = argparse.ArgumentParser(description="Administration des licences Relais")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keygen", help="Créer une paire de clés Ed25519")
    issue = sub.add_parser("issue", help="Créer une licence signée")
    issue.add_argument("--customer", required=True)
    issue.add_argument("--plan", default="pro")
    issue.add_argument("--accounts", type=int, default=1)
    issue.add_argument("--days", type=int, default=30, help="0 = sans expiration")
    issue.add_argument("--installation-id", default=None)
    return parser


def main():
    args = build_parser().parse_args()
    if args.command == "keygen":
        private = Ed25519PrivateKey.generate()
        public = private.public_key()
        private_raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        public_raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        print(json.dumps({"private_key": b64(private_raw), "public_key": b64(public_raw)}, indent=2))
        return

    private_key = os.getenv("LICENSE_PRIVATE_KEY", "").strip()
    if not private_key:
        raise SystemExit("Définis LICENSE_PRIVATE_KEY côté vendeur uniquement.")
    if args.accounts < 1 or args.days < 0:
        raise SystemExit("Paramètres de licence invalides.")
    expiry = None if args.days == 0 else (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
    payload = {
        "license_id": "lic_" + secrets.token_urlsafe(12),
        "customer_id": args.customer,
        "plan": args.plan,
        "max_accounts": args.accounts,
        "expires_at": expiry,
        "installation_id": args.installation_id,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        token = sign_license(payload, private_key)
    except LicenseError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"license": token, "claims": payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
