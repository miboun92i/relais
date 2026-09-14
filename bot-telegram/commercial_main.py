"""Runtime commercial : le panel démarre même avant l'autorisation Telegram."""
from __future__ import annotations

import asyncio
import logging
import os
import random
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import ClientSession, ClientTimeout, web
from dotenv import load_dotenv
from telethon import TelegramClient, events, functions, types

from auth import Auth
from commercial_panel import add_commercial_routes
from core import Engine, Store
from licensing import load_license_from_env
from main import make_app
from telegram_onboarding import TelegramOnboarding

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(ROOT))).resolve()
logger = logging.getLogger(__name__)


async def main():
    os.umask(0o077)
    load_dotenv(ROOT / ".env")
    claims = load_license_from_env(required=True)
    authenticator = Auth.from_file(DATA_DIR / "panel-account.json")

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

    store = Store(DATA_DIR / "conversations.sqlite3")
    client = TelegramClient(str(DATA_DIR / "compte"), api_id, os.environ["TELEGRAM_API_HASH"])
    onboarding = TelegramOnboarding(client)
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
        await onboarding.ensure_connected()
        if not await client.is_user_authorized():
            raise ValueError("Aucun compte Telegram n'est connecté.")

    async def transport(chat_id, text):
        await require_authorized()
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
            me = await client.get_me()
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
            store.ensure(event.chat_id, name)
            is_sticker = getattr(event.message, "sticker", None) is not None
            text = event.raw_text
            if is_sticker and not (text or "").strip():
                text = "[Sticker Telegram reçu — peut être une salutation. Réponds naturellement et brièvement selon le contexte.]"
            elif not text:
                text = "[Média — à consulter dans Telegram]"
            if event.out:
                await engine.outgoing(event.chat_id, event.id, text, event.date.timestamp())
            elif store.add(event.chat_id, event.id, "client", text, event.date.timestamp()):
                if event.media and not is_sticker:
                    engine.set_mode(event.chat_id, "manual")
                else:
                    engine.incoming(event.chat_id)
        except Exception as error:
            logger.exception("Synchronisation commerciale ; conversation %s : %s", event.chat_id, error)
            engine.report_error(event.chat_id, "Erreur de synchronisation. Consultez les logs.")

    runner = None
    try:
        await onboarding.ensure_connected()
        app = make_app(engine, authenticator, origins, client.is_connected, provider)
        add_commercial_routes(app, onboarding, claims)
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
