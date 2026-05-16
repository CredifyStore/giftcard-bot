"""
handlers/webhook.py — Recebe notificações de pagamento do Mercado Pago.

CORREÇÕES APLICADAS:
- [BUG-03 CORRIGIDO] Validação de assinatura HABILITADA por padrão
- Bot sempre injetado via app["bot"] — nunca instanciado por request
- Logs estruturados em cada etapa
- Idempotência: topup já 'paid' é ignorado silenciosamente
- Status 'cancelled' e 'rejected' tratados (o original só tratava 'cancelled')
- Separação clara entre "topup não encontrado" e "já processado"
"""
import json
import logging
from aiohttp import web
from datetime import datetime, timezone

from models.database import (
    find_topup_by_payment_id, update_topup,
    credit_wallet, upsert_wallet,
)
from services.mercadopago import get_payment_status, verify_webhook_signature
from services.history import post_topup
from utils import brl

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def mercadopago_webhook(request: web.Request) -> web.Response:
    body = await request.read()

    # ── Validação de assinatura ──────────────────────────────
    sig = request.headers.get("x-signature", "")
    if not verify_webhook_signature(body, sig):
        logger.warning("[webhook] Assinatura MP inválida — request rejeitado")
        return web.Response(status=401, text="Unauthorized")

    # ── Parse do body ────────────────────────────────────────
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        logger.warning("[webhook] Body inválido — não é JSON")
        return web.Response(status=400, text="Invalid JSON")

    notif_type = data.get("type") or data.get("action", "")
    payment_id = str(data.get("data", {}).get("id", "")).strip()

    logger.info(f"[webhook] Recebido: type={notif_type!r} payment_id={payment_id!r}")

    # Ignora notificações que não são de pagamento
    if "payment" not in notif_type or not payment_id:
        return web.Response(status=200, text="ok")

    # ── Busca status real na API do MP ───────────────────────
    try:
        status = await get_payment_status(payment_id)
    except Exception as e:
        logger.error(f"[webhook] Erro ao consultar payment_id={payment_id}: {e}")
        return web.Response(status=200, text="ok")  # 200 para MP não retentar

    logger.info(f"[webhook] Status MP: payment_id={payment_id} status={status}")

    # ── Localiza topup pelo payment_id ───────────────────────
    topup = find_topup_by_payment_id(payment_id)
    if not topup:
        logger.warning(f"[webhook] Topup não encontrado para payment_id={payment_id}")
        return web.Response(status=200, text="ok")

    # ── Idempotência ─────────────────────────────────────────
    if topup["status"] == "paid":
        logger.info(f"[webhook] Topup {topup['id']} já processado — ignorando")
        return web.Response(status=200, text="ok")

    # ── Bot injetado via app ─────────────────────────────────
    bot = request.app.get("bot")
    if not bot:
        logger.error("[webhook] Bot não injetado em app['bot'] — configure create_webhook_app(bot=...)")
        return web.Response(status=500, text="Internal error")

    # ── Processa pagamento aprovado ──────────────────────────
    if status == "approved":
        update_topup(topup["id"], status="paid", payment_id=payment_id, paid_at=_now())
        upsert_wallet(topup["user_id"], topup.get("username", ""), topup.get("full_name", ""))
        new_bal = credit_wallet(
            topup["user_id"],
            topup["amount_cents"],
            f"Recarga PIX — {topup['id']}",
            payment_id=payment_id,
        )
        logger.info(f"[webhook] Saldo creditado: user_id={topup['user_id']} "
                    f"amount=R${brl(topup['amount_cents'])} novo_saldo=R${brl(new_bal)}")

        try:
            await bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"✅ *Recarga Confirmada\\!*\n\n"
                    f"➕ *\\+R\\$ {brl(topup['amount_cents'])}* adicionados ao seu saldo\\.\n"
                    f"💰 Saldo atual: *R\\$ {brl(new_bal)}*\n\n"
                    f"Use /start para comprar seus gift cards\\! 🎁"
                ),
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.error(f"[webhook] Erro ao notificar user_id={topup['user_id']}: {e}")

        try:
            await post_topup(
                bot,
                topup.get("full_name", ""),
                topup.get("username", ""),
                topup["amount_cents"],
            )
        except Exception as e:
            logger.warning(f"[webhook] Erro ao postar histórico: {e}")

    # ── Processa pagamento cancelado/expirado ────────────────
    elif status in ("cancelled", "expired", "rejected"):
        update_topup(topup["id"], status="expired")
        logger.info(f"[webhook] Topup {topup['id']} marcado como expired (status MP: {status})")
        try:
            await bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"⌛ *PIX Expirado*\n\n"
                    f"Seu PIX de recarga `{topup['id']}` foi cancelado ou expirou\\.\n\n"
                    f"Use /start para gerar um novo\\."
                ),
                parse_mode="MarkdownV2",
            )
        except Exception as e:
            logger.warning(f"[webhook] Erro ao notificar expiração: {e}")

    return web.Response(status=200, text="ok")


def create_webhook_app(bot=None) -> web.Application:
    app = web.Application()
    if bot:
        app["bot"] = bot
    else:
        logger.warning("[webhook] Bot não fornecido para create_webhook_app — notificações desabilitadas")
    app.router.add_post("/webhook/mercadopago", mercadopago_webhook)
    return app
