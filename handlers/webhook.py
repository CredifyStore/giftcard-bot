"""
Webhook Mercado Pago — processa notificações de pagamento PIX.
Só lida com recargas de saldo (topups). Compras são via débito de carteira.
"""
import json
import logging
from aiohttp import web
from datetime import datetime
from telegram import Bot

from config import BOT_TOKEN
from models.database import get_topup, update_topup, credit_wallet, upsert_wallet
from services.mercadopago import get_payment_status, verify_webhook_signature
from services.history import post_topup

logger = logging.getLogger(__name__)


def _fmt(cents: int) -> str:
    return f"{cents/100:.2f}".replace(".", ",")


async def mercadopago_webhook(request: web.Request) -> web.Response:
    body = await request.read()

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return web.Response(status=400, text="Invalid JSON")

    notif_type = data.get("type") or data.get("action", "")
    payment_id = str(data.get("data", {}).get("id", ""))

    logger.info(f"Webhook MP: type={notif_type} payment_id={payment_id}")

    if "payment" not in notif_type or not payment_id:
        return web.Response(status=200, text="ok")

    try:
        status = await get_payment_status(payment_id)
    except Exception as e:
        logger.error(f"Erro ao consultar pagamento {payment_id}: {e}")
        return web.Response(status=200, text="ok")

    logger.info(f"Status do pagamento {payment_id}: {status}")

    topup = _find_topup_by_payment_id(payment_id)
    if not topup:
        logger.warning(f"Topup com payment_id={payment_id} nao encontrado.")
        return web.Response(status=200, text="ok")

    if topup["status"] == "paid":
        return web.Response(status=200, text="ok")

    bot = Bot(token=BOT_TOKEN)

    # Pagamento aprovado
    if status == "approved":
        now = datetime.utcnow().isoformat()
        update_topup(topup["id"], status="paid", payment_id=payment_id, paid_at=now)
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
                    f"Recarga confirmada\\!\n\n"
                    f"R\\$ {_fmt(topup['amount_cents'])} adicionados ao seu saldo\\.\n"
                    f"Saldo atual: R\\$ {_fmt(new_bal)}\n\n"
                    f"Use /start para comprar seus giftcards\\!"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.error(f"Erro ao notificar user {topup['user_id']}: {e}")

        await post_topup(bot, topup["full_name"], topup["username"], topup["amount_cents"])

    # PIX expirado ou cancelado
    elif status in ("cancelled", "expired"):
        update_topup(topup["id"], status="expired")
        try:
            await bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"Seu PIX de recarga {topup['id']} expirou\\.\n"
                    "Use /start para gerar um novo\\."
                ),
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass

    return web.Response(status=200, text="ok")


def _find_topup_by_payment_id(payment_id: str):
    from models.database import get_conn
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM topups WHERE payment_id=?",
        (payment_id,),
    ).fetchone()
    if not row:
        # Tenta achar topup pendente mais recente (MP pode não ter enviado payment_id ainda)
        row = conn.execute(
            "SELECT * FROM topups WHERE status='pending' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_webhook_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/webhook/mercadopago", mercadopago_webhook)
    return app
