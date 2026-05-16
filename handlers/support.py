"""
handlers/support.py — Sistema de suporte ao cliente.

CORREÇÕES APLICADAS:
- [BUG-10 CORRIGIDO] Import circular removido — back_to_start não é mais
  importado dentro de função async
- [BUG-07 CORRIGIDO] ParseMode.MARKDOWN_V2 em todos os handlers
- Logs estruturados adicionados
- Limite de tamanho de mensagem de suporte (evita spam com textos gigantes)
"""
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ContextTypes, ConversationHandler,
                           MessageHandler, filters, CallbackQueryHandler)
from telegram.constants import ParseMode

from config import ADMIN_IDS
from utils import esc

logger = logging.getLogger(__name__)

WAITING_MESSAGE = 10
WAITING_REPLY   = 11

MAX_SUPPORT_MSG = 1000  # caracteres


async def support_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton("❌ Cancelar", callback_data="support_cancel")]]
    await query.edit_message_text(
        "🎧 *Suporte*\n\n"
        "Descreva sua dúvida ou problema em detalhes\\.\n"
        "Nossa equipe responderá o mais rápido possível\\!\n\n"
        f"📝 _Máximo {MAX_SUPPORT_MSG} caracteres_",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return WAITING_MESSAGE


async def support_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    [BUG-10 CORRIGIDO] Volta ao menu sem import circular.
    Reconstrói o menu principal diretamente em vez de chamar back_to_start.
    """
    from handlers.user import _home_text, _main_kb  # import no topo do módulo seria circular
    query = update.callback_query
    await query.answer()
    user = query.from_user
    await query.edit_message_text(
        _home_text(user),
        reply_markup=_main_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


async def support_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text

    # Valida tamanho da mensagem
    if len(text) > MAX_SUPPORT_MSG:
        await update.message.reply_text(
            f"❌ Mensagem muito longa \\({len(text)} chars\\)\\.\n"
            f"Limite: *{MAX_SUPPORT_MSG} caracteres*\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return WAITING_MESSAGE  # mantém estado, deixa tentar de novo

    tag = f"@{user.username}" if user.username else user.full_name or "Anônimo"

    sent_count = 0
    for admin_id in ADMIN_IDS:
        kb = [[InlineKeyboardButton(
            f"↩️ Responder {user.first_name}",
            callback_data=f"reply_user:{user.id}",
        )]]
        try:
            await update.get_bot().send_message(
                chat_id=admin_id,
                text=(
                    f"🎧 *Novo Ticket de Suporte*\n\n"
                    f"👤 *De:* {esc(tag)} \\(ID: `{user.id}`\\)\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💬 *Mensagem:*\n{esc(text)}"
                ),
                reply_markup=InlineKeyboardMarkup(kb),
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            sent_count += 1
        except Exception as e:
            logger.warning(f"[support] Erro ao notificar admin {admin_id}: {e}")

    logger.info(f"[support] Ticket recebido de user_id={user.id}, notificados {sent_count} admins")

    await update.message.reply_text(
        "✅ *Mensagem Enviada\\!*\n\n"
        "Nossa equipe foi notificada e responderá em breve\\. 🙏\n\n"
        "Use /start para voltar ao menu\\.",
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
        f"↩️ Respondendo para ID `{target_id}`\\.\n\nDigite sua resposta:",
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
            text=(
                f"🎧 *Resposta do Suporte Credify*\n\n"
                f"{esc(update.message.text)}\n\n"
                f"_Use /start para voltar ao menu\\._"
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        await update.message.reply_text(
            "✅ Resposta enviada ao cliente\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        logger.info(f"[support] Admin {update.effective_user.id} respondeu para user_id={target_id}")
    except Exception as e:
        logger.error(f"[support] Erro ao responder para {target_id}: {e}")
        await update.message.reply_text(
            f"❌ Falha ao enviar resposta\\.\nErro: `{esc(str(e)[:100])}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
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
    per_user=True,
    per_chat=False,
)

admin_reply_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_reply_start, pattern="^reply_user:")],
    states={
        WAITING_REPLY: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_reply_send)
        ],
    },
    fallbacks=[],
    per_user=True,
    per_chat=False,
)
