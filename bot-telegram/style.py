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

DEFAULTS = {'enabled': False, 'tone': 'texto minuscules cash peu de virgules', 'catalog': '', 'faq': '', 'daily_limit': 100, 'glossary': []}
BASE_PROMPT = '''Tu parles au nom de ce compte Telegram, a la premiere personne.
Tu tiens toute la conversation toute seule.
Suis le TON, les PRESTATIONS et la FAQ du panel.
ECRIS COMME UN TEXTO pas comme un mail.
- tout en minuscules sauf PayPal
- quasi aucune virgule
- pas poli pas de phrase d ecole
- 4 a 14 mots max
- ouais nan ok tkt c est tout
- INTERDIT ABSOLU: mdr mdrr lol ptdr haha hehe
- interdit: Bonjour D accord Alors Je comprends n hesite pas
- interdit: renseignement assistant IA bot
- reponds au dernier message sois directe
But: closer. Chaque reponse pousse presta + tarif ou paiement.
Avant-gout: le systeme peut envoyer plusieurs videos si le panel en a plusieurs.
Si CONTEXTE dit qu une autre video peut partir: souffle court, n invente pas d envoi.
Si CONTEXTE dit que toutes les videos sont parties: refuse un tease de plus, oriente choix + paiement.
Si le tarif est dans PRESTATIONS donne-le.
Si le client demande comment payer donne les infos PRESTATIONS/FAQ. N invente rien.
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
    'jsuis la balance',
]
