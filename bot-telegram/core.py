"""Etat durable et coordination."""
# deploy-trigger: teaser primary-first + mark after send
import asyncio, json, logging, random, re, sqlite3, time, unicodedata
from datetime import datetime, timezone
logger = logging.getLogger(__name__)

class HumanHandoffRequired(ValueError):
    pass

class DailyLimitReached(ValueError):
    pass

def temporary_ai_error(error):
    status = getattr(error, 'status_code', None) or getattr(error, 'status', None)
    if status is not None:
        return status in (408, 429, 500, 502, 503, 504)
    if isinstance(error, (TimeoutError, ConnectionError)):
        return True
    return any(cls.__name__ in ('APIConnectionError', 'APITimeoutError', 'ClientConnectionError') for cls in type(error).__mro__)

def plain_response(text):
    return text.replace('**', '').strip()

def clip_reply(text, max_words=14):
    """Force Telegram-short replies; keep a payment URL if present."""
    text = plain_response(text)
    if not text:
        return text
    urls = re.findall(r'https?://\S+|paypal\.me/\S+', text, flags=re.I)
    masked = text
    for i, u in enumerate(urls):
        masked = masked.replace(u, f'URL{i}')
    words = text.split()
    if len(words) <= max_words:
        return text
    m = re.search(r'.+?[.!?]', masked, flags=re.DOTALL)
    if m:
        candidate = m.group(0).strip()
        for i, u in enumerate(urls):
            candidate = candidate.replace(f'URL{i}', u)
        if 3 <= len(candidate.split()) <= max_words + 2:
            text = candidate
            words = text.split()
    if len(words) > max_words:
        dangling = {'de','du','des','le','la','les','un','une','et','ou','a','à','pour','que','qui','ce','cet','cette','mon','ma','mes','ton','ta','tes','au','aux','en','d','l','y','sur','avec'}
        def tidy(parts):
            while parts and parts[-1].lower().rstrip(',;:') in dangling:
                parts.pop()
            return ' '.join(parts).rstrip(',;:')
        if urls:
            lead = []
            for w in words:
                if w in urls or any(w.startswith(u.rstrip('.,;:!?)')) for u in urls):
                    break
                lead.append(w)
                if len(lead) >= max(3, max_words - 1):
                    break
            text = (tidy(lead) + ' ' + urls[0]).strip()
        else:
            text = tidy(words[:max_words])
    return text.strip()

DEFAULTS = {'enabled': False, 'tone': 'Reponds en francais, ton naturel.', 'catalog': '', 'faq': '', 'daily_limit': 100, 'glossary': []}
BASE_PROMPT = '''Tu parles au nom de ce compte Telegram, a la premiere personne.
Tu tiens toute la conversation toute seule, de A a Z.
Suis le TON, les PRESTATIONS et la FAQ du panel.
Style: phrases courtes, orales, naturelles, jamais robotiques.
LONGUEUR (DUR, PRIORITAIRE):
- 1 seule phrase (presque jamais 2)
- Maximum ~8–14 mots
- Une seule idée par message
- Abréviations OK (tkt, mdr, jsp)
- Interdit: listes, pavés, explications longues, plusieurs questions d’affilée
- Exception: si le client demande comment payer, tu peux ajouter le lien/moyen en une phrase courte
Interdits: renseignement, assistant, IA, bot.
Si le tarif est dans PRESTATIONS, donne-le.
Si le client demande comment payer, PayPal, Wero, virement ou Paysafecard : donne les infos listées dans PRESTATIONS ou FAQ. N invente aucun identifiant absent.
Si un detail manque, recentre sans inventer.
N avoue jamais etre une IA.
Renvoie exactement [RELAIS_HUMAIN] UNIQUEMENT si le client dit qu il a DEJA paye ou envoie une preuve de paiement.
Ne renvoie PAS [RELAIS_HUMAIN] juste parce qu il demande un moyen de paiement ou dit qu il va payer.
Sinon tu geres seule.
Si le client demande un avant-gout, tease, preview ou apercu : reponds TRES court (1 phrase).
Le systeme peut joindre une video automatiquement : ne pretend pas avoir envoye une video si tu n as produit que du texte.
N invente aucun nom de fichier ni de media.
Si un avant-gout a deja ete envoye a ce client, ne promets pas d en renvoyer un.
Si aucune video n est disponible, tease verbalement ou dis d attendre un peu, sans inventer d envoi.
Reponds seulement avec le texte a envoyer.
'''

