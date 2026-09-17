"""Runtime commercial : le panel démarre même avant l'autorisation Telegram."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
from contextlib import suppress
from commercial_security import LicenseGate, lock_volume
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout, web
from dotenv import load_dotenv
from telethon import TelegramClient, events, functions, types

from auth import Auth
from commercial_panel import add_commercial_routes
from runtime_config import data_directory
from telegram_events import route_private_message
from core import Engine, Store
from licensing import load_license_from_env
from main import make_app
from telegram_onboarding import TelegramOnboarding

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


async def main():
    os.umask(0o077)
    data_dir = data_directory(ROOT)
    test_mode = os.getenv("COMMERCIAL_TEST_MODE", "0") == "1"
    required = os.getenv("LICENSE_REQUIRED", "1") != "0"
    claims = load_license_from_env(required=required)
    if claims is None:
        if not test_mode:
            raise SystemExit("Licence commerciale requise.")
        claims = SimpleNamespace(
            license_id="test-mode",
            customer_id="railway-test",
            plan="test",
            max_accounts=1,
            expires_at=None,
        )
    volume_lock = lock_volume(data_dir)
    gate = None
    if not test_mode:
        gate = LicenseGate(claims, os.environ['LICENSE_TOKEN'], os.environ['LICENSE_PUBLIC_KEY'],
                           os.getenv('LICENSE_SERVER_URL', ''), data_dir)
        try:
            await gate.refresh()
        except ValueError:
            pass  # Keep the authenticated panel available; automation stays blocked.
    def require_license():
        if gate:
            gate.require()
    authenticator = Auth.from_file(data_dir / "panel-account.json")

    if not os.getenv("TELEGRAM_API_ID") or not os.getenv("TELEGRAM_API_HASH"):
        raise SystemExit("TELEGRAM_API_ID et TELEGRAM_API_HASH sont requis.")
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"])
        ignored = {int(x.strip()) for x in os.getenv("IGNORE_CHAT_IDS", "").split(",") if x.strip()}
        port = int(os.getenv("PORT", "8787"))
    except ValueError as exc:
        raise SystemExit("Les identifiants Telegram et le port doivent être des nombres.") from exc

    origins = {s.strip().rstrip("/") for s in os.getenv("PANEL_ORIGINS", "").split(",") if s.strip()}
    if not origins or "*" in origins:
        raise SystemExit("PANEL_ORIGINS doit contenir l'adresse exacte du panel.")

    provider = os.getenv("AI_PROVIDER", "openai")
    if provider not in ("ollama", "anthropic", "openai"):
        raise SystemExit("AI_PROVIDER doit être ollama, anthropic ou openai.")

    store = Store(data_dir / "conversations.sqlite3")
    client = TelegramClient(str(data_dir / "compte"), api_id, os.environ["TELEGRAM_API_HASH"])
    def bind_account(user_id):
        path = data_dir / '.telegram-user-id'
        if path.exists() and path.read_text().strip() != str(user_id):
            raise ValueError('Ce volume appartient à un autre compte Telegram. Utilisez une nouvelle installation.')
        if not path.exists():
            path.write_text(str(user_id))
            path.chmod(0o600)
    onboarding = TelegramOnboarding(client, account_validator=bind_account)
    http = ClientSession(timeout=ClientTimeout(total=55))
    anthropic_client = None
    openai_client = None

    if provider == "anthropic":
        from anthropic import AsyncAnthropic
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise SystemExit("ANTHROPIC_API_KEY est requis.")
        anthropic_client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=50, max_retries=0)
    elif provider == "openai":
        from openai import AsyncOpenAI
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY est requis.")
        openai_client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=50, max_retries=0)

    async def generate(prompt, messages):
        require_license()
        if provider == "anthropic":
            result = await anthropic_client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
                max_tokens=500,
                system=prompt,
                messages=messages,
            )
            return "".join(block.text for block in result.content if block.type == "text")
        if provider == "openai":
            result = await openai_client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                instructions=prompt,
                input=messages,
                max_output_tokens=500,
            )
            return result.output_text
        ollama_url = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        parsed = urlparse(ollama_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("Adresse Ollama invalide.")
        model = os.getenv("OLLAMA_MODEL", "").strip()
        if not model:
            raise ValueError("OLLAMA_MODEL est requis.")
        async with http.post(
            ollama_url + "/api/chat",
            json={"model": model, "stream": False, "messages": [{"role": "system", "content": prompt}] + messages,
                  "options": {"num_predict": 500}},
            allow_redirects=False,
        ) as response:
            response.raise_for_status()
            return (await response.json())["message"]["content"]

    async def require_authorized():
        require_license()
        await onboarding.ensure_connected()
        if not await client.is_user_authorized():
            raise ValueError("Aucun compte Telegram n'est connecté.")

    async def transport(chat_id, text):
        await require_authorized()
        bind_account((await client.get_me()).id)
        require_license()
        message = await client.send_message(chat_id, text, parse_mode=None, link_preview=False)
        return message.id

    async def mark_read(chat_id):
        await require_authorized()
        messages = store.messages(chat_id, 1)
        if messages:
            await client.send_read_acknowledge(chat_id, max_id=messages[-1]["telegram_id"])

    async def simulate_typing(chat_id, text):
        await require_authorized()
        duration = max(1.2, min((1.0 + len(text.strip()) / 35) * random.uniform(0.80, 1.25), 7.5))
        peer = await client.get_input_entity(chat_id)
        try:
            remaining = duration
            while remaining > 0:
                await client(functions.messages.SetTypingRequest(peer, types.SendMessageTypingAction()))
                step = min(remaining, 4.0)
                await asyncio.sleep(step)
                remaining -= step
        finally:
            await client(functions.messages.SetTypingRequest(peer, types.SendMessageCancelAction()))

    engine = Engine(store, transport, generate, mark_read=mark_read, simulate_typing=simulate_typing)

    @client.on(events.NewMessage())
    async def event_message(event):
        try:
            if not await client.is_user_authorized():
                return
            require_license()
            me = await client.get_me()
            bind_account(me.id)
            if not event.is_private or event.chat_id in ignored or event.chat_id == 777000:
                return
            if event.chat_id == me.id:
                if event.out and event.raw_text in ("/ia pause", "/ia reprendre", "/ia statut"):
                    action = event.raw_text.split()[-1]
                    value = store.settings()
                    if action != "statut":
                        value["enabled"] = action == "reprendre"
                        engine.settings(value)
                    await client.send_message(me.id, f"IA {'active' if value['enabled'] else 'en pause'} · {store.usage()} demandes aujourd’hui.")
                return
            if event.out and store.chat(event.chat_id):
                await engine.outgoing(event.chat_id, event.id, event.raw_text or "[Média — à consulter dans Telegram]", event.date.timestamp())
                return
            peer = await event.get_chat()
            if getattr(peer, "bot", False):
                return
            name = " ".join(filter(None, [getattr(peer, "first_name", ""), getattr(peer, "last_name", "")])) or getattr(peer, "username", "") or str(event.chat_id)
            await route_private_message(engine, event, name)
        except Exception as error:
            logger.exception("Synchronisation commerciale ; conversation %s : %s", event.chat_id, error)
            engine.report_error(event.chat_id, "Erreur de synchronisation. Consultez les logs.")

    runner = None
    monitor = asyncio.create_task(gate.monitor()) if gate else None
    try:
        await onboarding.ensure_connected()
        async def connection_ready():
            return client.is_connected() and await client.is_user_authorized()
        app = make_app(engine, authenticator, origins, connection_ready, provider)
        add_commercial_routes(app, onboarding, claims, gate)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        host = os.getenv("HOST", "0.0.0.0")
        await web.TCPSite(runner, host, port).start()
        status = await onboarding.status()
        print(
            f"Panel commercial disponible sur {host}:{port} · Telegram "
            f"{'connecté' if status.authorized else 'à connecter depuis le panel'} · licence {claims.plan}.",
            flush=True,
        )
        print(f"IA {'active' if store.settings()['enabled'] else 'en pause'} : état enregistré conservé ; données persistantes configurées.", flush=True)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        if monitor:
            monitor.cancel()
            with suppress(asyncio.CancelledError):
                await monitor
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
        volume_lock.close()
