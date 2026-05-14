"""
Painel Admin — produtos dinâmicos, estoque, clientes com saldo.
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ContextTypes, ConversationHandler,
                           CallbackQueryHandler, MessageHandler, filters)
from telegram.constants import ParseMode

from config import ADMIN_IDS, DISCOUNT
from models.database import (
    add_gift_code, count_gift_codes,
    list_wallets_with_balance, get_balance,
    upsert_product, upsert_product_value,
    get_all_products, list_all_products_admin,
)
from services.history import post_stock_update

# Estados
(ASK_PROD_NAME, ASK_PROD_EMOJI,
 ASK_FACE_VALUE, ASK_CODES) = range(4)


def is_admin(uid): return uid in ADMIN_IDS

def _esc(t):
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(t))

def brl2(c): return f"{c/100:.2f}".replace(".", ",")


# ──── Painel ──────────────────────────────────────────────────

async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return
    await _send_panel(update.message.reply_text)


async def adm_panel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True); return
    await query.answer()
    await _send_panel(query.edit_message_text)


async def _send_panel(fn):
    kb = [
        [InlineKeyboardButton("➕ Adicionar estoque",  callback_data="adm_add_codes")],
        [InlineKeyboardButton("📊 Ver estoque",        callback_data="adm_stock")],
        [InlineKeyboardButton("👛 Clientes c/ saldo",  callback_data="adm_balances")],
    ]
    await fn("🔧 *Painel Admin*", reply_markup=InlineKeyboardMarkup(kb),
             parse_mode=ParseMode.MARKDOWN)


# ──── Fluxo: adicionar estoque ────────────────────────────────
# Passo 1: pergunta se usa produto existente ou cria novo

async def adm_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    products = get_all_products()
    kb = []
    for p in products:
        total = sum(count_gift_codes(p["key"], v["amount_cents"]) for v in p["values"]) if p["values"] else 0
        kb.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']} ({total} un)",
            callback_data=f"adm_existprod:{p['key']}"
        )])
    kb.append([InlineKeyboardButton("🆕 Criar novo produto", callback_data="adm_newprod")])
    kb.append([InlineKeyboardButton("❌ Cancelar",           callback_data="adm_cancel")])

    await query.edit_message_text(
        "📦 *Adicionar Estoque*\n\nSelecione um produto existente ou crie um novo:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASK_PROD_NAME


# Escolheu produto existente
async def adm_pick_existing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.split(":")[1]
    ctx.user_data["adm_product_key"] = product_key

    product = next((p for p in get_all_products() if p["key"] == product_key), None)
    ctx.user_data["adm_product_name"]  = product["name"]
    ctx.user_data["adm_product_emoji"] = product["emoji"]

    return await _ask_face_value(query, ctx, product)


# Escolheu criar novo
async def adm_new_prod(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["adm_product_key"] = None
    await query.edit_message_text(
        "🆕 *Novo produto*\n\nDigite o *nome* do produto \\(ex: `PlayStation`, `Netflix`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_PROD_NAME


async def adm_recv_prod_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    name = update.message.text.strip()
    ctx.user_data["adm_product_name"] = name
    # Gera key automática
    key = name.lower().replace(" ", "_")[:20]
    ctx.user_data["adm_product_key"] = key

    await update.message.reply_text(
        f"Produto: *{_esc(name)}*\n\nAgora escolha um emoji para o produto \\(ex: `🎮`, `🎬`, `🎵`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_PROD_EMOJI


async def adm_recv_prod_emoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    emoji = update.message.text.strip().split()[0]
    ctx.user_data["adm_product_emoji"] = emoji

    # Salva o produto no banco
    upsert_product(
        ctx.user_data["adm_product_key"],
        ctx.user_data["adm_product_name"],
        emoji,
    )

    await update.message.reply_text(
        f"{emoji} *{_esc(ctx.user_data['adm_product_name'])}* criado\\!\n\n"
        f"Agora vamos adicionar os códigos\\.\n\n"
        f"Digite o *valor de saldo* do gift card \\(valor de face\\)\\.\n"
        f"Ex: `100` para R\\$ 100,00:",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_FACE_VALUE


async def _ask_face_value(query_or_msg, ctx, product=None):
    """Pergunta o valor de face — usado tanto para produto novo quanto existente."""
    lines = ["💳 *Qual o valor de saldo do gift card?*\n"]
    if product and product.get("values"):
        lines.append("Valores já cadastrados:")
        for v in product["values"]:
            qty = count_gift_codes(product["key"], v["amount_cents"])
            discount_price = brl2(v["amount_cents"])
            lines.append(f"  • R\\$ {v['face_value_cents']//100},00 → R\\$ {discount_price} \\({qty} un\\)")
        lines.append("")
    lines.append("Digite o valor de face em reais \\(ex: `100` para R\\$ 100,00\\):")

    if hasattr(query_or_msg, "edit_message_text"):
        await query_or_msg.edit_message_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await query_or_msg.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2,
        )
    return ASK_FACE_VALUE


async def adm_recv_face_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        face_cents = round(float(raw) * 100)
    except ValueError:
        await update.message.reply_text("❌ Valor inválido. Ex: `100` para R$ 100,00")
        return ASK_FACE_VALUE

    discount = DISCOUNT
    amount_cents = round(face_cents * (1 - discount))
    discount_pct = int(discount * 100)

    ctx.user_data["adm_face_value"]  = face_cents
    ctx.user_data["adm_amount"]      = amount_cents

    # Salva o valor no produto
    upsert_product_value(ctx.user_data["adm_product_key"], face_cents, amount_cents)

    await update.message.reply_text(
        f"✅ Valor cadastrado\\!\n\n"
        f"💳 Saldo do pin: *R\\$ {face_cents//100},00*\n"
        f"💸 Preço com {discount_pct}% OFF: *R\\$ {brl2(amount_cents)}*\n\n"
        f"Agora envie os *códigos*, um por linha:\n\n"
        f"`ABC123-DEF456\nXYZ789-GHI012`",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_CODES


async def adm_recv_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END

    product_key = ctx.user_data.get("adm_product_key")
    face_value  = ctx.user_data.get("adm_face_value")
    amount      = ctx.user_data.get("adm_amount")
    lines       = [l.strip() for l in update.message.text.splitlines() if l.strip()]

    added, dupes = 0, 0
    for code in lines:
        if add_gift_code(product_key, face_value, amount, code):
            added += 1
        else:
            dupes += 1

    product_name  = ctx.user_data.get("adm_product_name", product_key)
    product_emoji = ctx.user_data.get("adm_product_emoji", "🎁")
    face_brl      = f"R$ {face_value//100},00"
    price_brl     = f"R$ {brl2(amount)}"
    label         = f"{face_brl} por {price_brl}"

    # Notifica grupo de histórico
    if added > 0:
        await post_stock_update(
            update.get_bot(), product_name, product_emoji, label, added
        )

    total_now = count_gift_codes(product_key, amount)

    await update.message.reply_text(
        f"✅ *Estoque atualizado\\!*\n\n"
        f"{product_emoji} *{_esc(product_name)}* \\- {_esc(label)}\n"
        f"➕ Adicionados: *{added}*\n"
        f"⚠️ Duplicados ignorados: *{dupes}*\n"
        f"📦 Total em estoque: *{total_now}*\n\n"
        f"📢 Grupo de histórico notificado\\!",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ConversationHandler.END


async def adm_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _send_panel(query.edit_message_text)
    return ConversationHandler.END


# ──── Ver estoque ─────────────────────────────────────────────

async def adm_stock(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True); return
    await query.answer()

    products = get_all_products()
    if not products:
        await query.edit_message_text(
            "📊 *Estoque vazio*\n\nNenhum produto cadastrado ainda\\.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    lines = ["📊 *Estoque atual:*\n"]
    for p in products:
        lines.append(f"\n{p['emoji']} *{_esc(p['name'])}*")
        if not p["values"]:
            lines.append("  Nenhum valor cadastrado")
        for v in p["values"]:
            qty = count_gift_codes(p["key"], v["amount_cents"])
            bar = "🟢" if qty > 3 else ("🟡" if qty > 0 else "🔴")
            lines.append(f"  {bar} R\\$ {v['face_value_cents']//100},00 → R\\$ {brl2(v['amount_cents'])} — {qty} un")

    kb = [[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Clientes com saldo ──────────────────────────────────────

async def adm_balances(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True); return
    await query.answer()

    wallets = list_wallets_with_balance()
    if not wallets:
        lines = ["👛 *Nenhum cliente com saldo positivo*\\."]
    else:
        lines = [f"👛 *Clientes com saldo \\({len(wallets)}\\):*\n"]
        for w in wallets[:20]:
            tag = f"@{w['username']}" if w["username"] else w["full_name"]
            lines.append(f"• {_esc(tag)} — *R\\$ {brl2(w['balance_cents'])}* \\(ID: `{w['user_id']}`\\)")

    kb = [[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── ConversationHandler ─────────────────────────────────────

add_codes_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(adm_add_start,    pattern="^adm_add_codes$")],
    states={
        ASK_PROD_NAME: [
            CallbackQueryHandler(adm_pick_existing, pattern="^adm_existprod:"),
            CallbackQueryHandler(adm_new_prod,      pattern="^adm_newprod$"),
            MessageHandler(filters.TEXT & ~filters.COMMAND, adm_recv_prod_name),
        ],
        ASK_PROD_EMOJI: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, adm_recv_prod_emoji),
        ],
        ASK_FACE_VALUE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, adm_recv_face_value),
        ],
        ASK_CODES: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, adm_recv_codes),
        ],
    },
    fallbacks=[CallbackQueryHandler(adm_cancel, pattern="^adm_cancel$")],
    per_user=True, per_chat=False,
)
