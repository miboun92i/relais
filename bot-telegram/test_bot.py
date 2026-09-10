import asyncio
import tempfile
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from core import Store, Engine
from main import make_app
from auth import Auth, LoginFailed, TooManyAttempts, create_account

TEST_PASSWORD = 'Une phrase strictement pour les tests 2026'
TEST_ACCOUNT = create_account('test-admin', TEST_PASSWORD)

class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'test.sqlite3'
        self.store = Store(self.path)
        self.store.ensure(1, 'Client test')
        self.store.add(1, 1, 'client', 'Bonjour')
        self.store.save_settings({**self.store.settings(), 'enabled': True})
        self.started, self.release = asyncio.Event(), asyncio.Event()
        self.sent = []
        async def generate(prompt, messages):
            self.started.set()
            await self.release.wait()
            return 'Réponse test'
        async def send(chat_id, text):
            self.sent.append((chat_id, text))
            return 100 + len(self.sent)
        self.engine = Engine(self.store, send, generate, delay=0)

    async def asyncTearDown(self):
        await self.engine.close()
        self.store.db.close()
        self.tmp.cleanup()

    async def start_reply(self):
        task = asyncio.create_task(self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch))
        await asyncio.wait_for(self.started.wait(), 1)
        return task

    async def test_phone_takeover_discards_pending_ai(self):
        task = await self.start_reply()
        await self.engine.outgoing(1, 50, 'Je réponds moi-même')
        self.release.set()
        await task
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.chat(1)['mode'], 'manual')
        self.assertEqual(self.store.messages(1)[-1]['source'], 'human')

    async def test_panel_send_pauses_and_is_idempotent(self):
        task = await self.start_reply()
        first = await self.engine.reply(1, 'Bonjour !', 'same-request-123456')
        second = await self.engine.reply(1, 'Bonjour !', 'same-request-123456')
        self.release.set()
        await task
        self.assertEqual(first, second)
        self.assertEqual(self.sent, [(1, 'Bonjour !')])

    async def test_pause_then_resume_does_not_release_old_draft(self):
        task = await self.start_reply()
        self.engine.set_mode(1, 'manual')
        self.engine.set_mode(1, 'auto')
        self.release.set()
        await task
        self.assertEqual(self.sent, [])

    async def test_global_pause_invalidates_draft(self):
        task = await self.start_reply()
        self.engine.settings({**self.store.settings(), 'enabled': False})
        self.release.set()
        await task
        self.assertEqual(self.sent, [])

    async def test_ai_echo_does_not_pause(self):
        self.release.set()
        await self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch)
        await self.engine.outgoing(1, 101, 'Réponse test')
        self.assertEqual(self.store.chat(1)['mode'], 'auto')
        self.assertEqual(len(self.store.messages(1)), 2)
        self.assertEqual(self.store.messages(1)[-1]['source'], 'ai')

    async def test_identity_question_hands_over_without_ai_call(self):
        self.store.add(1, 2, 'client', "C'est vraiment toi ?")
        self.engine.incoming(1)
        self.assertEqual(self.store.chat(1)['mode'], 'manual')
        self.assertEqual(self.sent, [])
        self.assertFalse(self.started.is_set())
        self.assertEqual(self.store.usage(), 0)

    async def test_ai_self_presentation_is_not_sent(self):
        async def generate(prompt, messages):
            return 'Je suis un assistant automatique.'
        self.engine.generate = generate
        await self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.chat(1)['mode'], 'manual')

    async def test_identity_draft_requires_personal_reply(self):
        self.store.add(1, 2, 'client', 'Tu es une IA ?')
        with self.assertRaises(ValueError):
            await self.engine.draft(1)
        self.assertEqual(self.store.chat(1)['mode'], 'manual')
        self.assertEqual(self.store.usage(), 0)

    async def test_settings_and_takeover_survive_restart(self):
        self.engine.set_mode(1, 'manual')
        self.engine.settings({**self.store.settings(), 'catalog': 'Prestation test 25 €'})
        other = Store(self.path)
        self.assertEqual(other.chat(1)['mode'], 'manual')
        self.assertEqual(other.settings()['catalog'], 'Prestation test 25 €')
        other.db.close()

    async def test_daily_limit_persists(self):
        self.engine.settings({**self.store.settings(), 'daily_limit': 1})
        self.store.reserve()
        other = Store(self.path)
        with self.assertRaises(ValueError):
            other.reserve()
        other.db.close()

    async def test_burst_sends_only_latest_response(self):
        first = await self.start_reply()
        self.store.add(1, 2, 'client', 'Et les délais ?')
        second = asyncio.create_task(self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch))
        self.release.set()
        await asyncio.gather(first, second)
        self.assertEqual(len(self.sent), 1)

    async def test_auth_origin_and_api_mutations(self):
        authenticator = Auth(TEST_ACCOUNT)
        token = authenticator.issue()
        app = make_app(self.engine, authenticator, {'https://panel.example'}, lambda: True, 'ollama')
        async with TestClient(TestServer(app)) as client:
            self.assertEqual((await client.get('/api/state')).status, 401)
            auth = {'Authorization': 'Bearer ' + token, 'Origin': 'https://panel.example'}
            wrong = {**auth, 'Origin': 'https://evil.example'}
            self.assertEqual((await client.get('/api/state', headers=wrong)).status, 403)
            self.assertEqual((await client.options('/api/state', headers=auth)).status, 204)
            response = await client.get('/api/state', headers=auth)
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Access-Control-Allow-Origin'], 'https://panel.example')
            self.assertNotIn(token, await response.text())
            response = await client.post('/api/chats/1/mode', headers=auth, json={'mode': 'manual'})
            self.assertEqual(response.status, 200)
            self.assertEqual(self.store.chat(1)['mode'], 'manual')
            self.assertEqual((await client.post('/api/chats/1/mode', headers=auth, json={'mode': 'invalid'})).status, 400)
            settings = {**self.store.settings(), 'faq': 'Infos test'}
            self.assertEqual((await client.post('/api/settings', headers=auth, json=settings)).status, 200)
            self.assertEqual(self.store.settings()['faq'], 'Infos test')
            self.assertEqual((await client.post('/api/chats/2/reply', headers=auth, json={})).status, 404)

    async def test_login_logout_and_no_unauthenticated_mutations(self):
        authenticator = Auth(TEST_ACCOUNT)
        app = make_app(self.engine, authenticator, {'https://panel.example'}, lambda: True, 'ollama')
        async with TestClient(TestServer(app)) as client:
            for endpoint in ('/api/settings', '/api/chats/1/mode', '/api/chats/1/reply', '/api/chats/1/draft', '/api/chats/1/read'):
                self.assertEqual((await client.post(endpoint, json={'mode': 'manual'})).status, 401)
            self.assertEqual(self.store.chat(1)['mode'], 'auto')
            self.assertEqual(self.sent, [])
            self.assertEqual((await client.get('/api/chats/1/messages')).status, 401)
            login = {'username': 'test-admin', 'password': TEST_PASSWORD}
            evil = await client.post('/api/login', headers={'Origin': 'https://evil.example'}, json=login)
            self.assertEqual(evil.status, 403)
            response = await client.post('/api/login', json={**login, 'password': 'wrong'})
            self.assertEqual(response.status, 401)
            good = await client.post('/api/login', json=login)
            self.assertEqual(good.status, 200)
            data = await good.json()
            self.assertNotIn(TEST_PASSWORD, str(data))
            self.assertNotIn('digest', data)
            headers = {'Authorization': 'Bearer ' + data['token']}
            self.assertEqual((await client.get('/api/state', headers=headers)).status, 200)
            self.assertEqual((await client.post('/api/logout', headers=headers)).status, 200)
            self.assertEqual((await client.get('/api/state', headers=headers)).status, 401)


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_password_is_hashed_and_salted(self):
        second = create_account('test-admin', TEST_PASSWORD)
        self.assertNotEqual(TEST_ACCOUNT['salt'], second['salt'])
        self.assertNotEqual(TEST_ACCOUNT['digest'], second['digest'])
        self.assertNotIn(TEST_PASSWORD, str(second))
        with self.assertRaises(ValueError):
            create_account('test-admin', 'short')

    async def test_wrong_username_and_wrong_password_are_rejected(self):
        authenticator = Auth(TEST_ACCOUNT)
        for username, password in [('other-admin', TEST_PASSWORD), ('test-admin', 'incorrect')]:
            with self.assertRaises(LoginFailed):
                await authenticator.login(username, password)
        self.assertEqual(authenticator.sessions, {})

    async def test_brute_force_is_throttled_and_recovers(self):
        now = [1000.0]
        authenticator = Auth(TEST_ACCOUNT, clock=lambda: now[0])
        for _ in range(5):
            with self.assertRaises(LoginFailed):
                await authenticator.login('test-admin', '')
        with self.assertRaises(TooManyAttempts):
            await authenticator.login('test-admin', TEST_PASSWORD)
        now[0] += 61
        token = await authenticator.login('test-admin', TEST_PASSWORD)
        self.assertTrue(authenticator.valid(token))

    async def test_expiry_logout_and_restart_revoke_access(self):
        now = [10.0]
        authenticator = Auth(TEST_ACCOUNT, ttl=30, clock=lambda: now[0])
        token = authenticator.issue()
        self.assertTrue(authenticator.valid(token))
        self.assertNotIn(token, authenticator.sessions)
        now[0] += 31
        self.assertFalse(authenticator.valid(token))
        token = authenticator.issue()
        authenticator.logout(token)
        self.assertFalse(authenticator.valid(token))
        token = authenticator.issue()
        self.assertFalse(Auth(TEST_ACCOUNT).valid(token))

if __name__ == '__main__':
    unittest.main()
