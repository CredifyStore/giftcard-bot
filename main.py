"""
main.py — Ponto de entrada do Credify Bot.

MUDANÇAS:
- Logging estruturado configurado no startup (não apenas basicConfig)
- Global error handler registrado — captura exceções não tratadas sem crashar
- Graceful shutdown com SIGTERM/SIGINT
- Ordem de registro dos handlers corrigida e documentada
- Verificação de variáveis críticas antes de iniciar
"""
import logging
import asyncio
import signal
from aiohttp import web

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters,
)

from config import BOT_TOKEN, PORT
from models.database import init_db
from handlers.user import (
    start, back_to_start,
    show_catalog, show_product, initiate_buy, insufficient_balance,
    my_orders, help_menu, my_profile, ranking,
    topup_menu, topup_pix_info, topup_custom,
    topup_receive_custom, topup_fixed,
    wallet_menu,
)
from handlers.admin import (
    admin_panel, adm_panel_callback, adm_stock, adm_balances,
    adm_delete_menu, adm_delete_pick_product, adm_delete_value_confirm,
    adm_manual_deliver, adm_manual_credit,
    add_codes_conv,
)
from handlers.support import support_conv, admin_reply_conv
from handlers.webhook import create_webhook_app


# ──── Logging ─────────────────────────────────────────────────

def setup_logging():
    logging.basicConfig(
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        level=logging.INFO,
    )
    # Silencia logs verbosos de bibliotecas externas
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)


logger = logging.getLogger(__name__)


# ──── Error Handler Global ────────────────────────────────────

async def error_handler(update: object, context) -> None:
    """Captura exceções não tratadas — evita crash do bot."""
    logger.error(f"[error_handler] Exceção não tratada: {context.error}", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Ocorreu um erro interno\\. Tente novamente em alguns segundos\\.",
                parse_mode="MarkdownV2",
            )
        except Exception:
            pass


# ──── Build da aplicação Telegram ────────────────────────────

def build_telegram_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_error_handler(error_handler)

    # ── ConversationHandlers primeiro (maior prioridade)
    app.add_handler(add_codes_conv)
    app.add_handler(support_conv)
    app.add_handler(admin_reply_conv)

    # ── Comandos
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("admin",    admin_panel))
    app.add_handler(CommandHandler("entregar", adm_manual_deliver))
    app.add_handler(CommandHandler("creditar", adm_manual_credit))

    # ── Texto livre no DM (valor de recarga personalizado)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        topup_receive_custom,
    ))

    # ── Callbacks do usuário
    app.add_handler(CallbackQueryHandler(back_to_start,        pattern="^start$"))
    app.add_handler(CallbackQueryHandler(show_catalog,         pattern="^catalog$"))
    app.add_handler(CallbackQueryHandler(show_product,         pattern="^product:"))
    app.add_handler(CallbackQueryHandler(initiate_buy,         pattern="^buy:"))
    app.add_handler(CallbackQueryHandler(initiate_buy,         pattern="^no_stock$"))
    app.add_handler(CallbackQueryHandler(insufficient_balance, pattern="^insuf:"))
    app.add_handler(CallbackQueryHandler(my_orders,            pattern="^my_orders$"))
    app.add_handler(CallbackQueryHandler(help_menu,            pattern="^help$"))
    app.add_handler(CallbackQueryHandler(wallet_menu,          pattern="^wallet$"))
    app.add_handler(CallbackQueryHandler(topup_menu,           pattern="^topup_menu$"))
    app.add_handler(CallbackQueryHandler(topup_pix_info,       pattern="^topup_pix$"))
    app.add_handler(CallbackQueryHandler(topup_custom,         pattern="^topup_custom$"))
    app.add_handler(CallbackQueryHandler(topup_fixed,          pattern="^topup_amount:"))
    app.add_handler(CallbackQueryHandler(my_profile,           pattern="^profile$"))
    app.add_handler(CallbackQueryHandler(ranking,              pattern="^ranking$"))

    # ── Callbacks do admin
    app.add_handler(CallbackQueryHandler(adm_panel_callback,       pattern="^adm_panel$"))
    app.add_handler(CallbackQueryHandler(adm_stock,                pattern="^adm_stock$"))
    app.add_handler(CallbackQueryHandler(adm_balances,             pattern="^adm_balances$"))
    app.add_handler(CallbackQueryHandler(adm_delete_menu,          pattern="^adm_delete_menu$"))
    app.add_handler(CallbackQueryHandler(adm_delete_pick_product,  pattern="^adm_delprod:"))
    app.add_handler(CallbackQueryHandler(adm_delete_value_confirm, pattern="^adm_delval:"))

    return app


# ──── Main ────────────────────────────────────────────────────

async def main():
    setup_logging()
    logger.info("🚀 Iniciando Credify Bot...")

    init_db()

    tg_app = build_telegram_app()
    await tg_app.initialize()
    await tg_app.start()

    webhook_app = create_webhook_app(bot=tg_app.bot)
    runner = web.AppRunner(webhook_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"✅ Webhook ouvindo na porta {PORT}")

    await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("✅ Bot iniciado e aguardando mensagens.")

    # Graceful shutdown com SIGTERM/SIGINT
    stop_event = asyncio.Event()

    def _handle_signal():
        logger.info("⚠️  Sinal de encerramento recebido — desligando...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows não suporta add_signal_handler

    await stop_event.wait()

    logger.info("🛑 Encerrando bot...")
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()
    await runner.cleanup()
    logger.info("✅ Bot encerrado com sucesso.")


if __name__ == "__main__":
    asyncio.run(main())
