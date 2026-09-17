"""Onboarding Telegram pilotable depuis le panel commercial.

Le contrôleur encapsule le flux Telethon : téléphone -> code -> mot de passe 2FA.
Il ne stocke jamais le code ni le mot de passe reçu du client.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from telethon.errors import (
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    PasswordHashInvalidError,
)


class TelegramOnboardingError(ValueError):
    pass


@dataclass
class TelegramStatus:
    connected: bool
    authorized: bool
    user_id: int | None = None
    username: str | None = None
    display_name: str | None = None
    pending_phone: str | None = None
    needs_password: bool = False

    def as_dict(self):
        return {
            "connected": self.connected,
            "authorized": self.authorized,
            "user_id": self.user_id,
            "username": self.username,
            "display_name": self.display_name,
            "pending_phone": self.pending_phone,
            "needs_password": self.needs_password,
        }


class TelegramOnboarding:
    def __init__(self, client, account_validator=None):
        self.client = client
        self.account_validator = account_validator
        self.lock = asyncio.Lock()
        self.pending_phone = None
        self.phone_code_hash = None
        self.needs_password = False
        self.expires_at = 0

    async def ensure_connected(self):
        if not self.client.is_connected():
            await self.client.connect()

    async def status(self) -> TelegramStatus:
        await self.ensure_connected()
        if self.expires_at and time.monotonic() >= self.expires_at:
            self._clear_pending()
        authorized = await self.client.is_user_authorized()
        if not authorized:
            return TelegramStatus(
                connected=self.client.is_connected(),
                authorized=False,
                pending_phone=self.pending_phone,
                needs_password=self.needs_password,
            )
        me = await self.client.get_me()
        if self.account_validator:
            try:
                self.account_validator(me.id)
            except ValueError as exc:
                await self._logout()
                self._clear_pending()
                raise TelegramOnboardingError(str(exc)) from exc
        name = " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])) or None
        return TelegramStatus(
            connected=self.client.is_connected(),
            authorized=True,
            user_id=getattr(me, "id", None),
            username=getattr(me, "username", None),
            display_name=name,
        )

    @staticmethod
    def normalize_phone(phone: str) -> str:
        if not isinstance(phone, str):
            raise TelegramOnboardingError("Numéro de téléphone invalide.")
        phone = re.sub(r"[\s().-]", "", phone.strip())
        if not re.fullmatch(r"\+?[0-9]{8,15}", phone):
            raise TelegramOnboardingError("Numéro invalide. Utilisez le format international, par exemple +33612345678.")
        if not phone.startswith("+"):
            phone = "+" + phone
        return phone

    async def send_code(self, phone: str):
        phone = self.normalize_phone(phone)
        async with self.lock:
            await self.ensure_connected()
            if await self.client.is_user_authorized():
                raise TelegramOnboardingError("Un compte Telegram est déjà connecté.")
            self._clear_pending()
            try:
                sent = await self.client.send_code_request(phone)
            except PhoneNumberInvalidError as exc:
                raise TelegramOnboardingError("Telegram refuse ce numéro de téléphone.") from exc
            except FloodWaitError as exc:
                raise TelegramOnboardingError(f"Telegram demande d'attendre {exc.seconds} secondes avant de réessayer.") from exc
            self.pending_phone = phone
            self.phone_code_hash = sent.phone_code_hash
            self.needs_password = False
            self.expires_at = time.monotonic() + 600
            return {"ok": True, "phone": phone, "next": "code"}

    async def submit_code(self, code: str):
        if not isinstance(code, str):
            raise TelegramOnboardingError("Code Telegram invalide.")
        code = re.sub(r"\s", "", code)
        if not re.fullmatch(r"[0-9]{4,8}", code):
            raise TelegramOnboardingError("Code Telegram invalide.")
        async with self.lock:
            self._check_expiry()
            if not self.pending_phone or not self.phone_code_hash:
                raise TelegramOnboardingError("Demandez d'abord un nouveau code Telegram.")
            await self.ensure_connected()
            try:
                await self.client.sign_in(
                    phone=self.pending_phone,
                    code=code,
                    phone_code_hash=self.phone_code_hash,
                )
            except SessionPasswordNeededError:
                self.needs_password = True
                return {"ok": True, "next": "password"}
            except PhoneCodeInvalidError as exc:
                raise TelegramOnboardingError("Le code Telegram est incorrect.") from exc
            except PhoneCodeExpiredError as exc:
                self._clear_pending()
                raise TelegramOnboardingError("Le code Telegram a expiré. Demandez-en un nouveau.") from exc
            except FloodWaitError as exc:
                raise TelegramOnboardingError(f"Telegram demande d'attendre {exc.seconds} secondes avant de réessayer.") from exc
            self._clear_pending()
            return {"ok": True, "next": "done", "status": (await self.status()).as_dict()}

    async def submit_password(self, password: str):
        if not isinstance(password, str) or not 1 <= len(password) <= 256:
            raise TelegramOnboardingError("Mot de passe 2FA invalide.")
        async with self.lock:
            self._check_expiry()
            if not self.needs_password:
                raise TelegramOnboardingError("Aucun mot de passe 2FA n'est attendu.")
            await self.ensure_connected()
            try:
                await self.client.sign_in(password=password)
            except PasswordHashInvalidError as exc:
                raise TelegramOnboardingError("Mot de passe 2FA Telegram incorrect.") from exc
            except FloodWaitError as exc:
                raise TelegramOnboardingError(f"Telegram demande d'attendre {exc.seconds} secondes avant de réessayer.") from exc
            self._clear_pending()
            return {"ok": True, "next": "done", "status": (await self.status()).as_dict()}

    async def disconnect_account(self):
        """Déconnecte la session locale du compte sans supprimer le compte Telegram."""
        async with self.lock:
            await self.ensure_connected()
            if await self.client.is_user_authorized():
                await self._logout()
            self._clear_pending()
            return {"ok": True}

    async def _logout(self):
        filename = getattr(getattr(self.client, 'session', None), 'filename', None)
        await self.client.log_out()
        if filename:
            from telethon.sessions import SQLiteSession
            self.client.session = SQLiteSession(filename)

    def _clear_pending(self):
        self.pending_phone = None
        self.phone_code_hash = None
        self.needs_password = False
        self.expires_at = 0

    def _check_expiry(self):
        if self.expires_at and time.monotonic() >= self.expires_at:
            self._clear_pending()
            raise TelegramOnboardingError("Connexion expirée. Demandez un nouveau code Telegram.")
