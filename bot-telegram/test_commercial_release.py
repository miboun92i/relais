import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer
from auth import Auth, create_account
from commercial_security import LicenseGate, claim_volume, lock_volume
from license_server import make_license_app
from licensing import LicenseError, verify_license, sign_license
from test_licensing import PRIVATE_B64, PUBLIC_B64, payload


class AuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.account = create_account('owner', 'A secret owner password 2026')
        self.auth = Auth(self.account)
        self.http = TestClient(TestServer(make_license_app(self.root, PRIVATE_B64, PUBLIC_B64, self.auth)))
        await self.http.start_server()
        self.headers = {'Authorization': 'Bearer ' + self.auth.issue()}

    async def asyncTearDown(self):
        await self.http.close()
        self.tmp.cleanup()

    async def issue(self, accounts=1):
        r = await self.http.post('/admin/licenses', json={'customer': 'customer-a', 'accounts': accounts, 'days': 30}, headers=self.headers)
        self.assertEqual(r.status, 201)
        return await r.json()

    async def lease(self, token, installation):
        return await self.http.post('/lease', json={'license': token, 'installation_id': installation})

    async def test_admin_requires_auth_and_cross_origin_blocked(self):
        self.assertEqual((await self.http.get('/admin/licenses')).status, 401)
        self.assertEqual((await self.http.post('/admin/licenses', headers={**self.headers, 'Origin':'https://evil.example'}, json={})).status, 403)
        for accounts in (0, True, 101):
            r = await self.http.post('/admin/licenses', headers=self.headers, json={'customer':'a','accounts':accounts})
            self.assertEqual(r.status, 400)

    async def test_slots_concurrent_revocation_and_restart(self):
        issued = await self.issue()
        results = await asyncio.gather(*(self.lease(issued['license'], f'inst_customer_{i}') for i in range(2)))
        self.assertEqual(sorted(r.status for r in results), [200, 403])
        winner = results.index(next(r for r in results if r.status == 200))
        self.assertEqual((await self.lease(issued['license'], f'inst_customer_{winner}')).status, 200)
        await self.http.close()
        self.auth = Auth(self.account)
        self.http = TestClient(TestServer(make_license_app(self.root, PRIVATE_B64, PUBLIC_B64, self.auth)))
        await self.http.start_server()
        self.assertEqual((await self.lease(issued['license'], 'inst_another')).status, 403)
        self.headers = {'Authorization': 'Bearer ' + self.auth.issue()}
        r = await self.http.post('/admin/licenses/'+issued['claims']['license_id']+'/revoke', headers=self.headers)
        self.assertEqual(r.status, 200)
        self.assertEqual((await self.lease(issued['license'], f'inst_customer_{winner}')).status, 403)

    async def test_renewal_preserves_slot_and_invalidates_old_token(self):
        issued = await self.issue()
        self.assertEqual((await self.lease(issued['license'], 'inst_renewal_test')).status, 200)
        response = await self.http.post('/admin/licenses/'+issued['claims']['license_id']+'/renew',
                                        headers=self.headers, json={'days':30})
        self.assertEqual(response.status, 200)
        renewed = await response.json()
        self.assertEqual(renewed['claims']['license_id'], issued['claims']['license_id'])
        self.assertGreater(renewed['claims']['expires_at'], issued['claims']['expires_at'])
        self.assertEqual((await self.lease(renewed['license'], 'inst_renewal_test')).status, 200)
        self.assertEqual((await self.lease(issued['license'], 'inst_renewal_test')).status, 403)

    async def test_disable_releases_slot_without_reactivation(self):
        issued = await self.issue()
        self.assertEqual((await self.lease(issued['license'], 'inst_first_account')).status, 200)
        self.assertEqual((await self.http.post('/admin/installations/inst_first_account/disable', headers=self.headers)).status, 200)
        self.assertEqual((await self.lease(issued['license'], 'inst_second_account')).status, 200)
        self.assertEqual((await self.lease(issued['license'], 'inst_first_account')).status, 403)

    async def test_gate_revocation_and_outage_fail_closed(self):
        issued = await self.issue()
        claims = verify_license(issued['license'], PUBLIC_B64, bind_installation=False)
        with patch.dict(os.environ, {'DATA_DIR':str(self.root)}):
            gate = LicenseGate(claims, issued['license'], PUBLIC_B64, str(self.http.make_url('')).rstrip('/'), self.root, allow_local=True)
            await gate.refresh()
            gate.require()
            await self.http.post('/admin/licenses/'+claims.license_id+'/revoke', headers=self.headers)
            with self.assertRaises(LicenseError):
                await gate.refresh()
            with self.assertRaises(LicenseError):
                gate.require()
        self.assertLessEqual(gate.until, time.monotonic())

    async def test_client_zero_http_flow_and_persistent_session(self):
        from core import Store, Engine
        from main import make_app
        from commercial_panel import add_commercial_routes
        from telegram_onboarding import TelegramOnboarding
        from telethon.errors import SessionPasswordNeededError
        from telethon.sessions import SQLiteSession
        from telethon.crypto import AuthKey
        issued = await self.issue()
        claims = verify_license(issued['license'], PUBLIC_B64, bind_installation=False)
        with patch.dict(os.environ, {'DATA_DIR':str(self.root)}):
            gate = LicenseGate(claims, issued['license'], PUBLIC_B64, str(self.http.make_url('')).rstrip('/'), self.root, allow_local=True)
            await gate.refresh()
            store = Store(self.root/'conversations.sqlite3')
            session = SQLiteSession(str(self.root/'compte'))
            telegram = SimpleNamespace(is_connected=lambda:True, connect=AsyncMock(),
                is_user_authorized=AsyncMock(return_value=False), send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash='secret-hash')),
                get_me=AsyncMock(return_value=SimpleNamespace(id=123,first_name='Client zéro')))
            async def signin(**kw):
                if 'code' in kw:
                    raise SessionPasswordNeededError(None)
                telegram.is_user_authorized.return_value=True
                session.auth_key=AuthKey(b'A'*256)
                session.save()
            telegram.sign_in=AsyncMock(side_effect=signin)
            transport=AsyncMock(return_value=2)
            engine=Engine(store,transport,AsyncMock(return_value='Bonjour client zéro'),delay=0)
            app=make_app(engine, Auth(self.account), {'https://panel.example'}, telegram.is_user_authorized, 'mock')
            add_commercial_routes(app,TelegramOnboarding(telegram),claims,gate)
            async with TestClient(TestServer(app)) as panel:
                login=await panel.post('/api/login',json={'username':'owner','password':'A secret owner password 2026'})
                headers={'Authorization':'Bearer '+(await login.json())['token']}
                for endpoint,data in [('send-code',{'phone':'+33612345678'}),('code',{'code':'12345'}),('password',{'password':'test-only'})]:
                    r=await panel.post('/api/telegram/'+endpoint,json=data,headers=headers)
                    self.assertEqual(r.status,200)
                settings={**store.settings(),'enabled':True,'catalog':'Catalogue client zéro'}
                self.assertEqual((await panel.post('/api/settings',json=settings,headers=headers)).status,200)
                store.ensure(1, 'Client test')
                store.add(1, 1, 'client', 'Bonjour', time.time())
                engine.incoming(1)
                await asyncio.gather(*list(engine.tasks))
                transport.assert_awaited_once()
            await engine.close()
            session.close();store.db.close()
            reopened=Store(self.root/'conversations.sqlite3')
            restored=SQLiteSession(str(self.root/'compte'))
            self.assertTrue(reopened.settings()['enabled'])
            self.assertEqual(reopened.settings()['catalog'],'Catalogue client zéro')
            self.assertEqual(reopened.messages(1)[-1]['text'],'Bonjour client zéro')
            self.assertEqual(restored.auth_key.key,b'A'*256)
            restored.close();reopened.db.close()


