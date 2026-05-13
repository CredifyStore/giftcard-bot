"""
Painel admin:
- Adicionar códigos ao estoque (com notificação no grupo de histórico)
- Ver estoque
- Ver pedidos
- Listar clientes com saldo
- Estornar saldo manualmente
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.constants import ParseMode

from config import CATALOG, ADMIN_IDS
from models.database import (
    add_gift_code, count_gift_codes,
    list_wallets_with_balance, get_balance,
    get_user_orders,
)
from services.history import post_stock_update

SELECT_PRODUCT, SELECT_VALUE, ENTER_CODES = range(3)
REFUND_USER, REFUND_AMOUNT, REFUND_CONFIRM = range(10, 13)


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


# ──── /admin ──────────────────────────────────────────────────

async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return
    await _send_admin_panel(update.message.reply_text)


async def adm_panel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 Acesso negado.", show_alert=True)
        return
    await query.answer()
    await _send_admin_panel(query.edit_message_text)


async def _send_admin_panel(send_fn):
    kb = [
        [InlineKeyboardButton("➕ Adicionar códigos",    callback_data="adm_add_codes")],
        [InlineKeyboardButton("📊 Estoque",              callback_data="adm_stock")],
        [InlineKeyboardButton("👛 Clientes com saldo",   callback_data="adm_balances")],
    ]
    await send_fn("🔧 *Painel Admin*", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


# ──── Adicionar códigos ───────────────────────────────────────

async def adm_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    kb = [[InlineKeyboardButton(f"{v['emoji']} {v['name']}", callback_data=f"adm_prod:{k}")]
          for k, v in CATALOG.items()]
    kb.append([InlineKeyboardButton("❌ Cancelar", callback_data="adm_panel")])
    await query.edit_message_text("Selecione o *produto*:", reply_markup=InlineKeyboardMarkup(kb),
                                  parse_mode=ParseMode.MARKDOWN)
    return SELECT_PRODUCT


async def adm_select_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["adm_product_key"] = query.data.split(":")[1]
    item = CATALOG[ctx.user_data["adm_product_key"]]

    kb = [[InlineKeyboardButton(v["label"], callback_data=f"adm_val:{v['amount']}")]
          for v in item["values"]]
    kb.append([InlineKeyboardButton("❌ Cancelar", callback_data="adm_panel")])
    await query.edit_message_text(f"*{item['name']}* — Selecione o *valor*:",
                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    return SELECT_VALUE


async def adm_select_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["adm_amount"] = int(query.data.split(":")[1])
    await query.edit_message_text(
        "Envie os códigos, *um por linha*:\n\nExemplo:\n`ABC123-DEF456\nXYZ789-GHI012`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ENTER_CODES


async def adm_receive_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    product_key = ctx.user_data.get("adm_product_key")
    amount      = ctx.user_data.get("adm_amount")
    lines = [l.strip() for l in update.message.text.splitlines() if l.strip()]

    added, dupes = 0, 0
    for code in lines:
        if add_gift_code(product_key, amount, code):
            added += 1
        else:
            dupes += 1

    item  = CATALOG[product_key]
    label = next((v["label"] for v in item["values"] if v["amount"] == amount), "?")

    # Notifica grupo de histórico
    if added > 0:
        await post_stock_update(update.get_bot(), item["name"], item["emoji"], label, added)

    await update.message.reply_text(
        f"✅ *{item['name']}* {label}\n"
        f"➕ Adicionados: {added}\n"
        f"⚠️ Duplicados ignorados: {dupes}\n\n"
        f"📢 Grupo de histórico notificado\\!",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


async def adm_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _send_admin_panel(query.edit_message_text)
    return ConversationHandler.END


# ──── Estoque ─────────────────────────────────────────────────

async def adm_stock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()

    lines = ["📊 *Estoque atual:*\n"]
    for key, item in CATALOG.items():
        lines.append(f"\n{item['emoji']} *{item['name']}*")
        for v in item["values"]:
            qty = count_gift_codes(key, v["amount"])
            bar = "🟢" if qty > 3 else ("🟡" if qty > 0 else "🔴")
            lines.append(f"  {bar} {v['label']} — {qty} un")

    kb = [[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
                                  parse_mode=ParseMode.MARKDOWN)


# ──── Clientes com saldo ──────────────────────────────────────

async def adm_balances(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()

    wallets = list_wallets_with_balance()
    if not wallets:
        lines = ["👛 Nenhum cliente com saldo positivo\\."]
    else:
        lines = [f"👛 *Clientes com saldo \\({len(wallets)}\\):*\n"]
        for w in wallets[:20]:
            tag = f"@{w['username']}" if w["username"] else w["full_name"]
            lines.append(f"• {_esc(tag)} — *R\\$ {w['balance_cents']/100:.2f}* \\(ID: `{w['user_id']}`\\)")

    kb = [[InlineKeyboardButton("↩️ Estornar", callback_data="adm_refund_start")],
          [InlineKeyboardButton("🔙 Admin",    callback_data="adm_panel")]]
    await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
                                  parse_mode=ParseMode.MARKDOWN_V2)


# ──── Estorno manual ──────────────────────────────────────────

async def adm_refund_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    ctx.user_data["refund_step"] = "user"
    await query.edit_message_text(
        "↩️ *Estorno manual*\n\nEnvie o *ID do usuário* \\(número\\) ou @username:",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return REFUND_USER


async def adm_refund_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip().lstrip("@")
    # Aceita ID numérico
    if raw.isdigit():
        ctx.user_data["refund_user_id"] = int(raw)
        bal = get_balance(int(raw))
        await update.message.reply_text(
            f"Usuário ID `{raw}`\n💰 Saldo atual: *R\\$ {bal/100:.2f}*\n\nDigite o valor a estornar \\(ex: `50` ou `25,90`\\):",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return REFUND_AMOUNT
    else:
        await update.message.reply_text("❌ Envie o ID numérico do usuário. Tente novamente:")
        return REFUND_USER


async def adm_refund_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        cents = round(float(raw) * 100)
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Tente novamente:")
        return REFUND_AMOUNT

    ctx.user_data["refund_amount"] = cents
    uid = ctx.user_data["refund_user_id"]
    bal = get_balance(uid)

    kb = [
        [InlineKeyboardButton("✅ Confirmar estorno", callback_data="adm_refund_confirm")],
        [InlineKeyboardButton("❌ Cancelar",           callback_data="adm_panel")],
    ]
    await update.message.reply_text(
        f"↩️ Confirma estorno de *R\\$ {cents/100:.2f}* para ID `{uid}`?\n"
        f"Saldo atual: R\\$ {bal/100:.2f} → R\\$ {(bal+cents)/100:.2f}",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return REFUND_CONFIRM


async def adm_refund_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    uid    = ctx.user_data["refund_user_id"]
    cents  = ctx.user_data["refund_amount"]
    new_bal = refund_wallet(uid, cents, "Estorno manual", query.from_user.id)

    # Notifica o cliente
    try:
        await ctx.bot.send_message(
            chat_id=uid,
            text=(
                f"↩️ *Estorno recebido\\!*\n\n"
                f"R\\$ {cents/100:.2f} foram devolvidos ao seu saldo\\.\n"
                f"💰 Novo saldo: *R\\$ {new_bal/100:.2f}*"
            ),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception:
        pass

    await query.edit_message_text(
        f"✅ Estorno de R\\$ {cents/100:.2f} realizado para ID `{uid}`\\.\nNovo saldo: R\\$ {new_bal/100:.2f}",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


# ──── ConversationHandlers exportados ────────────────────────

add_codes_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(adm_add_start, pattern="^adm_add_codes$")],
    states={
        SELECT_PRODUCT: [CallbackQueryHandler(adm_select_product, pattern="^adm_prod:")],
        SELECT_VALUE:   [CallbackQueryHandler(adm_select_value,   pattern="^adm_val:")],
        ENTER_CODES:    [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_receive_codes)],
    },
    fallbacks=[CallbackQueryHandler(adm_cancel, pattern="^adm_panel$")],
    per_user=True, per_chat=False,
)

