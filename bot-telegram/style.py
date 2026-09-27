"""Style, prompts, helpers Lexa."""
import re, unicodedata

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

Apres 2 messages sans choix de presta ni paiement:
tu papotes. tu prends nudes cam ou canal ?

CLOSER
Chaque reponse doit contenir un choix concret (presta ou prix) sauf si il vient de payer.
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
]
