import logging
import asyncio
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
    wallet_menu, topup_menu, topup_pix_info, topup_custom,
    topup_receive_custom, topup_fixed,
)
from handlers.admin import (
    admin_panel, adm_panel_callback, adm_stock, adm_balances,
    adm_delete_menu, adm_delete_pick_product, adm_delete_value_confirm,
    adm_manual_deliver, adm_manual_credit,
    add_codes_conv,
)
from handlers.support import support_conv, admin_reply_conv
from handlers.webhook import create_webhook_app

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def build_telegram_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    # ConversationHandlers primeiro — maior prioridade
    app.add_handler(add_codes_conv)
    app.add_handler(support_conv)
    app.add_handler(admin_reply_conv)

    # Comandos
    app.add_handler(CommandHandler("start",    start))
    app.add_handler(CommandHandler("admin",    admin_panel))
    app.add_handler(CommandHandler("entregar", adm_manual_deliver))
    app.add_handler(CommandHandler("creditar", adm_manual_credit))

    # Texto livre no DM (valor de recarga)
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND,
        topup_receive_custom,
    ))

    # Callbacks usuário
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

    # Callbacks admin
    app.add_handler(CallbackQueryHandler(adm_panel_callback,       pattern="^adm_panel$"))
    app.add_handler(CallbackQueryHandler(adm_stock,                pattern="^adm_stock$"))
    app.add_handler(CallbackQueryHandler(adm_balances,             pattern="^adm_balances$"))
    app.add_handler(CallbackQueryHandler(adm_delete_menu,          pattern="^adm_delete_menu$"))
    app.add_handler(CallbackQueryHandler(adm_delete_pick_product,  pattern="^adm_delprod:"))
    app.add_handler(CallbackQueryHandler(adm_delete_value_confirm, pattern="^adm_delval:"))

    return app


async def main():
    init_db()

    tg_app = build_telegram_app()
    await tg_app.initialize()
    await tg_app.start()

    # Passa o bot para o webhook para evitar instanciar Bot() a cada request
    webhook_app = create_webhook_app(bot=tg_app.bot)

    runner = web.AppRunner(webhook_app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", PORT).start()
    logger.info(f"Webhook na porta {PORT}.")

    await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    logger.info("Bot iniciado.")

    try:
        await asyncio.Event().wait()
    finally:
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
