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

# Mots qui ne peuvent pas terminer une phrase seule (article/preposition/etc.)
_DANGLING = {
    'de', 'du', 'des', 'le', 'la', 'les', 'un', 'une', 'et', 'ou', 'a', 'à', 'pour',
    'que', 'qui', 'ce', 'cet', 'cette', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes',
    'au', 'aux', 'en', 'd', 'l', 'y', 'sur', 'avec', 'dans', 'par', 'chez', 'vers',
    'sans', 'sous', 'entre', 'mais', 'donc', 'car', 'ni', 'si', 'quand', 'comme',
    'je', 'tu', 'il', 'elle', 'on', 'nous', 'vous', 'ils', 'elles', 'me', 'te',
    'se', 'lui', 'leur', 'leurs', 'son', 'sa', 'ses', 'notre', 'votre', 'vos',
    'ne', 'pas', 'plus', 'tres', 'très', 'tout', 'toute', 'tous', 'toutes',
    'aux', 'des', 'du', 'les',
}

def _ends_incomplete(words):
    if not words:
        return True
    last = words[-1].lower().rstrip('.,;:!?…')
    if last in _DANGLING:
        return True
    # "20e" / "25€" = prix complet
    if re.fullmatch(r'\d+[e€]', last):
        return False
    # "10 min" = duree complete
    if last in {'min', 'mins', 'mn', 'minutes', 'euro', 'euros', 'e', '€'}:
        if len(words) >= 2 and re.search(r'\d', words[-2]):
            return False
        return True
    # Nombre nu: incomplet seulement si precede d'un article/dangling ("les 10")
    # sinon "canal 50" / "cam 20" est un prix OK
    if re.fullmatch(r'\d+', last):
        prev = words[-2].lower().rstrip('.,;:!?…') if len(words) >= 2 else ''
        if prev in _DANGLING:
            return True
        return False
    return False

def _has_price_and_paypal(text):
    value = normalize_compare(text)
    has_price = bool(re.search(r'\d+\s*(?:€|e(?:uros?)?)|\b\d+e\b', value))
    has_pay = 'paypal' in value or 'paypal.me' in (text or '').lower()
    return has_price and has_pay

def _restore_urls(text, urls):
    for i, u in enumerate(urls):
        text = text.replace(f'URL{i}', u)
    return text

def clip_reply(text, max_words=14, soft_max=22):
    """Coupe a une frontiere propre. Ne laisse jamais une phrase incomplete (ex: '... 20e les')."""
    text = plain_response(text)
    if not text:
        return text
    urls = re.findall(r'https?://\S+|paypal\.me/\S+', text, flags=re.I)
    masked = text
    for i, u in enumerate(urls):
        masked = masked.replace(u, f'URL{i}')
    words = masked.split()
    # Tarif + PayPal: autoriser plus long pour respecter la consigne
    limit = soft_max if _has_price_and_paypal(text) else max_words
    hard_cap = max(soft_max, limit + 6)

    if len(words) <= limit and not _ends_incomplete(words):
        return _restore_urls(' '.join(words), urls).strip()

    # 1) Preferer la 1ere phrase complete si elle est raisonnable
    m = re.search(r'.+?[.!?…]', masked, flags=re.DOTALL)
    if m:
        candidate = m.group(0).strip()
        cwords = candidate.split()
        if 3 <= len(cwords) <= hard_cap and not _ends_incomplete(cwords):
            return _restore_urls(candidate, urls).strip()

    # 2) Couper a limit, puis etendre jusqu'a frontiere propre (pas de dangling)
    if len(words) <= limit:
        parts = list(words)
    else:
        parts = list(words[:limit])
    # Etendre si incomplete et qu'il reste des mots
    i = len(parts)
    while _ends_incomplete(parts) and i < len(words) and i < hard_cap:
        parts.append(words[i])
        i += 1
    # Si toujours incomplete: reculer jusqu'a un mot "plein"
    while len(parts) > 3 and _ends_incomplete(parts):
        parts.pop()
    # Si on a trop recule et que le texte original etait court et incomplet,
    # garder le texte original entier s'il tient sous hard_cap (mieux qu'une coupe)
    if _ends_incomplete(parts) and len(words) <= hard_cap:
        parts = list(words)
    # Dernier filet: ne jamais finir sur dangling si on peut ajouter 1-3 mots
    if _ends_incomplete(parts):
        while _ends_incomplete(parts) and len(parts) < len(words) and len(parts) < hard_cap:
            parts.append(words[len(parts)])

    out = _restore_urls(' '.join(parts), urls).strip().rstrip(',;:')
    return out

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
    banned = {normalize_compare(b) for b in globals().get('BANNED_FALLBACKS', ())}
    choices = [p for p in pool if normalize_compare(p) not in recent and normalize_compare(p) not in banned]
    if not choices:
        choices = [p for p in pool if normalize_compare(p) not in banned]
    return random.choice(choices or ['et donc'])

