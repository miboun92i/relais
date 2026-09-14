import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import Store, Engine
from telegram_events import route_private_message
from telegram_onboarding import TelegramOnboarding, TelegramOnboardingError
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telethon.types import MessageMediaWebPage, WebPageEmpty
from runtime_config import data_directory


class ReliabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'db.sqlite'
        self.store = Store(self.path)
        self.store.save_settings({**self.store.settings(), 'enabled': True})
        self.send = AsyncMock(return_value=100)
        self.generate = AsyncMock(return_value='Bonjour !')
        self.engine = Engine(self.store, self.send, self.generate, delay=0, retry_delays=(0, 0))

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.db.close()
        self.tmp.cleanup()

    async def incoming(self, text='Bonjour', media=None, sticker=None, uid=1, mid=1):
        event = SimpleNamespace(chat_id=uid, id=mid, raw_text=text, media=media,
                                message=SimpleNamespace(sticker=sticker), out=False,
                                date=datetime.now(timezone.utc))
        await route_private_message(self.engine, event, 'Test')
        await asyncio.gather(*list(self.engine.tasks))

    async def test_empty_response_retries_without_manual(self):
        self.generate.side_effect = ['', '   ', 'Salut !']
        await self.incoming()
        self.assertEqual(self.generate.await_count, 3)
        self.send.assert_awaited_once_with(1, 'Salut !')
        self.assertEqual(self.store.chat(1)['mode'], 'auto')

    async def test_empty_exhausted_then_next_message_recovers(self):
        self.generate.return_value = ''
        await self.incoming()
        self.assertEqual(self.generate.await_count, 3)
        self.assertEqual(self.store.chat(1)['mode'], 'auto')
        self.send.assert_not_awaited()
        self.assertIn('EmptyAIResponse', self.engine.last_error)
        self.generate.return_value = 'Bonjour !'
        await self.incoming(mid=2)
        self.send.assert_awaited_once()
        self.assertIsNone(self.engine.last_error)

    async def test_configuration_error_is_not_a_human_handoff(self):
        self.generate.side_effect = ValueError('Invalid provider setting')
        await self.incoming()
        self.assertEqual(self.generate.await_count, 1)
        self.assertEqual(self.store.chat(1)['mode'], 'auto')
        self.send.assert_not_awaited()

    async def test_timeout_retries_and_other_chat_isolated(self):
        self.generate.side_effect = [TimeoutError(), 'OK', 'Deuxième']
        await self.incoming(uid=1)
        await self.incoming(uid=2)
        self.assertEqual(self.store.messages(1)[-1]['text'], 'OK')
        self.assertEqual(self.store.messages(2)[-1]['text'], 'Deuxième')

    async def test_sticker_greeting_replies_once(self):
        await self.incoming(text='', media=object(), sticker=object())
        self.assertEqual(self.store.chat(1)['mode'], 'auto')
        self.assertIn('Sticker Telegram', self.generate.call_args.args[1][-1]['content'])
        await self.incoming(text='', media=object(), sticker=object())
        self.send.assert_awaited_once()

    async def test_real_media_requires_manual(self):
        await self.incoming(text='', media=object())
        self.assertEqual(self.store.chat(1)['mode'], 'manual')
        self.generate.assert_not_awaited()

    async def test_link_preview_is_text(self):
        await self.incoming(text='Voici le lien', media=MessageMediaWebPage(WebPageEmpty(1)))
        self.send.assert_awaited_once()
        self.assertEqual(self.store.chat(1)['mode'], 'auto')

    async def test_explicit_handoff_still_pauses(self):
        self.generate.return_value = '[RELAIS_HUMAIN]'
        await self.incoming()
        self.assertEqual(self.store.chat(1)['mode'], 'manual')
        self.assertNotIn('[RELAIS_HUMAIN]', self.send.call_args.args[1])

    async def test_vague_message_stays_auto(self):
        self.generate.return_value = 'Tu peux préciser ce que tu recherches ?'
        await self.incoming(text='hein truc jsp')
        self.assertEqual(self.store.chat(1)['mode'], 'auto')
        self.send.assert_awaited_once()

    async def test_restart_preserves_enabled_and_intentional_pause(self):
        for enabled in (True, False):
            self.engine.settings({**self.store.settings(), 'enabled': enabled})
            other = Store(self.path)
            self.assertEqual(other.settings()['enabled'], enabled)
            other.db.close()

    async def test_panel_login_onboarding_dm_and_restart(self):
        from aiohttp.test_utils import TestClient, TestServer
        from auth import Auth, create_account
        from main import make_app
        from commercial_panel import add_commercial_routes
        account = create_account('test-admin', 'Test password isolated 2026')
        client = OnboardingTests().client()
        client.is_connected = lambda: True
        onboarding = TelegramOnboarding(client)
        app = make_app(self.engine, Auth(account), {'https://panel.example'}, client.is_user_authorized, 'test')
        claims = SimpleNamespace(license_id='test', customer_id='test', plan='test', max_accounts=1, expires_at=None)
        add_commercial_routes(app, onboarding, claims)
        async with TestClient(TestServer(app)) as http:
            self.assertEqual((await http.get('/health')).status, 200)
            self.assertEqual((await http.post('/api/telegram/code', json={'code': '12345'})).status, 401)
            login = await http.post('/api/login', json={'username': 'test-admin', 'password': 'Test password isolated 2026'})
            self.assertEqual(login.status, 200)
            headers = {'Authorization': 'Bearer ' + (await login.json())['token']}
            self.assertFalse((await (await http.get('/api/state', headers=headers)).json())['connected'])
            response = await http.post('/api/telegram/send-code', headers=headers, json={'phone': '+33612345678'})
            self.assertEqual(response.status, 200)
            async def sign_in(**kwargs): client.is_user_authorized.return_value = True
            client.sign_in.side_effect = sign_in
            response = await http.post('/api/telegram/code', headers=headers, json={'code': '12345'})
            self.assertEqual((await response.json())['next'], 'done')
            await self.incoming()
            state = await (await http.get('/api/state', headers=headers)).json()
            self.assertTrue(state['connected'])
            self.assertEqual(self.store.messages(1)[-1]['source'], 'ai')
        reopened = Store(self.path)
        try:
            self.assertTrue(reopened.settings()['enabled'])
            self.assertEqual(reopened.messages(1)[-1]['text'], 'Bonjour !')
            restarted = Auth(account)
            self.assertTrue(restarted.valid(await restarted.login('test-admin', 'Test password isolated 2026')))
        finally:
            reopened.db.close()