def test_volume_ownership_and_single_process(tmp_path):
    claim_volume(tmp_path,'customer-a')
    claim_volume(tmp_path,'customer-a')
    with __import__('pytest').raises(LicenseError): claim_volume(tmp_path,'customer-b')
    lock=lock_volume(tmp_path)
    try:
        with __import__('pytest').raises(LicenseError): lock_volume(tmp_path)
    finally: lock.close()


def test_expiry_checked_during_runtime(tmp_path, monkeypatch):
    token=sign_license(payload(expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()),PRIVATE_B64)
    with __import__('pytest').raises(LicenseError): verify_license(token,PUBLIC_B64,False)
    for invalid in ([], {'license_id':[]}, payload(expires_at=5)):
        with __import__('pytest').raises(LicenseError): verify_license(sign_license(invalid,PRIVATE_B64),PUBLIC_B64,False)

class AccountIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_telegram_identity_is_disconnected(self):
        from telegram_onboarding import TelegramOnboarding, TelegramOnboardingError
        client=SimpleNamespace(is_connected=lambda:True,is_user_authorized=AsyncMock(return_value=True),
                              get_me=AsyncMock(return_value=SimpleNamespace(id=456)),log_out=AsyncMock())
        def bound_account(uid):
            if uid != 123: raise ValueError('Compte différent')
        flow=TelegramOnboarding(client,account_validator=bound_account)
        with self.assertRaises(TelegramOnboardingError): await flow.status()
        client.log_out.assert_awaited_once()

    async def test_panel_token_does_not_cross_customer_boundary(self):
        from core import Store,Engine
        from main import make_app
        a=Auth(create_account('customer-a','Customer A password 2026'))
        b=Auth(create_account('customer-b','Customer B password 2026'))
        token=a.issue()
        with tempfile.TemporaryDirectory() as tmp:
            sa,sb=Store(Path(tmp)/'a.sqlite3'),Store(Path(tmp)/'b.sqlite3')
            sa.ensure(123,'Conversation privée A')
            engine=Engine(sb,AsyncMock(),AsyncMock())
            async with TestClient(TestServer(make_app(engine,b,{'https://panel.example'},lambda:False,'mock'))) as http:
                response=await http.get('/api/state',headers={'Authorization':'Bearer '+token})
                self.assertEqual(response.status,401)
                self.assertEqual(sb.chats(),[])
            await engine.close();sa.db.close();sb.db.close()


def test_lease_and_license_expiry_rechecked_without_restart(tmp_path,monkeypatch):
    from dataclasses import replace
    monkeypatch.setenv('DATA_DIR',str(tmp_path))
    claims=verify_license(sign_license(payload(),PRIVATE_B64),PUBLIC_B64,False)
    gate=LicenseGate(claims,'signed-token',PUBLIC_B64,'https://authority.example',tmp_path)
    gate.until=time.monotonic()+60
    gate.require()
    gate.claims=replace(claims,expires_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    with __import__('pytest').raises(LicenseError):gate.require()
    gate.claims=claims;gate.until=time.monotonic()-1
    with __import__('pytest').raises(LicenseError):gate.require()
