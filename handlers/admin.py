"""
Painel Admin.

Correções aplicadas:
- _esc() e brl2() removidas — importadas de utils.py
- list_all_products_admin removida (não era usada)
- Fluxo de exclusão de valor integrado
"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ContextTypes, ConversationHandler,
                           CallbackQueryHandler, MessageHandler,
                           CommandHandler, filters)
from telegram.constants import ParseMode
import datetime

from config import ADMIN_IDS, DISCOUNT
from models.database import (
    add_gift_code, count_gift_codes, count_all_gift_codes,
    list_wallets_with_balance,
    upsert_product, upsert_product_value,
    get_all_products, get_product,
    delete_product_value, credit_wallet, upsert_wallet,
    pop_gift_code, create_order, update_order,
)
from services.history import post_stock_update
from utils import esc, brl

ASK_PROD_NAME, ASK_PROD_EMOJI, ASK_FACE_VALUE, ASK_CODES = range(4)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return
    await _send_panel(update.message.reply_text)


async def adm_panel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()
    await _send_panel(query.edit_message_text)


async def _send_panel(fn):
    kb = [
        [InlineKeyboardButton("➕ Adicionar estoque",  callback_data="adm_add_codes")],
        [InlineKeyboardButton("📊 Ver estoque",        callback_data="adm_stock")],
        [InlineKeyboardButton("🗑 Excluir valor",      callback_data="adm_delete_menu")],
        [InlineKeyboardButton("👛 Clientes c/ saldo",  callback_data="adm_balances")],
    ]
    await fn("🔧 *Painel Admin*", reply_markup=InlineKeyboardMarkup(kb),
             parse_mode=ParseMode.MARKDOWN)


# ──── Adicionar estoque ───────────────────────────────────────

async def adm_add_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    products = get_all_products()
    kb = []
    for p in products:
        stock = count_all_gift_codes(p["key"])
        total = sum(stock.values())
        kb.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']} ({total} un)",
            callback_data=f"adm_existprod:{p['key']}",
        )])
    kb.append([InlineKeyboardButton("🆕 Criar novo produto", callback_data="adm_newprod")])
    kb.append([InlineKeyboardButton("❌ Cancelar",           callback_data="adm_cancel")])

    await query.edit_message_text(
        "📦 *Adicionar Estoque*\n\nSelecione um produto existente ou crie um novo:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ASK_PROD_NAME


async def adm_pick_existing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.split(":")[1]
    product     = get_product(product_key)
    if not product:
        await query.answer("Produto não encontrado.", show_alert=True)
        return ConversationHandler.END

    ctx.user_data["adm_product_key"]   = product_key
    ctx.user_data["adm_product_name"]  = product["name"]
    ctx.user_data["adm_product_emoji"] = product["emoji"]

    return await _ask_face_value_msg(query.edit_message_text, product)


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
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    name = update.message.text.strip()
    key  = name.lower().replace(" ", "_")[:20]
    ctx.user_data["adm_product_name"] = name
    ctx.user_data["adm_product_key"]  = key
    await update.message.reply_text(
        f"Produto: *{esc(name)}*\n\nEnvie um emoji para o produto \\(ex: `🎮`, `🎬`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_PROD_EMOJI


async def adm_recv_prod_emoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    emoji = update.message.text.strip().split()[0]
    ctx.user_data["adm_product_emoji"] = emoji
    upsert_product(ctx.user_data["adm_product_key"],
                   ctx.user_data["adm_product_name"], emoji)
    await update.message.reply_text(
        f"{emoji} *{esc(ctx.user_data['adm_product_name'])}* criado\\!\n\n"
        f"Digite o *valor de saldo* do gift card \\(ex: `100` para R\\$ 100,00\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_FACE_VALUE


async def _ask_face_value_msg(send_fn, product=None):
    lines = ["💳 *Qual o valor de saldo do gift card?*\n"]
    if product and product.get("values"):
        lines.append("Valores já cadastrados:")
        for v in product["values"]:
            qty = count_gift_codes(product["key"], v["amount_cents"])
            lines.append(f"  • R\\$ {v['face_value_cents']//100},00 → R\\$ {brl(v['amount_cents'])} \\({qty} un\\)")
        lines.append("")
    lines.append("Digite o valor de face em reais \\(ex: `100`\\):")
    await send_fn("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    return ASK_FACE_VALUE


async def adm_recv_face_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        face_cents   = round(float(raw) * 100)
        amount_cents = round(face_cents * (1 - DISCOUNT))
    except ValueError:
        await update.message.reply_text("❌ Valor inválido\\. Ex: `100`", parse_mode=ParseMode.MARKDOWN_V2)
        return ASK_FACE_VALUE

    ctx.user_data["adm_face_value"] = face_cents
    ctx.user_data["adm_amount"]     = amount_cents

    upsert_product_value(ctx.user_data["adm_product_key"], face_cents, amount_cents)

    await update.message.reply_text(
        f"✅ Valor cadastrado\\!\n\n"
        f"💳 Saldo do pin: *R\\$ {face_cents//100},00*\n"
        f"💸 Preço com {int(DISCOUNT*100)}% OFF: *R\\$ {brl(amount_cents)}*\n\n"
        f"Agora envie os *códigos*, um por linha:\n\n`ABC123\\nXYZ789`",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_CODES


async def adm_recv_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

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
    label         = f"R$ {face_value//100},00 por R$ {brl(amount)}"

    if added > 0:
        await post_stock_update(update.get_bot(), product_name, product_emoji, label, added)

    total_now = count_gift_codes(product_key, amount)
    await update.message.reply_text(
        f"✅ *Estoque atualizado\\!*\n\n"
        f"{product_emoji} *{esc(product_name)}* \\- {esc(label)}\n"
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
        await query.answer("🚫", show_alert=True)
        return
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
        lines.append(f"\n{p['emoji']} *{esc(p['name'])}*")
        stock = count_all_gift_codes(p["key"])
        if not p["values"]:
            lines.append("  Nenhum valor cadastrado")
        for v in p["values"]:
            qty = stock.get(v["amount_cents"], 0)
            bar = "🟢" if qty > 3 else ("🟡" if qty > 0 else "🔴")
            lines.append(f"  {bar} R\\$ {v['face_value_cents']//100},00 → R\\$ {brl(v['amount_cents'])} — {qty} un")

    kb = [[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Clientes com saldo ──────────────────────────────────────

async def adm_balances(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()

    wallets = list_wallets_with_balance()
    if not wallets:
        lines = ["👛 *Nenhum cliente com saldo positivo*\\."]
    else:
        lines = [f"👛 *Clientes com saldo \\({len(wallets)}\\):*\n"]
        for w in wallets[:20]:
            tag = f"@{w['username']}" if w["username"] else w["full_name"]
            lines.append(f"• {esc(tag)} — *R\\$ {brl(w['balance_cents'])}* \\(ID: `{w['user_id']}`\\)")

    kb = [[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Excluir valor ───────────────────────────────────────────

async def adm_delete_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()

    products = get_all_products()
    kb = []
    for p in products:
        if p["values"]:
            kb.append([InlineKeyboardButton(
                f"{p['emoji']} {p['name']}",
                callback_data=f"adm_delprod:{p['key']}",
            )])
    kb.append([InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")])

    await query.edit_message_text(
        "🗑 *Excluir valor*\n\nEscolha o produto:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN,
    )


async def adm_delete_pick_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()

    product_key = query.data.split(":")[1]
    product     = get_product(product_key)
    if not product or not product["values"]:
        await query.answer("Produto sem valores.", show_alert=True)
        return

    kb = []
    for v in product["values"]:
        qty = count_gift_codes(product_key, v["amount_cents"])
        kb.append([InlineKeyboardButton(
            f"🗑 R$ {v['face_value_cents']//100},00 → R$ {brl(v['amount_cents'])} ({qty} un)",
            callback_data=f"adm_delval:{product_key}:{v['face_value_cents']}",
        )])
    kb.append([InlineKeyboardButton("🔙 Voltar", callback_data="adm_delete_menu")])

    await query.edit_message_text(
        f"🗑 *{esc(product['name'])}*\n\nEscolha o valor para excluir:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def adm_delete_value_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫", show_alert=True)
        return
    await query.answer()

    parts            = query.data.split(":")
    product_key      = parts[1]
    face_value_cents = int(parts[2])
    product          = get_product(product_key)
    name             = product["name"] if product else product_key

    delete_product_value(product_key, face_value_cents)

    await query.edit_message_text(
        f"✅ Valor *R\\$ {face_value_cents//100},00* removido de *{esc(name)}*\\.\n\n"
        f"Códigos não utilizados deste valor também foram removidos\\.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")]]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Entrega e crédito manual ────────────────────────────────

async def adm_manual_deliver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/entregar <user_id> <product_key> <amount_cents>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return

    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: `/entregar <user_id> <product_key> <amount_cents>`\n"
            "Ex: `/entregar 123456789 playstation 8500`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    try:
        user_id      = int(args[0])
        product_key  = args[1]
        amount_cents = int(args[2])
    except ValueError:
        await update.message.reply_text("❌ Parâmetros inválidos\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    product = get_product(product_key)
    if not product:
        await update.message.reply_text(f"❌ Produto `{product_key}` não encontrado\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    gift_code = pop_gift_code(product_key, amount_cents)
    if not gift_code:
        await update.message.reply_text("❌ Sem estoque para este produto/valor\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    order_id = create_order(
        user_id=user_id, username="", full_name="Manual",
        product_key=product_key, product_name=product["name"],
        amount_cents=amount_cents, face_value_cents=amount_cents,
    )
    update_order(order_id, status="delivered", gift_code=gift_code,
                 delivered_at=datetime.datetime.now(datetime.timezone.utc).isoformat())

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=(
                f"🎊 *Sua compra foi processada\\!*\n\n"
                f"🎮 Gift Card: *{esc(product['name'])}*\n"
                f"🔑 Código: `{esc(gift_code)}`\n\n"
                f"⏳ Prazo para resgatar: *10 minutos*\n\n"
                f"Obrigado pela paciência\\! 🚀"
            ),
            parse_mode="MarkdownV2",
        )
        await update.message.reply_text(
            f"✅ Código entregue para `{user_id}`\\. Pedido: `{order_id}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await update.message.reply_text(
            f"❌ Erro ao enviar: `{e}`\nCódigo: `{gift_code}`",
            parse_mode=ParseMode.MARKDOWN,
        )


async def adm_manual_credit(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/creditar <user_id> <valor_reais>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return

    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text(
            "Uso: `/creditar <user_id> <valor>`\nEx: `/creditar 123456789 85`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    try:
        user_id      = int(args[0])
        amount_cents = round(float(args[1].replace(",", ".")) * 100)
    except ValueError:
        await update.message.reply_text("❌ Parâmetros inválidos\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return

    upsert_wallet(user_id, "", "")
    new_bal = credit_wallet(
        user_id, amount_cents,
        f"Crédito manual por admin {update.effective_user.id}"
    )

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ *Saldo adicionado\\!*\n\n"
                f"➕ *R\\$ {brl(amount_cents)}* foram creditados na sua carteira\\.\n"
                f"💰 Saldo atual: *R\\$ {brl(new_bal)}*\n\n"
                f"Use /start para comprar\\!"
            ),
            parse_mode="MarkdownV2",
        )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ R\\$ {brl(amount_cents)} creditados para `{user_id}`\\. Novo saldo: R\\$ {brl(new_bal)}",
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
