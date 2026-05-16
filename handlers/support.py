"""
Sistema de suporte.

Correções aplicadas:
- support_cancel não duplica mais o texto do menu — usa back_to_start diretamente
- Imports tardios removidos
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ContextTypes, ConversationHandler,
                           MessageHandler, filters, CallbackQueryHandler)
from telegram.constants import ParseMode
from config import ADMIN_IDS
from utils import esc

WAITING_MESSAGE = 10
WAITING_REPLY   = 11


async def support_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("❌ Cancelar", callback_data="support_cancel")]]
    await query.edit_message_text(
        "🎧 *Suporte*\n\nDescreva sua dúvida ou problema\\.\nNossa equipe responderá em breve\\:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WAITING_MESSAGE


async def support_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Volta ao menu principal reaproveitando back_to_start."""
    query = update.callback_query
    await query.answer()
    # Redireciona para o handler padrão do menu
    from handlers.user import back_to_start
    return await back_to_start(update, ctx)


async def support_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    tag  = f"@{user.username}" if user.username else user.full_name

    for admin_id in ADMIN_IDS:
        kb = [[InlineKeyboardButton(
            f"↩️ Responder {user.first_name}",
            callback_data=f"reply_user:{user.id}",
        )]]
        try:
            await update.get_bot().send_message(
                chat_id=admin_id,
                text=(
                    f"🎧 *Ticket de suporte*\n\n"
                    f"👤 *De:* {esc(tag)} \\(ID: `{user.id}`\\)\n\n"
                    f"💬 *Mensagem:*\n{esc(text)}"
                ),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            print(f"[support] Erro admin {admin_id}: {e}")

    await update.message.reply_text(
        "✅ *Mensagem enviada\\!*\n\nNossa equipe responderá em breve\\. 🙏",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


async def admin_reply_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id not in ADMIN_IDS:
        await query.answer("🚫 Acesso negado.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    target_id = int(query.data.split(":")[1])
    ctx.user_data["reply_target"] = target_id
    await query.message.reply_text(
        f"↩️ Respondendo para ID `{target_id}`\\.\nDigite sua resposta:",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WAITING_REPLY


async def admin_reply_send(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    target_id = ctx.user_data.get("reply_target")
    if not target_id:
        return ConversationHandler.END
    try:
        await update.get_bot().send_message(
            chat_id=target_id,
            text=f"🎧 *Resposta do suporte:*\n\n{esc(update.message.text)}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        await update.message.reply_text("✅ Resposta enviada\\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Erro: `{e}`", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


support_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(support_start, pattern="^support$")],
    states={
        WAITING_MESSAGE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive),
            CallbackQueryHandler(support_cancel, pattern="^support_cancel$"),
        ],
    },
    fallbacks=[CallbackQueryHandler(support_cancel, pattern="^support_cancel$")],
    per_user=True, per_chat=False,
)

admin_reply_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_reply_start, pattern="^reply_user:")],
    states={
        WAITING_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_send)],
    },
    fallbacks=[],
    per_user=True, per_chat=False,
)
