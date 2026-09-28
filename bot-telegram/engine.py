"""Moteur de reponse automatique Lexa."""
import asyncio, hashlib, logging, os, random
from style import (
    smash_style, clip_reply, BASE_PROMPT, EMPTY_FALLBACKS,
    temporary_ai_error, HumanHandoffRequired, DailyLimitReached,
    conversation_memory, dedupe_reply, pick_fallback,
)
from tease import (
    apply_glossary, needs_payment_handoff, needs_human_output,
    client_wants_teaser, teasers_sent_count, pick_teaser,
)
from engine_reply import AutoReplyMixin
logger = logging.getLogger(__name__)

def _safe_prompt_debug(prompt, messages):
    """Log structure only: roles + lengths + hashes, jamais le texte client."""
    if os.getenv('DEBUG_PROMPT', '').strip() not in ('1', 'true', 'yes'):
        return
    parts = []
    for m in messages:
        content = m.get('content') or ''
        digest = hashlib.sha256(content.encode('utf-8', errors='ignore')).hexdigest()[:10]
        parts.append(f"{m.get('role')}:{len(content)}:{digest}")
    sys_len = len(prompt or '')
    sys_hash = hashlib.sha256((prompt or '').encode('utf-8', errors='ignore')).hexdigest()[:10]
    print(f'Prompt debug : system={sys_len}:{sys_hash} messages=[{" ".join(parts)}]', flush=True)

