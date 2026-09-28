import asyncio
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from core import Store, Engine
from style import (
    conversation_memory, dedupe_reply, is_too_similar, pick_fallback,
    EMPTY_FALLBACKS, BANNED_FALLBACKS, BASE_PROMPT, clip_reply, sanitize_reply,
    looks_like_tease_offer, looks_like_menu_question, looks_like_tariff_dump,
    is_banned_phrase, client_asks_payment, client_chose_presta, payment_reply,
    fix_paypal_doublon, looks_like_choice_closer,
)
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

    async def test_read_typing_send_order_and_short_delay(self):
        order = []
        async def mark_read(chat_id):
            order.append('read')
        async def typing_pulse(chat_id):
            if 'typing' not in order:
                order.append('typing')
        async def cancel_typing(chat_id):
            order.append('cancel_typing')
        async def generate(prompt, messages):
            order.append('generate')
            self.assertIn('typing', order)  # typing demarre avant / pendant generate
            return 'Bonjour cash'
        async def send(chat_id, text):
            order.append('send')
            return 123
        self.engine.mark_read = mark_read
        self.engine.typing_pulse = typing_pulse
        self.engine.cancel_typing = cancel_typing
        self.engine.simulate_typing = None
        self.engine.generate = generate
        self.engine.transport = send
        self.engine.read_delay_min, self.engine.read_delay_max = 3.0, 8.0
        self.engine.delay_min, self.engine.delay_max = 1.0, 2.0
        sleeps = []
        async def sleep(seconds):
            sleeps.append(seconds)
            order.append('delay_read' if len(sleeps) == 1 else 'delay')
        with patch('engine_reply.random.uniform', side_effect=[5.0, 1.5]) as uniform, patch('engine_reply.asyncio.sleep', side_effect=sleep):
            await self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch)
        self.assertEqual(uniform.call_args_list[0].args, (3.0, 8.0))
        self.assertEqual(uniform.call_args_list[1].args, (1.0, 2.0))
        self.assertEqual(sleeps, [5.0, 1.5])
        self.assertEqual(order[:5], ['delay_read', 'read', 'delay', 'typing', 'generate'])
        self.assertIn('send', order)
        self.assertLess(order.index('delay_read'), order.index('read'))
        self.assertLess(order.index('read'), order.index('delay'))
        self.assertLess(order.index('delay'), order.index('typing'))
        self.assertLess(order.index('typing'), order.index('generate'))
        self.assertLess(order.index('generate'), order.index('send'))

    async def test_manual_takeover_during_typing_discards_reply(self):
        self.release.set()
        async def typing(chat_id, text):
            self.engine.set_mode(chat_id, 'manual')
        self.engine.simulate_typing = typing
        await self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch)
        self.assertEqual(self.sent, [])

    async def test_new_message_during_read_discards_old_generation(self):
        async def mark_read(chat_id):
            self.store.add(chat_id, 2, 'client', 'Autre question')
        self.engine.mark_read = mark_read
        await self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch)
        self.assertFalse(self.started.is_set())
        self.assertEqual(self.sent, [])

    async def test_shutdown_during_typing_cleans_up_without_send(self):
        self.release.set()
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def typing(chat_id, text):
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        self.engine.simulate_typing = typing
        self.engine.incoming(1)
        await asyncio.wait_for(entered.wait(), 1)
        await self.engine.close()
        self.assertTrue(cleaned.is_set())
        self.assertEqual(self.sent, [])

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
        await self.engine.outgoing(1, 101, 'réponse test')
        self.assertEqual(self.store.chat(1)['mode'], 'auto')
        self.assertEqual(len(self.store.messages(1)), 2)
        self.assertEqual(self.store.messages(1)[-1]['source'], 'ai')

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

    async def test_history_injected_includes_recent_exchanges(self):
        self.store.add(1, 2, 'ai', 'nudes 25 cam 20 canal 50')
        self.store.add(1, 3, 'client', 'cam alors')
        captured = {}
        async def generate(prompt, messages):
            captured['prompt'] = prompt
            captured['messages'] = messages
            return 'ok paypal et on book'
        self.engine.generate = generate
        text = await self.engine.draft(1)
        self.assertTrue(text)
        roles = [m['role'] for m in captured['messages']]
        self.assertEqual(roles[0], 'user')
        self.assertIn('assistant', roles)
        contents = [m['content'] for m in captured['messages']]
        self.assertIn('cam alors', contents)
        self.assertIn('nudes 25 cam 20 canal 50', contents)
        self.assertIn('MEMOIRE CONVERSATION', captured['prompt'])
        self.assertIn('ANTI-REPETITION', captured['prompt'])

    async def test_fallback_does_not_spam_same_phrase(self):
        recent = [EMPTY_FALLBACKS[0], EMPTY_FALLBACKS[0]]
        seen = set()
        for _ in range(12):
            seen.add(pick_fallback(list(recent)))
        self.assertGreaterEqual(len(seen), 2)
        self.assertNotIn(EMPTY_FALLBACKS[0], seen)
        self.assertNotIn('ok sois cash tu veux quoi', seen)

    async def test_dedupe_avoids_repeating_recent_ai(self):
        recent = ['tu prends nudes cam ou canal ?']
        out = dedupe_reply('tu prends nudes cam ou canal ?', recent)
        self.assertFalse(is_too_similar(out, recent))
        self.assertNotEqual(out, 'tu prends nudes cam ou canal ?')

    async def test_no_requestion_when_already_answered(self):
        history = [
            {'source': 'client', 'text': 'salut'},
            {'source': 'ai', 'text': 'nudes cam ou canal tu prends quoi'},
            {'source': 'client', 'text': 'cam'},
            {'source': 'ai', 'text': '20e les 10 min paypal'},
            {'source': 'client', 'text': 'ok'},
        ]
        note = conversation_memory(history)
        self.assertIn('deja', note.lower())
        self.assertTrue(
            'menu' in note.lower() or 'tarif' in note.lower() or 'question' in note.lower() or 'repondu' in note.lower()
        )
        captured = {}
        async def generate(prompt, messages):
            captured['prompt'] = prompt
            # Simule une IA qui reposerait la meme question
            return 'nudes cam ou canal tu prends quoi'
        for row in history[1:]:
            self.store.add(1, 10 + len(self.store.messages(1)), row['source'], row['text'])
        self.engine.generate = generate
        text = await self.engine.draft(1)
        self.assertIn('MEMOIRE CONVERSATION', captured['prompt'])
        self.assertFalse(is_too_similar(text, ['nudes cam ou canal tu prends quoi']))

    async def test_empty_ai_uses_diversified_fallback(self):
        self.store.add(1, 2, 'ai', EMPTY_FALLBACKS[0])
        async def generate(prompt, messages):
            return '   '
        self.engine.generate = generate
        text = await self.engine.draft(1)
        self.assertTrue(text.strip())
        self.assertNotEqual(text, EMPTY_FALLBACKS[0])


    async def test_draft_rewrites_tease_offer_when_video_will_send(self):
        async def generate(prompt, messages):
            self.assertIn('PART MAINTENANT', prompt)
            return "je peux t'envoyer un petit tease"
        async def send_media(chat_id, path, caption=None):
            return 900
        self.engine.generate = generate
        self.engine.send_media = send_media
        self.engine.get_active_teasers = lambda: [{'id': 't1', 'path': '/tmp/x.mp4', 'primary': True, 'created': 1}]
        self.store.add(1, 2, 'client', 'envoie un tease')
        text = await self.engine.draft(1)
        self.assertFalse(looks_like_tease_offer(text))


    async def test_draft_jenvoi_ou_returns_paypal_not_closer(self):
        self.store.save_settings({
            **self.store.settings(),
            'enabled': True,
            'catalog': 'cam 50e nudes 25e PayPal paypal.me/offlexa',
        })
        self.store.add(1, 2, 'client', 'cam a 50 euro')
        self.store.add(1, 3, 'client', 'jenvoi ou')
        async def generate(prompt, messages):
            self.fail('IA ne doit pas etre appelee pour une demande de paiement')
        self.engine.generate = generate
        text = await self.engine.draft(1)
        self.assertIn('paypal.me/offlexa', text.lower())
        self.assertNotIn('tu book', text.lower())
        self.assertNotIn('balance ton choix', text.lower())

    async def test_draft_chose_cam_replaces_choice_closer(self):
        self.store.save_settings({
            **self.store.settings(),
            'enabled': True,
            'catalog': 'cam 50e paypal.me/offlexa',
        })
        self.store.add(1, 2, 'client', 'cam a 50 euro')
        self.store.add(1, 3, 'client', 'ok')
        async def generate(prompt, messages):
            return 'tu book quoi'
        self.engine.generate = generate
        text = await self.engine.draft(1)
        self.assertIn('paypal.me/offlexa', text.lower())
        self.assertFalse(looks_like_choice_closer(text))

    async def test_auto_reply_after_teaser_not_offer_tease(self):
        order = []
        async def generate(prompt, messages):
            return "je peux t'envoyer un petit tease"
        async def send_media(chat_id, path, caption=None):
            order.append('media')
            return 901
        async def send(chat_id, text):
            order.append(('text', text))
            return 902
        self.store.add(1, 2, 'client', 'montre un apercu')
        self.engine.generate = generate
        self.engine.send_media = send_media
        self.engine.transport = send
        self.engine.get_active_teasers = lambda: [{'id': 't1', 'path': '/tmp/x.mp4', 'primary': True, 'created': 1}]
        self.engine.simulate_typing = None
        await self.engine.auto_reply(1, self.store.chat(1)['revision'], self.engine.epoch)
        self.assertIn('media', order)
        texts = [item[1] for item in order if isinstance(item, tuple) and item[0] == 'text']
        self.assertEqual(len(texts), 1)
        self.assertFalse(looks_like_tease_offer(texts[0]))

    async def test_draft_anti_menu_when_already_proposed(self):
        self.store.add(1, 2, 'ai', 'nudes cam ou canal tu prends quoi')
        self.store.add(1, 3, 'ai', 'nudes 25 cam 20 canal 50')
        self.store.add(1, 4, 'client', 'et pour une scene')
        async def generate(prompt, messages):
            self.assertIn('menu nudes/cam/canal DEJA', prompt)
            return 'nudes cam ou canal tu prends quoi'
        self.engine.generate = generate
        text = await self.engine.draft(1)
        self.assertFalse(looks_like_menu_question(text))


    async def test_catch_up_pending_queues_waiting_chats(self):
        self.store.ensure(2, 'Autre')
        self.store.add(2, 1, 'client', 'yo')
        self.release.set()
        n = self.engine.catch_up_pending()
        self.assertGreaterEqual(n, 1)
        await asyncio.sleep(0.05)
        await self.engine.close()

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


