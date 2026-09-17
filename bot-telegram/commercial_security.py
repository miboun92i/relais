"""Fail-closed licensing and ownership of a single-customer volume."""
import asyncio
import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from aiohttp import ClientSession, ClientTimeout
from licensing import LicenseError, installation_id, verify_license


def claim_volume(root, customer_id):
    path = root / '.customer-id'
    if path.exists():
        if path.read_text().strip() != customer_id:
            raise LicenseError('Ce volume appartient à un autre client. Utilisez un volume vierge.')
    else:
        with path.open('x') as out:
            out.write(customer_id)
        path.chmod(0o600)


def lock_volume(root):
    handle = (root / '.runtime.lock').open('a')
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise LicenseError('Une autre instance utilise déjà ce volume.')
    return handle


class LicenseGate:
    def __init__(self, claims, token, public_key, url, root, *, allow_local=False):
        if not url.startswith('https://') and not (allow_local and url.startswith('http://127.0.0.1:')):
            raise LicenseError('LICENSE_SERVER_URL HTTPS est requis.')
        self.claims, self.token, self.public_key, self.url = claims, token, public_key, url.rstrip('/')
        self.installation = installation_id()
        self.until = 0
        self.error = 'Validation de licence en attente.'
        self.lock = asyncio.Lock()
        claim_volume(root, claims.customer_id)

    def require(self):
        self.claims.assert_valid(self.installation)
        if time.monotonic() >= self.until:
            raise LicenseError(self.error)

    async def refresh(self):
        async with self.lock:
            started = time.monotonic()
            try:
                async with ClientSession(timeout=ClientTimeout(total=10)) as http:
                    async with http.post(self.url + '/lease', json={'license': self.token, 'installation_id': self.installation}, allow_redirects=False) as response:
                        if response.status != 200:
                            self.until = 0
                            raise LicenseError('Licence refusée : contactez le propriétaire.')
                        token = (await response.json())['lease']
                lease = verify_license(token, self.public_key, bind_installation=False)
                lease.assert_valid(self.installation)
                from licensing import _b64d
                raw = json.loads(_b64d(token.split('.')[0]))
                if (lease.license_id != self.claims.license_id or lease.installation_id != self.installation
                    or raw.get('purpose') != 'runtime-lease' or raw.get('license_hash') != hashlib.sha256(self.token.encode()).hexdigest()):
                    raise LicenseError('Autorisation de licence incohérente.')
                from datetime import datetime, timezone
                seconds = (datetime.fromisoformat(lease.expires_at.replace('Z', '+00:00')) - datetime.now(timezone.utc)).total_seconds()
                self.until = started + min(90, max(0, seconds))
                self.error = 'Validation de licence expirée. Vérifiez le serveur de licences.'
            except LicenseError as exc:
                self.until = 0
                self.error = str(exc)
            except Exception:
                self.error = 'Serveur de licences indisponible. Réessayez plus tard.'
            self.require()

    async def monitor(self):
        while True:
            await asyncio.sleep(25)
            try:
                await self.refresh()
            except LicenseError:
                pass
