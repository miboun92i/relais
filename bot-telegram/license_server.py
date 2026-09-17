"""Owner-operated authority: signed licences and durable installation slots."""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiohttp import web
from auth import Auth, LoginFailed, TooManyAttempts
from bootstrap import ensure_panel_account
from licensing import LicenseError, sign_license, verify_license
from runtime_config import data_directory


def make_license_app(data_dir, private_key, public_key, auth):
    db = sqlite3.connect(data_dir / 'licenses.sqlite3')
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS licenses (
        id TEXT PRIMARY KEY, customer TEXT NOT NULL, token TEXT NOT NULL,
        revoked INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS installations (
        id TEXT PRIMARY KEY, license_id TEXT NOT NULL, created TEXT NOT NULL,
        disabled INTEGER NOT NULL DEFAULT 0);
    ''')

    @web.middleware
    async def guard(request, handler):
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                   'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"}
        if request.headers.get('Origin') and request.headers['Origin'] != 'https://' + request.host:
            return web.json_response({'error': 'Origine refusée.'}, status=403, headers=headers)
        if request.path.startswith('/admin/'):
            token = request.headers.get('Authorization', '').removeprefix('Bearer ')
            if not auth.valid(token):
                return web.json_response({'error': 'Connexion propriétaire requise.'}, status=401, headers=headers)
        try:
            response = await handler(request)
        except TooManyAttempts:
            response = web.json_response({'error': 'Réessayez dans une minute.'}, status=429)
        except LoginFailed:
            response = web.json_response({'error': 'Identifiants incorrects.'}, status=401)
        except (ValueError, KeyError, TypeError):
            response = web.json_response({'error': 'Demande ou licence invalide.'}, status=400)
        response.headers.update(headers)
        return response

    app = web.Application(middlewares=[guard], client_max_size=16000)
    async def health(request):
        db.execute('SELECT 1').fetchone()
        return web.json_response({'status': 'ok', 'component': 'license-authority'})
    async def login(request):
        data = await request.json()
        return web.json_response({'token': await auth.login(data.get('username'), data.get('password'))})
    async def listing(request):
        rows = []
        for row in db.execute('SELECT * FROM licenses ORDER BY created DESC'):
            value = dict(row)
            claims = json.loads(__import__('base64').urlsafe_b64decode(value.pop('token').split('.')[0] + '==='))
            value.update(expires_at=claims['expires_at'], max_accounts=claims['max_accounts'])
            value['installations'] = [dict(r) for r in db.execute('SELECT id,disabled FROM installations WHERE license_id=?', (row['id'],))]
            rows.append(value)
        return web.json_response({'licenses': rows})
    async def issue(request):
        data = await request.json()
        customer, days, accounts = data.get('customer'), data.get('days', 30), data.get('accounts', 1)
        if not isinstance(customer, str) or not 1 <= len(customer.strip()) <= 100:
            raise ValueError()
        if type(days) is not int or not 1 <= days <= 3660 or type(accounts) is not int or not 1 <= accounts <= 100:
            raise ValueError()
        now = datetime.now(timezone.utc)
        claims = dict(license_id='lic_' + secrets.token_urlsafe(18), customer_id=customer.strip(),
                      plan='pro', max_accounts=accounts, expires_at=(now + timedelta(days=days)).isoformat(), issued_at=now.isoformat())
        token = sign_license(claims, private_key)
        with db:
            db.execute('INSERT INTO licenses(id,customer,token,created) VALUES(?,?,?,?)', (claims['license_id'], claims['customer_id'], token, now.isoformat()))
        return web.json_response({'license': token, 'claims': claims}, status=201)
    async def renew(request):
        data = await request.json()
        days = data.get('days', 30)
        if type(days) is not int or not 1 <= days <= 3660:
            raise ValueError()
        row = db.execute('SELECT token,revoked FROM licenses WHERE id=?', (request.match_info['id'],)).fetchone()
        if not row or row['revoked']:
            raise ValueError()
        from licensing import _b64d
        claims = json.loads(_b64d(row['token'].split('.')[0]))
        previous = datetime.fromisoformat(claims['expires_at'].replace('Z', '+00:00'))
        claims['expires_at'] = (max(previous, datetime.now(timezone.utc)) + timedelta(days=days)).isoformat()
        token = sign_license(claims, private_key)
        with db:
            db.execute('UPDATE licenses SET token=? WHERE id=?', (token, claims['license_id']))
        return web.json_response({'license': token, 'claims': claims})
    async def revoke(request):
        with db:
            result = db.execute('UPDATE licenses SET revoked=1 WHERE id=?', (request.match_info['id'],))
        if not result.rowcount:
            raise web.HTTPNotFound()
        return web.json_response({'ok': True})
    async def disable_installation(request):
        with db:
            result = db.execute('UPDATE installations SET disabled=1 WHERE id=?', (request.match_info['id'],))
        if not result.rowcount:
            raise web.HTTPNotFound()
        return web.json_response({'ok': True})
    async def lease(request):
        data = await request.json()
        token, installation = data.get('license'), data.get('installation_id')
        if not isinstance(token, str) or len(token) > 8192 or not isinstance(installation, str) or not 10 <= len(installation) <= 100:
            raise ValueError()
        claims = verify_license(token, public_key, bind_installation=False)
        claims.assert_valid(installation)
        row = db.execute('SELECT token,revoked FROM licenses WHERE id=?', (claims.license_id,)).fetchone()
        if not row or row['revoked'] or not secrets.compare_digest(row['token'], token):
            return web.json_response({'error': 'Licence révoquée ou inconnue.'}, status=403)
        # No await inside the transaction: count and reservation are atomic.
        try:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM installations WHERE id=?', (installation,)).fetchone()
            if existing and (existing['license_id'] != claims.license_id or existing['disabled']):
                db.rollback()
                return web.json_response({'error': 'Installation désactivée ou attribuée à une autre licence.'}, status=403)
            if not existing:
                count = db.execute('SELECT count(*) FROM installations WHERE license_id=? AND disabled=0', (claims.license_id,)).fetchone()[0]
                if count >= claims.max_accounts:
                    db.rollback()
                    return web.json_response({'error': 'Limite de comptes atteinte.'}, status=403)
                db.execute('INSERT INTO installations(id,license_id,created) VALUES(?,?,?)', (installation, claims.license_id, datetime.now(timezone.utc).isoformat()))
            db.commit()
        except Exception:
            db.rollback()
            raise
        expiry = min(datetime.now(timezone.utc) + timedelta(seconds=90), datetime.fromisoformat(claims.expires_at.replace('Z', '+00:00')))
        payload = dict(license_id=claims.license_id, customer_id=claims.customer_id, plan=claims.plan,
                       max_accounts=claims.max_accounts, installation_id=installation, expires_at=expiry.isoformat(),
                       purpose='runtime-lease', license_hash=hashlib.sha256(token.encode()).hexdigest())
        return web.json_response({'lease': sign_license(payload, private_key)})
    async def index(request):
        return web.FileResponse(Path(__file__).with_name('license_admin.html'))
    async def styles(request):
        return web.FileResponse(Path(__file__).with_name('license_admin.css'))
    async def script(request):
        return web.FileResponse(Path(__file__).with_name('license_admin.js'))
    async def close(app):
        db.close()
    app.on_cleanup.append(close)
    app.add_routes([web.get('/health', health), web.get('/', index), web.get('/license_admin.js', script), web.get('/license_admin.css', styles),
                    web.post('/login', login), web.get('/admin/licenses', listing), web.post('/admin/licenses', issue),
                    web.post('/admin/licenses/{id}/revoke', revoke), web.post('/admin/licenses/{id}/renew', renew),
                    web.post('/admin/installations/{id}/disable', disable_installation), web.post('/lease', lease)])
    return app


if __name__ == '__main__':
    os.umask(0o077)
    root = data_directory(Path(__file__).resolve().parent)
    ensure_panel_account(root)
    private, public = os.environ['LICENSE_PRIVATE_KEY'], os.environ['LICENSE_PUBLIC_KEY']
    # Fail at startup if the key pair is inconsistent.
    probe = dict(license_id='probe', customer_id='probe', plan='probe', max_accounts=1)
    verify_license(sign_license(probe, private), public, bind_installation=False)
    web.run_app(make_license_app(root, private, public, Auth.from_file(root / 'panel-account.json')),
                host='0.0.0.0', port=int(os.getenv('PORT', '8787')), access_log=None)
