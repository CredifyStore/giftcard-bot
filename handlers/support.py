from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ContextTypes, ConversationHandler,
                           MessageHandler, filters, CallbackQueryHandler)
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
    kb = [[InlineKeyboardButton("❌ Cancelar", callback_data="support_cancel")]]
    await query.edit_message_text(
        "🎧 *Suporte*\n\n"
        "Descreva sua dúvida ou problema em uma mensagem\\.\n"
        "Nossa equipe responderá em breve\\:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WAITING_MESSAGE


async def support_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Cancela o suporte e volta ao menu principal."""
    query = update.callback_query
    await query.answer()
    user    = query.from_user
    from models.database import get_user_profile, upsert_wallet
    upsert_wallet(user.id, user.username or "", user.full_name)
    profile = get_user_profile(user.id)
    from handlers.user import _main_kb, brl2
    await query.edit_message_text(
        f"👋 Olá, *{_esc(user.first_name)}*\\! Bem\\-vindo à *Loja de Gift Cards*\\!\n\n"
        f"🪪 Seu ID: `{user.id}`\n"
        f"💰 Saldo Atual: R\\$ {brl2(profile['balance_cents'])}\n"
        f"🛒 Compras Realizadas: {profile['total_purchases']}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *INFORMAÇÕES IMPORTANTES:*\n\n"
        f"⚠️ Compre somente se souber utilizar o gift card\n"
        f"⏱ Você tem 10 minutos para resgatar o código\n"
        f"🔒 Garantimos que o saldo entre em sua conta\n"
        f"⚡ Não reservamos gifts \\— *pagou, recebeu*\n"
        f"📦 Novos gifts são adicionados conforme chegam\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *Ao continuar, você concorda com os termos*\n",
        reply_markup=_main_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


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
            print(f"[support] Erro admin {admin_id}: {e}")

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
    try:
        await ctx.bot.send_message(
            chat_id=target_id,
            text=f"🎧 *Resposta do suporte:*\n\n{_esc(update.message.text)}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        await update.message.reply_text("✅ Resposta enviada ao cliente\\.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        await update.message.reply_text(f"❌ Erro: `{e}`", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


# ──── ConversationHandlers ────────────────────────────────────

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
