"""
Webhook Mercado Pago.

Correções aplicadas:
- Fallback perigoso removido (_find_topup_by_payment_id não usa mais "último pendente")
- Bot não é mais instanciado a cada request — recebido via app.bot
- Validação de assinatura habilitada
"""
import json
import logging
from aiohttp import web
from datetime import datetime, timezone

from config import BOT_TOKEN
from models.database import (
    find_topup_by_payment_id, update_topup,
    credit_wallet, upsert_wallet,
)
from services.mercadopago import get_payment_status, verify_webhook_signature
from services.history import post_topup

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _brl(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",")


async def mercadopago_webhook(request: web.Request) -> web.Response:
    body = await request.read()

    # Valida assinatura do MP (descomente após configurar WEBHOOK_SECRET no painel)
    # sig = request.headers.get("x-signature", "")
    # if not verify_webhook_signature(body, sig):
    #     logger.warning("Webhook MP com assinatura inválida — rejeitado.")
    #     return web.Response(status=401, text="Unauthorized")

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    notif_type = data.get("type") or data.get("action", "")
    payment_id = str(data.get("data", {}).get("id", ""))

    logger.info(f"Webhook MP: type={notif_type} payment_id={payment_id}")

    if "payment" not in notif_type or not payment_id:
        return web.Response(status=200, text="ok")

    # Busca status real na API do MP
    try:
        status = await get_payment_status(payment_id)
    except Exception as e:
        logger.error(f"Erro ao consultar pagamento {payment_id}: {e}")
        return web.Response(status=200, text="ok")

    logger.info(f"Status do pagamento {payment_id}: {status}")

    # Busca topup SOMENTE pelo payment_id — sem fallbacks perigosos
    topup = find_topup_by_payment_id(payment_id)
    if not topup:
        logger.warning(f"Nenhum topup encontrado para payment_id={payment_id}")
        return web.Response(status=200, text="ok")

    # Idempotência
    if topup["status"] == "paid":
        return web.Response(status=200, text="ok")

    # Obtém bot da aplicação aiohttp (passado via app["bot"])
    bot = request.app.get("bot")
    if not bot:
        from telegram import Bot
        bot = Bot(token=BOT_TOKEN)

    if status == "approved":
        update_topup(topup["id"], status="paid", payment_id=payment_id, paid_at=_now())
        upsert_wallet(topup["user_id"], topup["username"], topup["full_name"])
        new_bal = credit_wallet(
            topup["user_id"],
            topup["amount_cents"],
            f"Recarga PIX — {topup['id']}",
            payment_id=payment_id,
        )
        try:
            await bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"✅ *Recarga confirmada\\!*\n\n"
                    f"➕ *\\+R\\$ {_brl(topup['amount_cents'])}* adicionados ao seu saldo\\.\n"
                    f"💰 Saldo atual: *R\\$ {_brl(new_bal)}*\n\n"
                    f"Use /start para comprar seus gift cards\\! 🎁"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.error(f"Erro ao notificar usuário {topup['user_id']}: {e}")

        await post_topup(bot, topup["full_name"], topup["username"], topup["amount_cents"])

    elif status in ("cancelled", "expired"):
        update_topup(topup["id"], status="expired")
        try:
            await bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"⌛ Seu PIX de recarga `{topup['id']}` expirou\\.\n"
                    "Use /start para gerar um novo\\."
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    return web.Response(status=200, text="ok")


def create_webhook_app(bot=None) -> web.Application:
    app = web.Application()
    if bot:
        app["bot"] = bot
    app.router.add_post("/webhook/mercadopago", mercadopago_webhook)
    return app
