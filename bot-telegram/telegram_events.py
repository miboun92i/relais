"""Shared DM routing for the standard and commercial runtimes."""
from telethon.types import MessageMediaWebPage


async def route_private_message(engine, event, name):
    store = engine.store
    store.ensure(event.chat_id, name)
    sticker = getattr(event.message, 'sticker', None) is not None
    text = event.raw_text or ''
    if sticker and not text.strip():
        text = '[Sticker Telegram reçu — peut être une salutation. Réponds naturellement et brièvement selon le contexte, sans supposer le contenu visuel.]'
    elif not text:
        text = '[Média — à consulter dans Telegram]'
    if event.out:
        await engine.outgoing(event.chat_id, event.id, text, event.date.timestamp())
    elif store.add(event.chat_id, event.id, 'client', text, event.date.timestamp()):
        # Link previews are text, not media requiring human inspection.
        if event.media and not sticker and not isinstance(event.media, MessageMediaWebPage):
            engine.set_mode(event.chat_id, 'manual')
        else:
            engine.incoming(event.chat_id)
