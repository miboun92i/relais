"""Vendor-only CLI to issue Relais licenses.

Usage:
  LICENSE_VERIFY_SECRET='...' python license_admin.py issue --customer client42 --plan pro --accounts 2 --days 30

Keep this script and the signing secret on the vendor side only.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

from licensing import LicenseError, sign_license


def build_parser():
    parser = argparse.ArgumentParser(description="Administration des licences Relais")
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue", help="Créer une licence signée")
    issue.add_argument("--customer", required=True)
    issue.add_argument("--plan", default="pro")
    issue.add_argument("--accounts", type=int, default=1)
    issue.add_argument("--days", type=int, default=30, help="0 = sans expiration")
    issue.add_argument("--installation-id", default=None)
    return parser


def main():
    args = build_parser().parse_args()
    secret = os.getenv("LICENSE_VERIFY_SECRET", "")
    if len(secret) < 32:
        raise SystemExit("Définis LICENSE_VERIFY_SECRET avec au moins 32 caractères.")
    if args.accounts < 1:
        raise SystemExit("--accounts doit être >= 1")
    if args.days < 0:
        raise SystemExit("--days doit être >= 0")

    if args.command == "issue":
        expiry = None
        if args.days:
            expiry = (datetime.now(timezone.utc) + timedelta(days=args.days)).isoformat()
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
            token = sign_license(payload, secret)
        except LicenseError as exc:
            raise SystemExit(str(exc)) from exc
        print(json.dumps({"license": token, "claims": payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
