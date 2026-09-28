"""Style, prompts, helpers Lexa."""
import re, random, unicodedata

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

def strip_mdr(text):
    text = re.sub(r'(?i)\b(mdr+|mdrr+|lol+|ptdr+|haha+|hehe+)\b', '', text or '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def smash_style(text):
    text = strip_mdr(plain_response(text or ''))
    if not text:
        return text
    urls = re.findall(r'https?://\S+|paypal\.me/\S+', text, flags=re.I)
    masked = text
    for i, u in enumerate(urls):
        masked = masked.replace(u, f'__URL{i}__')
    masked = masked.replace(',', ' ')
    masked = re.sub(r'\s+', ' ', masked).strip().rstrip('.!?')
    if masked and not masked.startswith('__URL'):
        masked = masked[0].lower() + masked[1:]
    for i, u in enumerate(urls):
        masked = masked.replace(f'__URL{i}__', u)
    return masked.strip()

def clip_reply(text, max_words=12):
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

def normalize_compare(text):
    value = ''.join(c for c in unicodedata.normalize('NFKD', (text or '').lower()) if not unicodedata.combining(c))
    value = value.replace('\u2019', "'")
    value = re.sub(r'[^a-z0-9\s?€e]', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()

def token_set(text):
    return {t for t in normalize_compare(text).split() if len(t) > 1}

def is_too_similar(candidate, recent_texts, *, threshold=0.72):
    cand = normalize_compare(candidate)
    if not cand:
        return False
    cand_tokens = token_set(candidate)
    for prev in recent_texts or []:
        prev_n = normalize_compare(prev)
        if not prev_n:
            continue
        if cand == prev_n:
            return True
        if cand in prev_n or prev_n in cand:
            if min(len(cand), len(prev_n)) >= 12:
                return True
        prev_tokens = token_set(prev)
        if not cand_tokens or not prev_tokens:
            continue
        overlap = len(cand_tokens & prev_tokens) / max(1, len(cand_tokens | prev_tokens))
        if overlap >= threshold:
            return True
    return False

def pick_fallback(recent_texts=None, pool=None):
    pool = list(pool or EMPTY_FALLBACKS)
    recent = [normalize_compare(t) for t in (recent_texts or [])]
    choices = [p for p in pool if normalize_compare(p) not in recent]
    return random.choice(choices or pool)

def looks_like_price(text):
    return bool(re.search(r'\d+\s*(?:€|e(?:uros?)?)\b|\b(?:nudes?|cam|canal)\b.{0,12}\d+', normalize_compare(text)))

def looks_like_menu_question(text):
    value = normalize_compare(text)
    return bool(re.search(r'\b(nudes?|cam|canal)\b', value) and ('?' in (text or '') or 'tu prends' in value or 'tu veux' in value))

def conversation_memory(messages):
    """Contexte anti-repetition injecte dans le prompt (pas de donnees privees logguees)."""
    notes = []
    ai_or_human = [m.get('text') or '' for m in messages if m.get('source') in ('ai', 'human')]
    clients = [m.get('text') or '' for m in messages if m.get('source') == 'client']
    joined_out = ' '.join(ai_or_human)
    if any('video avant-gout' in normalize_compare(t) or 'video avant gout' in normalize_compare(t) for t in ai_or_human):
        notes.append('avant-gout deja envoye: ne repropose pas la meme video ni la meme accroche.')
    if any(looks_like_price(t) for t in ai_or_human):
        notes.append('tarifs deja donnes: ne recolle pas la grille en boucle. avance vers choix ou paiement.')
    menu_asks = [t for t in ai_or_human if looks_like_menu_question(t)]
    if len(menu_asks) >= 1:
        notes.append('tu as deja demande nudes/cam/canal. ne repose pas la meme question. avance autrement.')
    if clients and any(re.search(r'\b(nudes?|cam|canal|paypal|paye|paiement)\b', normalize_compare(c)) for c in clients[-3:]):
        notes.append('le client a deja repondu sur presta/paiement: tiens-en compte, ne reclame pas la meme info.')
    if ai_or_human:
        last = smash_style(ai_or_human[-1])[:80]
        if last:
            notes.append('ne recopie pas ta derniere reponse ni une variante quasi identique.')
    if not notes:
        return ''
    return '\nMEMOIRE CONVERSATION\n' + '\n'.join('- ' + n for n in notes) + '\n'

def dedupe_reply(text, recent_ai, *, max_attempts=5):
    """Evite de renvoyer une phrase deja dite; bascule sur un fallback diversifie."""
    text = smash_style(text or '')
    if text and not is_too_similar(text, recent_ai):
        return text
    for _ in range(max_attempts):
        alt = pick_fallback(recent_ai)
        if not is_too_similar(alt, recent_ai + ([text] if text else [])):
            return alt
        recent_ai = list(recent_ai) + [alt]
    return pick_fallback(recent_ai)

DEFAULTS = {'enabled': False, 'tone': 'cash directe closer pas de rp gratuit', 'catalog': '', 'faq': '', 'daily_limit': 100, 'glossary': []}
BASE_PROMPT = '''Tu parles au nom de ce compte Telegram, a la premiere personne.
Tu vends des prestas. Tu n es pas une copine ni une partenaire de rp.
Ces regles battent le champ TON du panel si conflit.

ECRIS COMME UN TEXTO.
- minuscules sauf PayPal
- quasi aucune virgule
- 4 a 14 mots
- INTERDIT: mdr lol ptdr haha hehe
- INTERDIT: phrases longues descriptions de corps de langue de glace de frissons
- INTERDIT: valider son ego continuer le fantasme raconter une scene
- INTERDIT: Bonjour Je comprends n hesite pas assistant IA bot

RP / FANTASME
Si il decrit un acte ou veut jouer une scene:
une phrase max puis ramene au tarif.
exemples:
ca se fait en cam. 20e les 10 min tu prends ?
ok mais pas en chat. nudes 25 cam 20 canal 50.
si tu book on le fait. tu veux laquelle ?

ANTI-REPETITION
Lis l historique: ne repose jamais une question deja posee.
Si MEMOIRE CONVERSATION dit qu un tarif / avant-gout / menu a deja ete donne: avance (paiement ou prochaine etape).
Interdit de recopier ta derniere reponse ou une variante quasi identique.
Varie le wording a chaque message.

CLOSER
Pousse un choix concret (presta ou prix) sauf si il vient de payer ou si le choix est deja clair.
Donne les tarifs PRESTATIONS des qu il demande quoi / combien / comment.
N invente aucun prix hors PRESTATIONS/FAQ.

AVANT-GOUT
Le systeme envoie les videos. N invente pas d envoi.
S il reste une video: souffle court.
Si plus de video: refuse et oriente paiement.

N avoue jamais etre une IA.
Renvoie [RELAIS_HUMAIN] UNIQUEMENT si le client dit qu il a DEJA paye ou envoie une preuve.
Reponds seulement avec le texte a envoyer.
'''

HANDOFF_FALLBACKS = [
    'ok je check le paiement',
    'recu je regarde 2 min',
    'envoie la preuve si t as pas deja',
]

EMPTY_FALLBACKS = [
    'nan dis moi ce que tu veux',
    'ok sois cash tu veux quoi',
    'nudes cam ou canal tu prends quoi',
    'balance ton choix on avance',
    'tu book laquelle',
    'ok et pour le paiement',
]
