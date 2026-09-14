"""Extensions API réservées à l'édition commerciale."""
from __future__ import annotations

from aiohttp import web

from telegram_onboarding import TelegramOnboardingError


def add_commercial_routes(app, onboarding, license_claims):
    async def body(request):
        value = await request.json()
        if not isinstance(value, dict):
            raise web.HTTPBadRequest(reason="Objet JSON attendu.")
        return value

    async def license_status(request):
        return web.json_response({
            "license_id": license_claims.license_id,
            "customer_id": license_claims.customer_id,
            "plan": license_claims.plan,
            "max_accounts": license_claims.max_accounts,
            "expires_at": license_claims.expires_at,
        })

    async def telegram_status(request):
        return web.json_response((await onboarding.status()).as_dict())

    async def telegram_send_code(request):
        data = await body(request)
        try:
            result = await onboarding.send_code(data.get("phone"))
        except TelegramOnboardingError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def telegram_submit_code(request):
        data = await body(request)
        try:
            result = await onboarding.submit_code(data.get("code"))
        except TelegramOnboardingError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def telegram_submit_password(request):
        data = await body(request)
        try:
            result = await onboarding.submit_password(data.get("password"))
        except TelegramOnboardingError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        return web.json_response(result)

    async def telegram_logout(request):
        return web.json_response(await onboarding.disconnect_account())

    app.add_routes([
        web.get("/api/commercial/license", license_status),
        web.get("/api/telegram/status", telegram_status),
        web.post("/api/telegram/send-code", telegram_send_code),
        web.post("/api/telegram/code", telegram_submit_code),
        web.post("/api/telegram/password", telegram_submit_password),
        web.post("/api/telegram/logout", telegram_logout),
    ])
    return app
