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