HANDOFF_FALLBACKS = [
    'ok je verifie le paiement et je te dis',
    'recu, je check ca 2 min',
    'ok envoie la preuve si t as pas deja, je regarde',
]

EMPTY_FALLBACKS = [
    'mdr dis moi',
    'haha et toi',
    'ok dis moi ce que tu veux',
    'jsuis la dis moi',
]

def apply_glossary(text, glossary):
    for entry in glossary or []:
        expression = (entry.get('expression') or '').strip()
        if not expression:
            continue
        text = re.sub(re.escape(expression), entry.get('replacement') or '', text, flags=re.IGNORECASE)
    return text

def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.lower()) if not unicodedata.combining(c)).replace('\u2019', "'")

def needs_human_identity(text):
    return False

def needs_payment_handoff(text):
    value = normalized(text)
    return bool(re.search(r"j[' ]?ai paye|paiement (?:envoye|fait)|voila (?:le )?(?:paypal|recu|paiement)|preuve de paiement|j[' ]?ai envoye|c[' ]est paye|transaction (?:faite|envoyee)", value))

def needs_human_output(text):
    value = normalized(text)
    return '[relais_humain]' in value or bool(re.search(r"\b(?:je suis|i am|i'm) (?:un |une |a |an )?(?:ia|bot|robot|assistant)\b", value))

def wants_teaser(text):
    value = normalized(text or '')
    if not value.strip():
        return False
    patterns = [
        r"avant[- ]?gouts?",
        r"\btease\b",
        r"\bteaser\b",
        r"\bpreview\b",
        r"\bapercu\b",
        r"un[e]? apercu",
        r"petit apercu",
        r"montre[- ]?(?:moi )?(?:un peu|qqch|quelque chose|un truc|une? (?:photo|video|image))?",
        r"envoyer?[ -]?(?:moi )?(?:un |une |le |la |du |de la )?(?:tease|apercu|avant|photo|video|image|pack)?",
        r"envoie[- ]?(?:moi )?(?:un |une |le |la )?(?:tease|apercu|avant|photo|video|image)?",
        r"envoi(?:e|er)?[- ]?(?:moi )?(?:un |une |le |la )?(?:tease|apercu|avant|photo|video|image)?",
        r"voir un peu",
        r"un petit apercu",
        r"donne[- ]?(?:moi )?(?:un |une )?(?:avant|apercu|tease|photo|video)",
        r"\b(?:une?|des)?\s*photos?\b",
        r"\b(?:une?|des)?\s*videos?\b",
        r"\bpic(?:s)?\b",
    ]
    if any(re.search(p, value) for p in patterns):
        return True
    if re.fullmatch(r"(?:oui|ouais|ok|okay|yes|yep|go|vas[- ]?y|envoie|envoi|stp|s'?il te plait|siltp|svp)", value.strip()):
        return True
    return False

def client_wants_teaser(store, chat_id, latest_client=None):
    if latest_client is None:
        latest_client = None
        for m in reversed(store.messages(chat_id, 8)):
            if m['source'] == 'client':
                latest_client = m['text']
                break
    if not latest_client:
        return False
    if wants_teaser(latest_client):
        value = normalized(latest_client)
        if re.fullmatch(r"(?:oui|ouais|ok|okay|yes|yep|go|vas[- ]?y|envoie|envoi|stp|s'?il te plait|siltp|svp)", value.strip()):
            for m in reversed(store.messages(chat_id, 8)):
                if m['source'] == 'ai':
                    bot = normalized(m['text'] or '')
                    if re.search(r"apercu|avant[- ]?gout|tease|photo|video|preview|montre", bot):
                        return True
                    return False
            return False
        return True
    return False

def tease_delivered(store, chat_id):
    for m in store.messages(chat_id, 80):
        if m.get('source') != 'ai':
            continue
        value = normalized(m.get('text') or '')
        if 'video avant-gout' in value or 'video avant gout' in value or '[video avant-gout]' in value:
            return True
        if 'avant-gout' in value and 'video' in value:
            return True
    return False

def pick_teaser(active):
    if not active:
        return None
    return sorted(
        active,
        key=lambda t: (
            0 if t.get('primary') or t.get('featured') else 1,
            float(t.get('created') or 0),
            str(t.get('id') or ''),
        ),
    )[0]