class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return SimpleNamespace(is_connected=lambda: False, connect=AsyncMock(),
            is_user_authorized=AsyncMock(return_value=False),
            send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash='hash')),
            sign_in=AsyncMock(), get_me=AsyncMock(return_value=SimpleNamespace(id=123, first_name='Test')))

    async def test_code_2fa_flow(self):
        client = self.client(); flow = TelegramOnboarding(client)
        await flow.send_code('+33612345678')
        client.sign_in.side_effect = [PhoneCodeInvalidError(None), SessionPasswordNeededError(None), None]
        with self.assertRaises(TelegramOnboardingError): await flow.submit_code('12345')
        self.assertEqual((await flow.submit_code('23456'))['next'], 'password')
        client.is_user_authorized.return_value = True
        self.assertEqual((await flow.submit_password('test-password'))['next'], 'done')
        self.assertIsNone(flow.pending_phone)
        self.assertIsNone(flow.phone_code_hash)
        self.assertFalse(flow.needs_password)

    async def test_expired_login_clears_2fa(self):
        client = self.client(); flow = TelegramOnboarding(client)
        await flow.send_code('+33612345678')
        flow.needs_password = True; flow.expires_at = 1
        with self.assertRaises(TelegramOnboardingError): await flow.submit_password('test')
        client.sign_in.assert_not_awaited()
        self.assertIsNone(flow.pending_phone)


def test_dotenv_directory_is_loaded_before_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv('DATA_DIR', raising=False)
    monkeypatch.delenv('RAILWAY_VOLUME_MOUNT_PATH', raising=False)
    destination = tmp_path/'persistent'
    (tmp_path/'.env').write_text(f'DATA_DIR={destination}\n')
    try:
        assert data_directory(tmp_path) == destination
    finally:
        monkeypatch.delenv('DATA_DIR', raising=False)
