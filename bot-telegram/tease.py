"""Detection avant-gout et glossaire."""
import re, unicodedata

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
        r"avant[- ]?gouts?", r"\btease\b", r"\bteaser\b", r"\bpreview\b", r"\bapercu\b",
        r"montre[- ]?(?:moi )?(?:un peu|qqch|quelque chose|un truc|une? (?:photo|video|image))?",
        r"envoie[- ]?(?:moi )?(?:un |une |le |la )?(?:tease|apercu|avant|photo|video|image)?",
        r"\b(?:une?|des)?\s*photos?\b", r"\b(?:une?|des)?\s*videos?\b", r"\bpic(?:s)?\b",
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
    return teasers_sent_count(store, chat_id) > 0

def teasers_sent_count(store, chat_id):
    n = 0
    for m in store.messages(chat_id, 80):
        if m.get('source') != 'ai':
            continue
        value = normalized(m.get('text') or '')
        if 'video avant-gout' in value or 'video avant gout' in value or '[video avant-gout]' in value:
            n += 1
        elif 'avant-gout' in value and 'video' in value:
            n += 1
    return n

def ordered_teasers(active):
    return sorted(active or [], key=lambda t: (0 if t.get('primary') or t.get('featured') else 1, float(t.get('created') or 0), str(t.get('id') or '')))

def pick_teaser(active, index=0):
    ordered = ordered_teasers(active)
    if not ordered or index < 0 or index >= len(ordered):
        return None
    return ordered[index]