def looks_like_price(text):
    return bool(re.search(r'\d+\s*(?:€|e(?:uros?)?)\b|\b(?:nudes?|cam|canal)\b.{0,12}\d+', normalize_compare(text)))

def looks_like_menu_question(text):
    value = normalize_compare(text)
    # Menu classique nudes/cam/canal (avec ou sans ?)
    if re.search(r'\bnudes?\b', value) and re.search(r'\bcam\b', value) and re.search(r'\bcanal\b', value):
        return True
    return bool(re.search(r'\b(nudes?|cam|canal)\b', value) and ('?' in (text or '') or 'tu prends' in value or 'tu veux' in value or 'tu book' in value))

def looks_like_tariff_dump(text):
    value = normalize_compare(text)
    prices = re.findall(r'\d+\s*(?:€|e(?:uros?)?)|\b\d+e\b', value)
    mentions = sum(1 for k in ('nudes', 'nude', 'cam', 'canal') if k in value)
    return len(prices) >= 2 and mentions >= 2

def looks_like_tease_offer(text):
    value = normalize_compare(text)
    return bool(re.search(
        r'(je )?(peux|vais|pourrais)? ?(t |te )?(envoyer|envoie|envoi).{0,24}(tease|avant.?gout|apercu|preview|video|photo)'
        r'|(t|te) (envoyer|envoie).{0,16}(petit )?tease'
        r'|un petit tease'
        r'|je t envoie.{0,20}(tease|video|photo|apercu)',
        value,
    ))

def teaser_already_sent_in(messages):
    for m in messages or []:
        if m.get('source') != 'ai':
            continue
        value = normalize_compare(m.get('text') or '')
        if 'video avant-gout' in value or 'video avant gout' in value:
            return True
        if 'avant-gout' in value and 'video' in value:
            return True
    return False

def conversation_memory(messages, *, teaser_sending_now=False):
    """Contexte anti-repetition injecte dans le prompt (pas de donnees privees logguees)."""
    notes = []
    ai_or_human = [m.get('text') or '' for m in messages if m.get('source') in ('ai', 'human')]
    clients = [m.get('text') or '' for m in messages if m.get('source') == 'client']
    tease_done = teaser_already_sent_in(messages) or teaser_sending_now
    if tease_done:
        notes.append(
            'avant-gout DEJA envoye (ou en cours): INTERDIT de dire "je peux t envoyer un tease" '
            'ou proposer un envoi. Texte court vers choix presta (nudes/cam/canal) ou flirt cash.'
        )
    if any(looks_like_price(t) for t in ai_or_human):
        notes.append('tarifs deja donnes: ne recolle PAS un resume grille. avance choix ou paiement.')
    menu_asks = [t for t in ai_or_human if looks_like_menu_question(t)]
    if len(menu_asks) >= 1:
        notes.append(
            'menu nudes/cam/canal DEJA propose: ne le repropose pas. '
            'pas de double enchainement prix puis menu puis resume tarifs.'
        )
    if any(looks_like_tariff_dump(t) for t in ai_or_human):
        notes.append('resume tarifs deja envoye: une seule grille max. ensuite closer paiement.')
    if clients and any(re.search(r'\b(nudes?|cam|canal|paypal|paye|paiement)\b', normalize_compare(c)) for c in clients[-3:]):
        notes.append('le client a deja repondu sur presta/paiement: tiens-en compte.')
    if ai_or_human:
        notes.append('ne recopie pas ta derniere reponse ni une variante quasi identique.')
    if not notes:
        return ''
    return '\nMEMOIRE CONVERSATION\n' + '\n'.join('- ' + n for n in notes) + '\n'

POST_TEASE_FALLBACKS = [
    'tu veux laquelle',
    'dis juste ce que tu book',
    'ok tu book quoi',
]

AFTER_MENU_FALLBACKS = [
    'tu book laquelle',
    'ok et pour le paiement',
    'balance ton choix on avance',
]

def is_banned_phrase(text):
    """True si texte = phrase robot bannie (exacte ou quasi)."""
    cand = normalize_compare(text)
    if not cand:
        return False
    for banned in BANNED_FALLBACKS:
        b = normalize_compare(banned)
        if not b:
            continue
        if cand == b or cand in b or b in cand:
            return True
        if is_too_similar(text, [banned], threshold=0.85):
            return True
    return False