class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            mode TEXT NOT NULL DEFAULT 'auto', unread INTEGER NOT NULL DEFAULT 0,
            preview TEXT NOT NULL DEFAULT '', updated REAL NOT NULL, revision INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL, telegram_id INTEGER NOT NULL, source TEXT NOT NULL,
            text TEXT NOT NULL, created REAL NOT NULL, UNIQUE(chat_id,telegram_id));
        CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id,id);
        CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated DESC);
        CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, count INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL, message_id INTEGER);
        ''')
        self.db.execute('INSERT OR IGNORE INTO settings VALUES (1,?)', (json.dumps(DEFAULTS),))
        cols = {row[1] for row in self.db.execute('PRAGMA table_info(chats)')}
        if 'tease_sent' not in cols:
            self.db.execute('ALTER TABLE chats ADD COLUMN tease_sent INTEGER NOT NULL DEFAULT 0')
        self.db.commit()
    def settings(self):
        value = json.loads(self.db.execute('SELECT value FROM settings WHERE id=1').fetchone()[0])
        for key, default in DEFAULTS.items():
            value.setdefault(key, default)
        return value
    def save_settings(self, settings):
        self.db.execute('UPDATE settings SET value=? WHERE id=1', (json.dumps(settings),))
        self.db.commit()
    def ensure(self, chat_id, name):
        self.db.execute('INSERT INTO chats(id,name,updated) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name', (chat_id, name, time.time()))
        self.db.commit()
    def chat(self, chat_id):
        row = self.db.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        return dict(row) if row else None
    def chats(self):
        return [dict(row) for row in self.db.execute('SELECT * FROM chats ORDER BY updated DESC')]
    def mode(self, chat_id, mode):
        self.db.execute('UPDATE chats SET mode=?,revision=revision+1 WHERE id=?', (mode, chat_id))
        self.db.commit()
    def read(self, chat_id):
        self.db.execute('UPDATE chats SET unread=0 WHERE id=?', (chat_id,))
        self.db.commit()
    def known(self, chat_id, message_id):
        return self.db.execute('SELECT 1 FROM messages WHERE chat_id=? AND telegram_id=?', (chat_id, message_id)).fetchone() is not None
    def add(self, chat_id, message_id, source, text, created=None):
        created = created or time.time()
        cursor = self.db.execute('INSERT OR IGNORE INTO messages(chat_id,telegram_id,source,text,created) VALUES(?,?,?,?,?)', (chat_id, message_id, source, text, created))
        inserted = bool(cursor.rowcount)
        if inserted:
            self.db.execute('UPDATE chats SET preview=?,updated=?,unread=unread+?,revision=revision+1 WHERE id=?', (text[:160], created, int(source == 'client'), chat_id))
        self.db.commit()
        return inserted
    def messages(self, chat_id, limit=200):
        rows = self.db.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?', (chat_id, limit)).fetchall()
        return [dict(row) for row in reversed(rows)]
    def usage(self):
        day = datetime.now(timezone.utc).date().isoformat()
        row = self.db.execute('SELECT count FROM usage WHERE day=?', (day,)).fetchone()
        return row[0] if row else 0
    def reserve(self):
        day = datetime.now(timezone.utc).date().isoformat()
        if self.usage() >= self.settings()['daily_limit']:
            raise DailyLimitReached('Plafond quotidien de demandes IA atteint.')
        self.db.execute('INSERT INTO usage VALUES(?,1) ON CONFLICT(day) DO UPDATE SET count=count+1', (day,))
        self.db.commit()
    def claim_request(self, request_id, chat_id, text):
        row = self.db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
        if row:
            if row['chat_id'] != chat_id or row['text'] != text:
                raise ValueError('Identifiant deja utilise pour un autre message.')
            return dict(row)
        self.db.execute("INSERT INTO requests VALUES(?,?,?,'pending',NULL)", (request_id, chat_id, text))
        self.db.commit()
        return None
    def finish_request(self, request_id, message_id):
        self.db.execute("UPDATE requests SET status='sent',message_id=? WHERE id=?", (message_id, request_id))
        self.db.commit()
    def mark_tease_sent(self, chat_id):
        self.db.execute('UPDATE chats SET tease_sent=1 WHERE id=?', (chat_id,))
        self.db.commit()
    def clear_tease_sent(self, chat_id):
        self.db.execute('UPDATE chats SET tease_sent=0 WHERE id=?', (chat_id,))
        self.db.commit()

class Engine:
    def __init__(self, store, transport, generate, mark_read=None, simulate_typing=None, delay_min=1.8, delay_max=3.8, *, delay=None, retry_delays=(5, 15), send_media=None, get_active_teasers=None):
        self.store = store
        self.transport = transport
        self.generate = generate
        self.mark_read = mark_read
        self.simulate_typing = simulate_typing
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
            teaser_note = ''
            latest_client = None
            for m in reversed(self.store.messages(chat_id, 6)):
                if m['source'] == 'client':
                    latest_client = m['text']
                    break
            if latest_client and client_wants_teaser(self.store, chat_id, latest_client):
                chat = self.store.chat(chat_id) or {}
                if chat.get('tease_sent') and not tease_delivered(self.store, chat_id):
                    self.store.clear_tease_sent(chat_id)
                    chat = self.store.chat(chat_id) or {}
                    print('Telegram : flag avant-gout reinitialise (pas de video en historique).', flush=True)
                active = []
                if self.get_active_teasers:
                    try:
                        active = self.get_active_teasers() or []
                    except Exception:
                        active = []
                if chat.get('tease_sent'):
                    teaser_note = '\nCONTEXTE AVANT-GOUT\nTu as deja envoye une video tease a ce client. Ne promets pas d en renvoyer une.\n'
                elif not active:
                    teaser_note = '\nCONTEXTE AVANT-GOUT\nLe client demande un avant-gout mais aucune video n est active. Tease verbalement tres court. N invente pas avoir envoye une video.\n'
                else:
                    teaser_note = '\nCONTEXTE AVANT-GOUT\nLe systeme peut joindre une video. Garde ta reponse tres courte. Ne pretend pas avoir envoye une video dans le texte seul.\n'
            prompt = BASE_PROMPT + teaser_note + '\nTON\n' + settings['tone'] + '\nPRESTATIONS\n' + settings['catalog'] + '\nFAQ\n' + settings['faq']
            messages = [{'role': 'user' if m['source'] == 'client' else 'assistant', 'content': (apply_glossary(m['text'], settings['glossary']) if m['source'] == 'client' else m['text'])[:8000]} for m in self.store.messages(chat_id, 12)]
            while messages and messages[0]['role'] != 'user':
                messages.pop(0)
            if not messages:
                raise ValueError('Aucun message client a traiter.')
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
            if not text:
                text = random.choice(EMPTY_FALLBACKS)
                print('IA : reponse vide, secours court utilise.', flush=True)
            if needs_human_output(text):
                if '[relais_humain]' in text.lower().replace(' ', ''):
                    self.set_mode(chat_id, 'manual')
                    raise HumanHandoffRequired('Le client dit avoir paye ou envoie une preuve. Reprenez pour encaisser.')
                text = text.replace('[RELAIS_HUMAIN]', '').replace('[relais_humain]', '').strip() or 'ok dis-moi juste ce que tu veux'
            text = clip_reply(text, max_words=14)
            if not text:
                text = random.choice(EMPTY_FALLBACKS)
                print('IA : reponse vide, secours court utilise.', flush=True)
            return text[:4000]
    async def auto_reply(self, chat_id, revision, epoch):
        delivery_uncertain = False
        try:
            wait = random.uniform(self.delay_min, self.delay_max)
            print(f'Reponse automatique : attente initiale {wait:.1f} s.', flush=True)
            await asyncio.sleep(wait)
            if not self.valid(chat_id, revision, epoch):
                return
            handoff = None
            try:
                reply = await self.automatic_draft(chat_id, revision, epoch)
            except HumanHandoffRequired as error:
                handoff = str(error)
                reply = random.choice(HANDOFF_FALLBACKS)
            if reply is None or not self.valid(chat_id, revision, epoch):
                return
            async with self.lock(chat_id):
                if not self.valid(chat_id, revision, epoch):
                    return
                if self.simulate_typing:
                    try:
                        await self.simulate_typing(chat_id, reply)
                    except Exception as error:
                        logger.exception('Saisie Telegram ; conversation %s : %s', chat_id, type(error).__name__)
                if not self.valid(chat_id, revision, epoch):
                    return
                try:
                    sent_teaser = False
                    latest_client = None
                    for m in reversed(self.store.messages(chat_id, 8)):
                        if m['source'] == 'client':
                            latest_client = m['text']
                            break
                    chat = self.store.chat(chat_id) or {}
                    if chat.get('tease_sent') and not tease_delivered(self.store, chat_id):
                        self.store.clear_tease_sent(chat_id)
                        chat = self.store.chat(chat_id) or {}
                        print('Telegram : flag avant-gout reinitialise (pas de video en historique).', flush=True)
                    active = []
                    if self.get_active_teasers:
                        try:
                            active = list(self.get_active_teasers() or [])
                        except Exception as error:
                            logger.exception('Teasers ; conversation %s : %s', chat_id, type(error).__name__)
                    send_failed = False
                    if (
                        latest_client and client_wants_teaser(self.store, chat_id, latest_client)
                        and not chat.get('tease_sent')
                        and active and self.send_media
                    ):
                        teaser = pick_teaser(active)
                        path = (teaser.get('path') or teaser.get('filepath')) if teaser else None
                        if path:
                            try:
                                message_id = await self.send_media(chat_id, path, caption=None)
                                self.store.add(chat_id, message_id, 'ai', '[Vidéo avant-goût]')
                                self.store.mark_tease_sent(chat_id)
                                sent_teaser = True
                                print('Telegram : avant-gout video envoye.', flush=True)
                            except Exception as error:
                                send_failed = True
                                logger.exception('Envoi avant-gout echoue ; conversation %s : %s', chat_id, type(error).__name__)
                                print(f'Telegram : avant-gout envoi echoue ({type(error).__name__}), replique texte seule (flag non pose).', flush=True)
                    if latest_client and client_wants_teaser(self.store, chat_id, latest_client) and not sent_teaser:
                        chosen = pick_teaser(active) if active else None
                        chosen_path = (chosen.get('path') or chosen.get('filepath')) if chosen else None
                        reason = (
                            'deja_envoye' if chat.get('tease_sent')
                            else 'aucun_actif' if not active
                            else 'envoi_echoue' if send_failed
                            else 'pas_de_chemin' if not chosen_path
                            else 'send_media_absent' if not self.send_media
                            else 'autre'
                        )
                        print(f'Telegram : avant-gout non envoye ({reason}).', flush=True)
                    message_id = await self.transport(chat_id, reply)
                    self.store.add(chat_id, message_id, 'ai', reply)
                    if self.mark_read:
                        try:
                            await self.mark_read(chat_id)
                        except Exception as error:
                            logger.exception('Lecture Telegram ; conversation %s : %s', chat_id, type(error).__name__)
                except Exception:
                    delivery_uncertain = True
                    self.report_error(chat_id, 'Envoi incertain.')
                    raise
                finally:
                    if handoff and 'encaisser' in handoff:
                        self.set_mode(chat_id, 'manual')
                        if not delivery_uncertain:
                            self.report_error(chat_id, 'Client pret a payer. Reprenez pour encaisser.')
                        logger.warning('Relais humain ; conversation %s : %s', chat_id, handoff)
                print('Telegram : reponse automatique envoyee.', flush=True)
                if not handoff:
                    self.errors.pop(chat_id, None)
        except asyncio.CancelledError:
            raise
        except HumanHandoffRequired as error:
            self.report_error(chat_id, str(error))
        except DailyLimitReached:
            self.report_error(chat_id, 'Plafond quotidien IA atteint.')
        except Exception as error:
            if self.valid(chat_id, revision, epoch) and not delivery_uncertain:
                self.report_error(chat_id, 'Reponse automatique echouee (' + type(error).__name__ + ').')
            logger.exception('Reponse automatique ; conversation %s : %s', chat_id, type(error).__name__)
    async def outgoing(self, chat_id, message_id, text, created=None):
        async with self.lock(chat_id):
            if self.store.known(chat_id, message_id):
                return
            self.set_mode(chat_id, 'manual')
            self.store.add(chat_id, message_id, 'human', text, created)
    async def reply(self, chat_id, text, request_id):
        self.set_mode(chat_id, 'manual')
        async with self.lock(chat_id):
            previous = self.store.claim_request(request_id, chat_id, text)
            if previous:
                if previous['status'] == 'sent':
                    return previous['message_id']
                raise ValueError('Envoi precedent incertain.')
            message_id = await self.transport(chat_id, text)
            self.store.add(chat_id, message_id, 'human', text)
            self.store.finish_request(request_id, message_id)
            return message_id
    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
