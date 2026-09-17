"""Compte local privé : empreinte scrypt et sessions révocables en mémoire."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from collections import deque


def password_digest(password, salt):
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8,
                          p=3, maxmem=64 * 1024 * 1024, dklen=32).hex()


def create_account(username, password):
    username = username.strip()
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', username):
        raise ValueError('Identifiant : 3 à 64 lettres, chiffres, points, tirets ou tirets bas.')
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise ValueError('Choisissez un mot de passe de 15 à 128 caractères, par exemple une phrase unique.')
    salt = secrets.token_bytes(16)
    return {'username': username, 'algorithm': 'scrypt-n32768-r8-p3',
            'salt': salt.hex(), 'digest': password_digest(password, salt)}


class LoginFailed(Exception):
    pass


class TooManyAttempts(Exception):
    pass


class Auth:
    def __init__(self, account, ttl=8 * 3600, clock=time.monotonic):
        if (not isinstance(account, dict)
                or account.get('algorithm') != 'scrypt-n32768-r8-p3'
                or not re.fullmatch(r'[a-zA-Z0-9_.-]{3,64}', account.get('username', ''))
                or not re.fullmatch(r'[0-9a-f]{32}', account.get('salt', ''))
                or not re.fullmatch(r'[0-9a-f]{64}', account.get('digest', ''))):
            raise ValueError('Compte du panel invalide. Relancez configurer_compte.py.')
        self.account, self.ttl, self.clock = account, ttl, clock
        self.sessions = {}
        self.failures = deque()
        self.lock = asyncio.Lock()

    @classmethod
    def from_file(cls, path):
        try:
            return cls(json.loads(path.read_text(encoding='utf-8')))
        except FileNotFoundError:
            raise SystemExit('Créez votre identifiant et mot de passe avec : python configurer_compte.py')

    async def login(self, username, password):
        async with self.lock:
            now = self.clock()
            while self.failures and self.failures[0] <= now - 60:
                self.failures.popleft()
            if len(self.failures) >= 5:
                raise TooManyAttempts()
            valid_shape = (isinstance(username, str) and len(username) <= 64
                           and isinstance(password, str) and 1 <= len(password) <= 128)
            matches = False
            username_ok = False
            password_ok = False
            if valid_shape:
                digest = await asyncio.to_thread(password_digest, password, bytes.fromhex(self.account['salt']))
                password_ok = hmac.compare_digest(digest, self.account['digest'])
                username_ok = hmac.compare_digest(username.strip().encode(), self.account['username'].encode())
                matches = password_ok and username_ok
            if not matches:
                if os.getenv('COMMERCIAL_TEST_MODE', '0') == '1':
                    print(f'TEST LOGIN DIAG: shape={valid_shape} username_ok={username_ok} password_ok={password_ok}', flush=True)
                self.failures.append(self.clock())
                raise LoginFailed()
            self.failures.clear()
            return self.issue()

    def issue(self):
        now = self.clock()
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        if len(self.sessions) >= 20:
            del self.sessions[min(self.sessions, key=self.sessions.get)]
        token = secrets.token_urlsafe(48)
        self.sessions[hashlib.sha256(token.encode()).hexdigest()] = now + self.ttl
        return token

    def valid(self, token):
        if not isinstance(token, str) or len(token) > 128:
            return False
        key = hashlib.sha256(token.encode()).hexdigest()
        expiry = self.sessions.get(key, 0)
        if expiry <= self.clock():
            self.sessions.pop(key, None)
            return False
        return True

    def logout(self, token):
        self.sessions.pop(hashlib.sha256(token.encode()).hexdigest(), None)