class StyleHelperTests(unittest.TestCase):
    def test_base_prompt_has_anti_repetition(self):
        self.assertIn('ANTI-REPETITION', BASE_PROMPT)
        self.assertIn('ANTI-MENU', BASE_PROMPT)
        self.assertNotIn('tu papotes. tu prends nudes cam ou canal ?', BASE_PROMPT)

    def test_is_too_similar_detects_near_duplicates(self):
        self.assertTrue(is_too_similar('tu prends nudes cam ou canal', ['nudes cam ou canal tu prends quoi']))
        self.assertFalse(is_too_similar('ok paypal envoie', ['nudes cam ou canal tu prends quoi']))

    def test_clip_reply_never_leaves_dangling_les(self):
        # Ancien bug: "ca se fait en cam 20e les" (coupe mid-phrase)
        longish = 'ca se fait en cam 20e les 10 min tu prends maintenant mon coeur vite svp'
        out = clip_reply(longish, max_words=12)
        self.assertFalse(out.rstrip('?').endswith(' les'))
        self.assertFalse(out.rstrip('?').endswith(' de'))
        self.assertIn('20e', out)
        self.assertIn('10 min', out)
        complete = 'ca se fait en cam 20e les 10 min tu prends ?'
        self.assertEqual(clip_reply(complete, max_words=12), complete)
        # Texte deja coupe foireusement: on retire le dangling
        broken = clip_reply('ca se fait en cam 20e les', max_words=12)
        self.assertFalse(broken.endswith('les'))

    def test_clip_reply_keeps_tarif_paypal_together(self):
        text = 'nudes 25e cam 20e canal 50e PayPal paypal.me/demo go'
        out = clip_reply(text, max_words=12, soft_max=22)
        self.assertIn('PayPal', out)
        self.assertIn('paypal.me/demo', out)
        self.assertIn('25e', out)

    def test_sanitize_blocks_menu_replay_and_tariff_stack(self):
        hist = [
            {'source': 'ai', 'text': 'nudes 25 cam 20 canal 50'},
            {'source': 'ai', 'text': 'nudes cam ou canal tu prends quoi'},
        ]
        out = sanitize_reply('nudes cam ou canal tu prends quoi', hist)
        self.assertFalse(looks_like_menu_question(out))
        dump = sanitize_reply('nudes 25e cam 20e canal 50e tout compris', hist)
        self.assertFalse(looks_like_tariff_dump(dump))

    def test_sanitize_blocks_tease_offer_after_video(self):
        hist = [{'source': 'ai', 'text': '[Vidéo avant-goût]'}]
        offer = "je peux t'envoyer un petit tease"
        self.assertTrue(looks_like_tease_offer(offer))
        out = sanitize_reply(offer, hist, teaser_sending_now=True)
        self.assertFalse(looks_like_tease_offer(out))
        self.assertTrue(out.strip())

    def test_banned_cash_phrase_removed_from_fallbacks(self):
        self.assertNotIn('ok sois cash tu veux quoi', EMPTY_FALLBACKS)
        self.assertNotIn('nudes cam ou canal tu prends quoi', EMPTY_FALLBACKS)
        self.assertNotIn('balance ton choix', EMPTY_FALLBACKS)
        self.assertNotIn('et donc', EMPTY_FALLBACKS)
        self.assertNotIn('dis juste ce que tu book', EMPTY_FALLBACKS)
        joined = ' '.join(EMPTY_FALLBACKS + BANNED_FALLBACKS)
        self.assertIn('ok sois cash tu veux quoi', joined)  # still listed as banned
        for phrase in EMPTY_FALLBACKS:
            self.assertFalse(is_banned_phrase(phrase), phrase)

    def test_sanitize_and_dedupe_never_return_banned_cash(self):
        banned = 'ok sois cash tu veux quoi'
        self.assertTrue(is_banned_phrase(banned))
        out = sanitize_reply(banned, [])
        self.assertFalse(is_banned_phrase(out))
        self.assertNotEqual(out, banned)
        out2 = dedupe_reply(banned, [])
        self.assertFalse(is_banned_phrase(out2))
        out3 = dedupe_reply('Ok sois cash tu veux quoi !!!', ['et donc'])
        self.assertFalse(is_banned_phrase(out3))
        for _ in range(20):
            self.assertFalse(is_banned_phrase(pick_fallback([banned])))

    def test_client_chose_cam_no_reask_choice(self):
        hist = [
            {'source': 'client', 'text': 'cam a 50 euro'},
            {'source': 'ai', 'text': 'ok'},
            {'source': 'client', 'text': 'cam a 50 euro'},
        ]
        self.assertTrue(client_chose_presta(hist))
        catalog = 'nudes 25 cam 50 canal 50 PayPal paypal.me/offlexa'
        out = sanitize_reply('tu book quoi', hist, catalog=catalog)
        self.assertIn('paypal.me/offlexa', out.lower())
        self.assertFalse(looks_like_choice_closer(out))
        out2 = sanitize_reply('balance ton choix', hist, catalog=catalog)
        self.assertIn('paypal.me', out2.lower())
        out3 = sanitize_reply('dis juste ce que tu book', hist, catalog=catalog)
        self.assertIn('paypal.me', out3.lower())

    def test_jenvoi_ou_returns_paypal_link(self):
        self.assertTrue(client_asks_payment('jenvoi ou'))
        self.assertTrue(client_asks_payment('jenvoi ou les 50 euro'))
        hist = [
            {'source': 'client', 'text': 'cam a 50 euro'},
            {'source': 'client', 'text': 'jenvoi ou les 50 euro'},
        ]
        catalog = 'cam 50e paypal.me/offlexa'
        out = sanitize_reply('et donc', hist, catalog=catalog)
        self.assertIn('paypal.me/offlexa', out.lower())
        pay = payment_reply(catalog, '', hist)
        self.assertIn('paypal.me/offlexa', pay.lower())

    def test_no_paypal_paypal_doublon(self):
        raw = 'PayPal paypal paypal.me/offlexa'
        fixed = fix_paypal_doublon(raw)
        self.assertEqual(fixed, 'PayPal paypal.me/offlexa')
        self.assertNotIn('PayPal paypal ', fixed + ' ')
        # smash ne doit pas produire PayPal.me
        from style import smash_style
        s = smash_style(raw)
        self.assertIn('paypal.me/offlexa', s.lower())
        self.assertNotIn('PayPal.me', s)



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
