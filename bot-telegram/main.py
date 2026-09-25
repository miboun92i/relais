"""Relais : connexion Telegram et API privée du panel."""
import asyncio
import logging
import os
import random
from pathlib import Path
from urllib.parse import urlparse
from aiohttp import ClientSession, ClientTimeout, web
from dotenv import load_dotenv
from telethon import TelegramClient, events, functions, types
from core import Engine, Store
from auth import Auth, LoginFailed, TooManyAttempts

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv('DATA_DIR', str(ROOT))).resolve()
logger = logging.getLogger(__name__)

def make_app(engine, authenticator, origins, is_connected, provider):
    @web.middleware
    async def guard(request, handler):
        origin = request.headers.get('Origin')
        headers = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
        if origin and origin not in origins:
            return web.json_response({'error': 'Origine du panel non autorisée.'}, status=403, headers=headers)
        if origin:
            headers.update({'Access-Control-Allow-Origin': origin, 'Vary': 'Origin', 'Access-Control-Allow-Headers': 'Authorization, Content-Type', 'Access-Control-Allow-Methods': 'GET, POST, OPTIONS'})
        if request.method == 'OPTIONS':
            return web.Response(status=204, headers=headers)
        authorization = request.headers.get('Authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        login_request = request.path == '/api/login' and request.method == 'POST'
        if not login_request and not authenticator.valid(token):
            return web.json_response({'error': 'Connectez-vous pour accéder à cet espace.'}, status=401, headers=headers)
        request['session_token'] = token
        try:
            response = await handler(request)
        except ValueError as error:
            logger.exception('Panel : %s: %s', type(error).__name__, error)
            response = web.json_response({'error': str(error)}, status=400)
        except web.HTTPException as error:
            response = web.json_response({'error': error.reason}, status=error.status)
        except Exception as error:
            logger.exception('Panel : %s: %s', type(error).__name__, error)
            response = web.json_response({'error': 'La demande a échoué. Vérifiez Telegram et le fournisseur IA. Pour un envoi, vérifiez Telegram avant de réessayer.'}, status=503)
        response.headers.update(headers)
        return response

    app = web.Application(middlewares=[guard], client_max_size=150000)
    def chat_id(request):
        ident = int(request.match_info['chat'])
        if not engine.store.chat(ident):
            raise web.HTTPNotFound(reason='Conversation inconnue.')
        return ident
    async def body(request):
        value = await request.json()
        if not isinstance(value, dict):
            raise ValueError('Objet JSON attendu.')
        return value
    async def login(request):
        if request.content_length and request.content_length > 4096:
            raise web.HTTPRequestEntityTooLarge(max_size=4096, actual_size=request.content_length)
        data = await body(request)
        try:
            token = await authenticator.login(data.get('username'), data.get('password'))
        except LoginFailed:
            return web.json_response({'error': 'Identifiant ou mot de passe incorrect.'}, status=401)
        except TooManyAttempts:
            return web.json_response({'error': 'Trop de tentatives. Réessayez dans une minute.'}, status=429,
                                     headers={'Retry-After': '60'})
        return web.json_response({'token': token, 'expires_in': authenticator.ttl,
                                  'username': authenticator.account['username']})
    async def logout(request):
        authenticator.logout(request['session_token'])
        return web.json_response({'ok': True})
    async def state(request):
        return web.json_response({'chats': engine.store.chats(), 'settings': engine.store.settings(), 'connected': is_connected(), 'provider': provider, 'usage': engine.store.usage(), 'last_error': engine.last_error})
    async def messages(request):
        return web.json_response({'messages': engine.store.messages(chat_id(request))})
    async def mode(request):
        ident = chat_id(request)
        value = (await body(request)).get('mode')
        if value not in ('auto', 'manual'):
            raise ValueError('Mode inconnu.')
        engine.set_mode(ident, value)
        return web.json_response({'ok': True})
    async def read(request):
        engine.store.read(chat_id(request))
        return web.json_response({'ok': True})
    async def reply(request):
        ident = chat_id(request)
        data = await body(request)
        text, request_id = data.get('text'), data.get('request_id')
        if not isinstance(text, str) or not text.strip() or len(text) > 4000:
            raise ValueError('Le message doit contenir entre 1 et 4 000 caractères.')
        if not isinstance(request_id, str) or not 16 <= len(request_id) <= 80:
            raise ValueError('Identifiant de demande manquant.')
        if not is_connected():
            raise ValueError('Telegram est déconnecté.')
        ident_sent = await engine.reply(ident, text.strip(), request_id)
        return web.json_response({'ok': True, 'message_id': ident_sent})
    async def draft(request):
        return web.json_response({'text': await engine.draft(chat_id(request))})
    async def settings(request):
        value = await body(request)
        # More permissive: accept missing optional fields
        tone = value.get('tone', '')
        catalog = value.get('catalog', '')
        faq = value.get('faq', '')
        enabled = value.get('enabled', False)
        daily_limit = value.get('daily_limit', 100)
        glossary = value.get('glossary', [])

        if not isinstance(tone, str) or len(tone) > 6000:
            raise ValueError('Champ tone invalide.')
        if not isinstance(catalog, str) or len(catalog) > 12000:
            raise ValueError('Champ catalog invalide.')
        if not isinstance(faq, str) or len(faq) > 12000:
            raise ValueError('Champ faq invalide.')
        if not tone.strip():
            raise ValueError('Les consignes (ton) ne peuvent pas être vides.')
        if type(enabled) is not bool:
            raise ValueError('Activation invalide.')
        if type(daily_limit) is not int or not 1 <= daily_limit <= 1000:
            # Try to coerce from string/float
            try:
                daily_limit = int(daily_limit)
                if not 1 <= daily_limit <= 1000:
                    raise ValueError()
            except (TypeError, ValueError):
                raise ValueError('Le plafond doit être compris entre 1 et 1 000.')
        if not isinstance(glossary, list) or len(glossary) > 50:
            glossary = []
        clean_glossary = []
        for entry in glossary:
            if not isinstance(entry, dict):
                continue
            expr = entry.get('expression', '')
            repl = entry.get('replacement', '')
            if isinstance(expr, str) and expr.strip() and len(expr) <= 100 and isinstance(repl, str) and len(repl) <= 200:
                clean_glossary.append({'expression': expr.strip(), 'replacement': repl})

        cleaned = {
            'tone': tone.strip(),
            'catalog': catalog,
            'faq': faq,
            'enabled': enabled,
            'daily_limit': daily_limit,
            'glossary': clean_glossary,
        }
        engine.settings(cleaned)
        return web.json_response({'ok': True})
    app.add_routes([web.post('/api/login', login), web.post('/api/logout', logout), web.get('/api/state', state), web.get('/api/chats/{chat}/messages', messages), web.post('/api/chats/{chat}/mode', mode), web.post('/api/chats/{chat}/read', read), web.post('/api/chats/{chat}/reply', reply), web.post('/api/chats/{chat}/draft', draft), web.post('/api/settings', settings)])
    return app

async def main():
    os.umask(0o077)
    load_dotenv(ROOT / '.env')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    account_path = DATA_DIR / 'panel-account.json'
    account_env = os.getenv('PANEL_ACCOUNT_JSON')
    if account_env and not account_path.exists():
        account_path.write_text(account_env, encoding='utf-8')
        print('Bootstrap : panel-account.json écrit depuis l\'environnement.', flush=True)
    session_path = DATA_DIR / 'compte.session'
    session_env = os.getenv('TELEGRAM_SESSION_GZ_B64')
    if session_env and not session_path.exists():
        import base64, gzip
        session_path.write_bytes(gzip.decompress(base64.b64decode(session_env)))
        print('Bootstrap : compte.session écrit depuis l\'environnement.', flush=True)
    authenticator = Auth.from_file(account_path)
    if not os.getenv('TELEGRAM_API_ID') or not os.getenv('TELEGRAM_API_HASH'):
        raise SystemExit('Remplis TELEGRAM_API_ID et TELEGRAM_API_HASH dans .env.')
    provider = os.getenv('AI_PROVIDER', 'ollama')
    if provider not in ('ollama', 'anthropic', 'openai'):
        raise SystemExit('AI_PROVIDER doit être ollama, anthropic ou openai.')
    if provider == 'ollama' and not os.getenv('OLLAMA_MODEL', '').strip():
        raise SystemExit('Renseigne OLLAMA_MODEL dans .env avec le nom du modèle installé sur ton serveur.')
    if provider == 'anthropic' and not os.getenv('ANTHROPIC_API_KEY'):
        raise SystemExit('ANTHROPIC_API_KEY est nécessaire avec Anthropic.')
    if provider == 'openai' and not os.getenv('OPENAI_API_KEY'):
        raise SystemExit('OPENAI_API_KEY est nécessaire avec OpenAI.')
    try:
        api_id = int(os.environ['TELEGRAM_API_ID'])
        ignored = {int(x.strip()) for x in os.getenv('IGNORE_CHAT_IDS', '').split(',') if x.strip()}
        port = int(os.getenv('PORT', '8787'))
    except ValueError:
        raise SystemExit('Les identifiants Telegram et le port doivent être des nombres.')
    origins = {s.strip().rstrip('/') for s in os.getenv('PANEL_ORIGINS', '').split(',') if s.strip()}
    if not origins or '*' in origins:
        raise SystemExit('Renseigne PANEL_ORIGINS avec l’adresse exacte du panel, sans chemin.')
    store = Store(DATA_DIR / 'conversations.sqlite3')
    # Conserver le choix enregistré, y compris une pause volontaire.
    client = TelegramClient(str(DATA_DIR / 'compte'), api_id, os.environ['TELEGRAM_API_HASH'])
    http = ClientSession(timeout=ClientTimeout(total=55))
    anthropic_client = None
    openai_client = None
    if provider == 'anthropic':
        from anthropic import AsyncAnthropic
        anthropic_client = AsyncAnthropic(api_key=os.environ['ANTHROPIC_API_KEY'], timeout=50, max_retries=0)
    elif provider == 'openai':
        from openai import AsyncOpenAI
        openai_client = AsyncOpenAI(api_key=os.environ['OPENAI_API_KEY'], timeout=50, max_retries=0)
    async def generate(prompt, messages):
        if provider == 'anthropic':
            result = await anthropic_client.messages.create(model=os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5'), max_tokens=500, system=prompt, messages=messages)
            return ''.join(block.text for block in result.content if block.type == 'text')
        if provider == 'openai':
            result = await openai_client.responses.create(
                model=os.getenv('OPENAI_MODEL', 'gpt-5-mini'),
                instructions=prompt,
                input=messages,
                max_output_tokens=500,
            )
            return result.output_text
        ollama_url = os.getenv('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
        parsed = urlparse(ollama_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ValueError('Adresse Ollama invalide.')
        model = os.getenv('OLLAMA_MODEL', '').strip()
        if not model:
            raise ValueError('Renseignez OLLAMA_MODEL avec le nom du modèle installé.')
        async with http.post(ollama_url + '/api/chat', json={'model': model, 'stream': False, 'messages': [{'role': 'system', 'content': prompt}] + messages, 'options': {'num_predict': 500}}, allow_redirects=False) as response:
            response.raise_for_status()
            return (await response.json())['message']['content']
    async def transport(chat_id, text):
        message = await client.send_message(chat_id, text, parse_mode=None, link_preview=False)
        return message.id
    async def mark_read(chat_id):
        messages = store.messages(chat_id, 1)
        if not messages:
            return
        await client.send_read_acknowledge(chat_id, max_id=messages[-1]['telegram_id'])
        print('Telegram : accusé de lecture accepté.', flush=True)

    async def simulate_typing(chat_id, text):
        duration = max(1.2, min((1.0 + len(text.strip()) / 35) * random.uniform(0.80, 1.25), 7.5))
        peer = await client.get_input_entity(chat_id)
        # Attendre la requête directement : les erreurs doivent remonter au moteur.
        try:
            remaining = duration
            while remaining > 0:
                await client(functions.messages.SetTypingRequest(peer, types.SendMessageTypingAction()))
                print(f'Telegram : saisie acceptée ; délai restant {remaining:.1f} s.', flush=True)
                step = min(remaining, 4.0)
                await asyncio.sleep(step)
                remaining -= step
        finally:
            await client(functions.messages.SetTypingRequest(peer, types.SendMessageCancelAction()))

    engine = Engine(
        store,
        transport,
        generate,
        mark_read=mark_read,
        simulate_typing=simulate_typing,
    )
    print(f'Code chargé : main={Path(__file__).resolve()} ; core={Path(__import__("core").__file__).resolve()}', flush=True)
    print('Comportements actifs : lecture Telegram → génération → écrit… (1,2–7,5 s) → réponse ; attente initiale aléatoire 1,8–3,8 s.', flush=True)
    runner = None
    try:
        await client.start()
        me = await client.get_me()
        @client.on(events.NewMessage())
        async def event_message(event):
            try:
                if not event.is_private or event.chat_id in ignored or event.chat_id == 777000:
                    return
                if event.chat_id == me.id:
                    if event.out and event.raw_text in ('/ia pause', '/ia reprendre', '/ia statut'):
                        action = event.raw_text.split()[-1]
                        value = store.settings()
                        if action != 'statut':
                            value['enabled'] = action == 'reprendre'
                            engine.settings(value)
                        await client.send_message(me.id, f"IA {'active' if value['enabled'] else 'en pause'} · {store.usage()} demandes aujourd’hui.")
                    return
                if event.out and store.chat(event.chat_id):
                    await engine.outgoing(event.chat_id, event.id, event.raw_text or '[Média — à consulter dans Telegram]', event.date.timestamp())
                    return
                peer = await event.get_chat()
                if getattr(peer, 'bot', False):
                    return
                name = ' '.join(filter(None, [getattr(peer, 'first_name', ''), getattr(peer, 'last_name', '')])) or getattr(peer, 'username', '') or str(event.chat_id)
                store.ensure(event.chat_id, name)
                # Telegram envoie aussi ses salutations de démarrage en stickers,
                # y compris animés. Les autres médias restent à vérifier à la main.
                is_sticker = getattr(event.message, 'sticker', None) is not None
                text = event.raw_text
                if is_sticker and not (text or '').strip():
                    text = '[Sticker Telegram reçu — peut être une salutation. Réponds naturellement et brièvement selon le contexte, sans supposer le contenu visuel.]'
                elif not text:
                    text = '[Média — à consulter dans Telegram]'
                if event.out:
                    await engine.outgoing(event.chat_id, event.id, text, event.date.timestamp())
                elif store.add(event.chat_id, event.id, 'client', text, event.date.timestamp()):
                    if event.media and not is_sticker:
                        engine.set_mode(event.chat_id, 'manual')
                    else:
                        engine.incoming(event.chat_id)
            except Exception as error:
                logger.exception('Synchronisation ; conversation %s : %s: %s', event.chat_id, type(error).__name__, error)
                engine.report_error(event.chat_id, 'Erreur de synchronisation. IA globale inchangée ; consultez les logs.')
        app = make_app(engine, authenticator, origins, client.is_connected, provider)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        # 0.0.0.0 is required for Railway / cloud hosting
        host = os.getenv('HOST', '0.0.0.0')
        await web.TCPSite(runner, host, port).start()
        state = 'active' if store.settings()['enabled'] else 'en pause'
        print(f'Panel disponible sur {host}:{port}. IA {state} : état enregistré conservé.', flush=True)
        print('Connectez-vous au panel avec le compte créé par configurer_compte.py.')
        await client.run_until_disconnected()
    finally:
        await engine.close()
        if runner:
            await runner.cleanup()
        await client.disconnect()
        await http.close()
        if anthropic_client:
            await anthropic_client.close()
        if openai_client:
            await openai_client.close()
        store.db.close()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('\nRelais arrêté.')