def sanitize_reply(text, messages, *, teaser_sending_now=False):
    """Post-traitement: anti-menu empile, anti-offre tease si video partie."""
    text = smash_style(text or '')
    if not text:
        return text
    ai_texts = [m.get('text') or '' for m in (messages or []) if m.get('source') in ('ai', 'human')]
    recent = list(ai_texts)

    if is_banned_phrase(text):
        text = pick_fallback(recent + list(BANNED_FALLBACKS), EMPTY_FALLBACKS)

    tease_done = teaser_already_sent_in(messages) or teaser_sending_now
    if tease_done and looks_like_tease_offer(text):
        text = pick_fallback(recent, POST_TEASE_FALLBACKS)

    menu_already = any(looks_like_menu_question(t) for t in ai_texts)
    tariff_already = any(looks_like_tariff_dump(t) or looks_like_price(t) for t in ai_texts)

    # Menu deja propose: ne pas le reposer
    if menu_already and looks_like_menu_question(text):
        text = pick_fallback(recent + [text], AFTER_MENU_FALLBACKS)

    # Empilement resume tarifs alors que grille deja donnee
    if tariff_already and looks_like_tariff_dump(text):
        text = pick_fallback(recent + [text], AFTER_MENU_FALLBACKS)

    # Prix deja donnes + nouveau menu dans la meme reponse longue: garder la partie non-menu si possible
    if menu_already and looks_like_tariff_dump(text) and looks_like_menu_question(text):
        text = pick_fallback(recent + [text], AFTER_MENU_FALLBACKS)

    return smash_style(text)

def dedupe_reply(text, recent_ai, *, max_attempts=5):
    """Evite de renvoyer une phrase deja dite; bascule sur un fallback diversifie."""
    text = smash_style(text or '')
    banned_block = list(BANNED_FALLBACKS) + list(recent_ai or [])
    if text and is_banned_phrase(text):
        text = pick_fallback(banned_block, EMPTY_FALLBACKS)
    if text and not is_too_similar(text, recent_ai) and not is_banned_phrase(text):
        return text
    for _ in range(max_attempts):
        alt = pick_fallback(banned_block)
        if is_banned_phrase(alt):
            continue
        if not is_too_similar(alt, recent_ai + ([text] if text else [])):
            return alt
        banned_block = list(banned_block) + [alt]
    alt = pick_fallback(banned_block, EMPTY_FALLBACKS)
    return alt if not is_banned_phrase(alt) else 'et donc'

DEFAULTS = {'enabled': False, 'tone': 'cash directe closer pas de rp gratuit', 'catalog': '', 'faq': '', 'daily_limit': 100, 'glossary': []}
BASE_PROMPT = '''Tu parles au nom de ce compte Telegram, a la premiere personne.
Tu vends des prestas. Tu n es pas une copine ni une partenaire de rp.
Ces regles battent le champ TON du panel si conflit.

ECRIS COMME UN TEXTO.
- minuscules sauf PayPal
- quasi aucune virgule
- 4 a 18 mots (plus si tarif + PayPal ensemble)
- phrases COMPLETES: jamais couper au milieu (interdit: "20e les")
- INTERDIT: mdr lol ptdr haha hehe
- INTERDIT: phrases longues descriptions de corps de langue de glace de frissons
- INTERDIT: valider son ego continuer le fantasme raconter une scene
- INTERDIT: Bonjour Je comprends n hesite pas assistant IA bot

RP / FANTASME
Si il decrit un acte ou veut jouer une scene:
une phrase COMPLETE avec tarif, sans menu en plus.
exemples:
ca se fait en cam 20e les 10 min tu prends ?
ok mais pas en chat. nudes 25e
si tu book on le fait. tu veux laquelle ?

ANTI-MENU
Ne propose le menu nudes/cam/canal QU'UNE seule fois.
Si deja propose dans l historique: avance (choix precis ou paiement), ne le recolle pas.
N empile JAMAIS: prix + menu + resume tarifs dans la foulée.
Une grille tarifs max puis closer.

TARIF + PAYPAL
Si le client demande le prix / combien / comment payer et que PRESTATIONS/FAQ ont tarif + PayPal:
donne les deux ensemble dans UNE reponse complete (ne coupe pas).

ANTI-REPETITION
Lis l historique: ne repose jamais une question deja posee.
Si MEMOIRE dit tarif / avant-gout / menu deja donne: avance.
Interdit de recopier ta derniere reponse.

CLOSER
Pousse un choix concret sauf si il vient de payer ou si le choix est deja clair.
N invente aucun prix hors PRESTATIONS/FAQ.

AVANT-GOUT
Le systeme envoie les videos. N invente pas d envoi.
Si une video part maintenant ou est deja partie: INTERDIT "je peux t envoyer un tease".
Dis plutot un choix presta court.
S il reste une video et qu aucune n est partie: souffle court sans promettre a tort.
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

# Phrases robot a ne jamais renvoyer (meme si l'IA les invente)
BANNED_FALLBACKS = [
    'ok sois cash tu veux quoi',
    'sois cash tu veux quoi',
]

EMPTY_FALLBACKS = [
    'et donc',
    'tu veux laquelle',
    'dis juste ce que tu book',
    'balance ton choix',
    'ok et ensuite',
    'tu book quoi',
]
