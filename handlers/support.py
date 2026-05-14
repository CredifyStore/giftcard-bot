"""
Sistema de suporte:
- Usuário abre ticket via botão
- Mensagem vai para os admins com botão de responder
- Admin responde via bot e o cliente recebe
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from telegram.constants import ParseMode

from config import ADMIN_IDS

WAITING_MESSAGE = 10
WAITING_REPLY   = 11


def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


# ──── Usuário abre suporte ────────────────────────────────────

async def support_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["support_msg_id"] = query.message.message_id
    kb = [[InlineKeyboardButton("❌ Cancelar", callback_data="start")]]
    await query.edit_message_text(
        "🎧 *Suporte*\n\n"
        "Descreva sua dúvida ou problema em uma mensagem\\.\n"
        "Nossa equipe responderá em breve\\:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WAITING_MESSAGE


async def support_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    # Encaminha para todos os admins
    tag = f"@{user.username}" if user.username else user.full_name
    for admin_id in ADMIN_IDS:
        kb = [[InlineKeyboardButton(
            f"↩️ Responder {user.first_name}",
            callback_data=f"reply_user:{user.id}",
        )]]
        try:
            await ctx.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🎧 *Ticket de suporte*\n\n"
                    f"👤 *De:* {_esc(tag)} \\(ID: `{user.id}`\\)\n\n"
                    f"💬 *Mensagem:*\n{_esc(text)}"
                ),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            print(f"[support] Erro ao notificar admin {admin_id}: {e}")

    await update.message.reply_text(
        "✅ *Mensagem enviada\\!*\n\nNossa equipe responderá em breve\\. 🙏",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


# ──── Admin responde ──────────────────────────────────────────

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

    reply_text = update.message.text
    try:
        await ctx.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎧 *Resposta do suporte:*\n\n"
                f"{_esc(reply_text)}"
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        await update.message.reply_text("✅ Resposta enviada ao cliente\\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Erro ao enviar: `{e}`", parse_mode=ParseMode.MARKDOWN)

    return ConversationHandler.END


# ──── ConversationHandlers exportados ────────────────────────

support_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(support_start, pattern="^support$")],
    states={
        WAITING_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)],
    },
    fallbacks=[CallbackQueryHandler(lambda u, c: ConversationHandler.END, pattern="^start$")],
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