class Engine(AutoReplyMixin):
    def __init__(
        self,
        store,
        transport,
        generate,
        mark_read=None,
        simulate_typing=None,
        delay_min=0.4,
        delay_max=1.2,
        *,
        delay=None,
        retry_delays=(5, 15),
        send_media=None,
        get_active_teasers=None,
        typing_pulse=None,
        cancel_typing=None,
    ):
        self.store = store
        self.transport = transport
        self.generate = generate
        self.mark_read = mark_read
        self.simulate_typing = simulate_typing
        self.typing_pulse = typing_pulse
        self.cancel_typing = cancel_typing
        self.send_media = send_media
        self.get_active_teasers = get_active_teasers
        self.delay_min = delay_min if delay is None else delay
        self.delay_max = delay_max if delay is None else delay
        self.epoch = 0
        self.locks, self.tasks = {}, set()
        self.capacity = asyncio.Semaphore(2)
        self.errors = {}
        self.retry_delays = retry_delays

    @property
    def last_error(self):
        return '\n'.join(self.errors.values()) or None

    def report_error(self, chat_id, message):
        self.errors.pop(chat_id, None)
        self.errors[chat_id] = f'Conversation {chat_id} : {message}'
        if len(self.errors) > 20:
            self.errors.pop(next(iter(self.errors)))

    async def automatic_draft(self, chat_id, revision, epoch):
        for attempt in range(len(self.retry_delays) + 1):
            if not self.valid(chat_id, revision, epoch):
                return None
            try:
                return await self.draft(chat_id, manual_on_handoff=False)
            except Exception as error:
                if not temporary_ai_error(error) or attempt == len(self.retry_delays):
                    raise
                if not self.valid(chat_id, revision, epoch):
                    return None
                delay = self.retry_delays[attempt]
                self.report_error(chat_id, f'Erreur IA temporaire ; nouvelle tentative dans {delay} s.')
                logger.warning('IA ; conversation %s : tentative %s echouee (%s)', chat_id, attempt + 1, type(error).__name__)
                await asyncio.sleep(delay)

    def lock(self, chat_id):
        return self.locks.setdefault(chat_id, asyncio.Lock())

    def settings(self, value):
        self.epoch += 1
        self.store.save_settings(value)

    def set_mode(self, chat_id, mode):
        self.store.mode(chat_id, mode)

    def valid(self, chat_id, revision, epoch):
        chat = self.store.chat(chat_id)
        return bool(chat and chat['mode'] == 'auto' and chat['revision'] == revision and self.epoch == epoch and self.store.settings()['enabled'])

    def incoming(self, chat_id):
        chat = self.store.chat(chat_id)
        if not chat or not self.valid(chat_id, chat['revision'], self.epoch):
            return
        task = asyncio.create_task(self.auto_reply(chat_id, chat['revision'], self.epoch))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def catch_up_pending(self):
        if not self.store.settings().get('enabled'):
            print('Rattrapage : IA désactivée, rien à faire.', flush=True)
            return 0
        n = 0
        for chat in self.store.chats():
            if chat.get('mode') != 'auto':
                continue
            msgs = self.store.messages(chat['id'], 1)
            if not msgs or msgs[-1].get('source') != 'client':
                continue
            self.incoming(chat['id'])
            n += 1
        print(f'Reponse automatique : rattrapage de {n} conversation(s) en attente.', flush=True)
        return n

    async def draft(self, chat_id, *, manual_on_handoff=True):
        latest = self.store.messages(chat_id, 1)
        if latest and latest[-1]['source'] == 'client' and needs_payment_handoff(latest[-1]['text']):
            if manual_on_handoff:
                self.set_mode(chat_id, 'manual')
            raise HumanHandoffRequired('Le client est pret a payer ou dit avoir paye. Reprenez pour encaisser.')
        async with self.capacity:
            self.store.reserve()
            settings = self.store.settings()
            history = self.store.messages(chat_id, 12)
            teaser_note = ''
            latest_client = None
            for m in reversed(history):
                if m['source'] == 'client':
                    latest_client = m['text']
                    break
            if latest_client and client_wants_teaser(self.store, chat_id, latest_client):
                chat = self.store.chat(chat_id) or {}
                sent = max(int(chat.get('tease_sent') or 0), teasers_sent_count(self.store, chat_id))
                active = []
                if self.get_active_teasers:
                    try:
                        active = list(self.get_active_teasers() or [])
                    except Exception:
                        active = []
                if not active:
                    teaser_note = '\nCONTEXTE AVANT-GOUT\npas de video. tease verbal court.\n'
                elif sent >= len(active):
                    teaser_note = '\nCONTEXTE AVANT-GOUT\ntoutes les videos du panel sont parties. refuse un tease de plus. oriente choix et paiement.\n'
                elif sent > 0:
                    teaser_note = '\nCONTEXTE AVANT-GOUT\nune autre video du panel peut partir. texte tres court.\n'
                else:
                    teaser_note = '\nCONTEXTE AVANT-GOUT\nune video peut partir. texte tres court.\n'
            memory_note = conversation_memory(history)
            prompt = (
                BASE_PROMPT
                + teaser_note
                + memory_note
                + '\nTON\n' + settings['tone']
                + '\nPRESTATIONS\n' + settings['catalog']
                + '\nFAQ\n' + settings['faq']
            )
            messages = [
                {
                    'role': 'user' if m['source'] == 'client' else 'assistant',
                    'content': (
                        apply_glossary(m['text'], settings['glossary'])
                        if m['source'] == 'client'
                        else m['text']
                    )[:8000],
                }
                for m in history
            ]
            while messages and messages[0]['role'] != 'user':
                messages.pop(0)
            if not messages:
                raise ValueError('Aucun message client a traiter.')
            _safe_prompt_debug(prompt, messages)
            try:
                text = (await asyncio.wait_for(self.generate(prompt, messages), timeout=60)).strip()
            except (DailyLimitReached, asyncio.CancelledError):
                raise
            except Exception as error:
                if temporary_ai_error(error):
                    raise
                if manual_on_handoff:
                    self.set_mode(chat_id, 'manual')
                raise HumanHandoffRequired('IA refusee.') from error
            recent_ai = [m['text'] for m in history if m.get('source') == 'ai']
            if not text:
                text = pick_fallback(recent_ai, EMPTY_FALLBACKS)
            if needs_human_output(text):
                if '[relais_humain]' in text.lower().replace(' ', ''):
                    self.set_mode(chat_id, 'manual')
                    raise HumanHandoffRequired('Le client dit avoir paye ou envoie une preuve. Reprenez pour encaisser.')
                text = text.replace('[RELAIS_HUMAIN]', '').replace('[relais_humain]', '').strip() or pick_fallback(recent_ai)
            text = smash_style(clip_reply(text, max_words=12))
            text = dedupe_reply(text, recent_ai)
            return smash_style(text)[:4000]
