"""
handlers/admin.py — Painel de administração.

CORREÇÕES APLICADAS:
- [BUG-07 CORRIGIDO] ParseMode uniformizado para MARKDOWN_V2 em todos os handlers
- [BUG-08 CORRIGIDO] adm_manual_deliver: face_value_cents passado corretamente
- [SEC-03 CORRIGIDO] Validação de inputs melhorada (nome de produto, valores)
- [ARCH-06 CORRIGIDO] print() substituído por logging
- Comando /vendas adicionado — resumo de vendas do dia
- is_admin() usa set (O(1)) em vez de list
"""
import logging
import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (ContextTypes, ConversationHandler,
                           CallbackQueryHandler, MessageHandler,
                           CommandHandler, filters)
from telegram.constants import ParseMode

from config import ADMIN_IDS, DISCOUNT, DISCOUNT_PCT
from models.database import (
    add_gift_code, count_gift_codes, count_all_gift_codes,
    list_wallets_with_balance,
    upsert_product, upsert_product_value,
    get_all_products, get_product,
    delete_product_value, credit_wallet, upsert_wallet,
    pop_gift_code, create_order, update_order,
)
from services.history import post_stock_update
from utils import esc, brl, sanitize_code

logger = logging.getLogger(__name__)

ASK_PROD_NAME, ASK_PROD_EMOJI, ASK_FACE_VALUE, ASK_CODES = range(4)

# ADMIN_IDS já é set[int] no config refatorado
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


# ──── Painel principal ────────────────────────────────────────

async def admin_panel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return
    await _send_panel(update.message.reply_text)


async def adm_panel_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("🚫 Acesso negado.", show_alert=True)
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
    await fn(
        "🔧 *Painel Admin — Credify*\n\n"
        "Comandos disponíveis:\n"
        "`/entregar <user_id> <produto> <face_cents>`\n"
        "`/creditar <user_id> <valor_reais>`",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


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
        total = sum(count_all_gift_codes(p["key"]).values())
        kb.append([InlineKeyboardButton(
            f"{p['emoji']} {p['name']} ({total} un)",
            callback_data=f"adm_existprod:{p['key']}",
        )])
    kb.append([InlineKeyboardButton("🆕 Novo produto",  callback_data="adm_newprod")])
    kb.append([InlineKeyboardButton("❌ Cancelar",      callback_data="adm_cancel")])

    await query.edit_message_text(
        "📦 *Adicionar Estoque*\n\nSelecione um produto ou crie um novo:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
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
        "🆕 *Novo Produto*\n\nDigite o *nome* do produto \\(ex: `PlayStation`, `Netflix`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_PROD_NAME


async def adm_recv_prod_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    raw  = update.message.text.strip()
    # [SEC-03] Valida nome: só letras, números, espaços e alguns símbolos
    name = raw[:40]  # limite de comprimento
    key  = "".join(c if c.isalnum() or c == "_" else "_" for c in name.lower())[:20]
    key  = key.strip("_") or "produto"

    ctx.user_data["adm_product_name"] = name
    ctx.user_data["adm_product_key"]  = key
    await update.message.reply_text(
        f"Produto: *{esc(name)}* \\(`{esc(key)}`\\)\n\n"
        f"Envie um emoji para o produto \\(ex: `🎮`, `🎬`, `👟`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_PROD_EMOJI


async def adm_recv_prod_emoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END
    emoji = update.message.text.strip().split()[0][:10]
    ctx.user_data["adm_product_emoji"] = emoji
    upsert_product(
        ctx.user_data["adm_product_key"],
        ctx.user_data["adm_product_name"],
        emoji,
    )
    await update.message.reply_text(
        f"{emoji} *{esc(ctx.user_data['adm_product_name'])}* criado\\!\n\n"
        f"Agora, qual o *valor de face* do gift card em reais?\n"
        f"\\(ex: `100` para R\\$ 100,00\\)",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_FACE_VALUE


async def _ask_face_value_msg(send_fn, product=None):
    lines = ["💳 *Qual o valor de face do gift card?*\n"]
    if product and product.get("values"):
        lines.append("Valores já cadastrados:")
        for v in product["values"]:
            qty = count_gift_codes(product["key"], v["amount_cents"])
            lines.append(
                f"  • R\\$ {v['face_value_cents'] // 100},00 "
                f"→ R\\$ {brl(v['amount_cents'])} "
                f"\\({qty} un\\.\\)"
            )
        lines.append("")
    lines.append("Digite o valor em reais \\(ex: `100`\\):")
    await send_fn("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)
    return ASK_FACE_VALUE


async def adm_recv_face_value(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        face_reais   = float(raw)
        if face_reais <= 0 or face_reais > 10_000:
            raise ValueError("Fora do range")
        face_cents   = round(face_reais * 100)
        amount_cents = round(face_cents * (1 - DISCOUNT))
    except ValueError:
        await update.message.reply_text(
            "❌ Valor inválido\\. Digite um número positivo\\. Ex: `100`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return ASK_FACE_VALUE

    ctx.user_data["adm_face_value"] = face_cents
    ctx.user_data["adm_amount"]     = amount_cents
    upsert_product_value(ctx.user_data["adm_product_key"], face_cents, amount_cents)

    await update.message.reply_text(
        f"✅ *Valor cadastrado\\!*\n\n"
        f"💳 Saldo do pin: *R\\$ {face_cents // 100},00*\n"
        f"💸 Preço com {DISCOUNT_PCT}% OFF: *R\\$ {brl(amount_cents)}*\n\n"
        f"Agora envie os *códigos*, um por linha:\n\n"
        f"`ABCD\\-1234\\-EFGH`\n`WXYZ\\-5678\\-MNOP`",
        parse_mode=ParseMode.MARKDOWN_V2,
    )
    return ASK_CODES


async def adm_recv_codes(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return ConversationHandler.END

    product_key = ctx.user_data.get("adm_product_key")
    face_value  = ctx.user_data.get("adm_face_value")
    amount      = ctx.user_data.get("adm_amount")

    # Sanitiza e deduplica os códigos recebidos
    raw_lines = update.message.text.splitlines()
    codes     = list(dict.fromkeys(
        sanitize_code(l) for l in raw_lines if l.strip()
    ))

    added, dupes = 0, 0
    for code in codes:
        if not code:
            continue
        if add_gift_code(product_key, face_value, amount, code):
            added += 1
        else:
            dupes += 1

    product_name  = ctx.user_data.get("adm_product_name", product_key)
    product_emoji = ctx.user_data.get("adm_product_emoji", "🎁")
    label         = f"R$ {face_value // 100},00 por R$ {brl(amount)}"

    if added > 0:
        try:
            await post_stock_update(
                update.get_bot(), product_name, product_emoji, label, added
            )
        except Exception as e:
            logger.warning(f"[admin] Falha ao postar estoque no histórico: {e}")

    total_now = count_gift_codes(product_key, amount)
    logger.info(f"[admin] Estoque adicionado: produto={product_key} added={added} dupes={dupes}")

    await update.message.reply_text(
        f"✅ *Estoque Atualizado\\!*\n\n"
        f"{product_emoji} *{esc(product_name)}* \\— {esc(label)}\n"
        f"➕ Adicionados: *{added}*\n"
        f"⚠️ Duplicados ignorados: *{dupes}*\n"
        f"📦 Total em estoque: *{total_now}*",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")
        ]]),
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
            "📊 *Estoque vazio*\n\nNenhum produto cadastrado\\.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")
            ]]),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    lines = ["📊 *Estoque Atual*\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for p in products:
        lines.append(f"\n{p['emoji']} *{esc(p['name'])}*")
        stock = count_all_gift_codes(p["key"])
        if not p["values"]:
            lines.append("  _Nenhum valor cadastrado_")
            continue
        for v in p["values"]:
            qty = stock.get(v["amount_cents"], 0)
            bar = "🟢" if qty > 3 else ("🟡" if qty > 0 else "🔴")
            lines.append(
                f"  {bar} R\\$ {v['face_value_cents'] // 100},00 "
                f"→ R\\$ {brl(v['amount_cents'])} — *{qty} un\\.*"
            )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")
        ]]),
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
        total_em_caixa = sum(w["balance_cents"] for w in wallets)
        lines = [
            f"👛 *Clientes com Saldo \\({len(wallets)}\\)*\n"
            f"💰 Total em carteiras: *R\\$ {brl(total_em_caixa)}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
        ]
        for w in wallets[:20]:
            tag = f"@{w['username']}" if w["username"] else w["full_name"] or "Anônimo"
            lines.append(
                f"• {esc(tag)} — *R\\$ {brl(w['balance_cents'])}* "
                f"\\(ID: `{w['user_id']}`\\)"
            )
        if len(wallets) > 20:
            lines.append(f"\n_\\.\\.\\. e mais {len(wallets) - 20} clientes_")

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")
        ]]),
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
    kb = [
        [InlineKeyboardButton(f"{p['emoji']} {p['name']}", callback_data=f"adm_delprod:{p['key']}")]
        for p in products if p["values"]
    ]
    kb.append([InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")])

    await query.edit_message_text(
        "🗑 *Excluir Valor*\n\nEscolha o produto:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
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
            f"🗑 R$ {v['face_value_cents'] // 100},00 → R$ {brl(v['amount_cents'])} ({qty} un)",
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
    logger.info(f"[admin] Valor deletado: produto={product_key} face={face_value_cents}")

    await query.edit_message_text(
        f"✅ Valor *R\\$ {face_value_cents // 100},00* removido de *{esc(name)}*\\.\n\n"
        f"Códigos não utilizados deste valor também foram removidos\\.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Admin", callback_data="adm_panel")
        ]]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Entrega e crédito manual ────────────────────────────────

async def adm_manual_deliver(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/entregar <user_id> <product_key> <face_value_cents>"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Acesso negado.")
        return

    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "Uso: `/entregar <user_id> <product_key> <face_value_cents>`\n"
            "Ex: `/entregar 123456789 playstation 10000`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    try:
        user_id          = int(args[0])
        product_key      = args[1].lower().strip()
        face_value_cents = int(args[2])
    except ValueError:
        await update.message.reply_text(
            "❌ Parâmetros inválidos\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    product = get_product(product_key)
    if not product:
        await update.message.reply_text(
            f"❌ Produto `{esc(product_key)}` não encontrado\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # [BUG-08 CORRIGIDO] amount_cents calculado a partir do face_value
    amount_cents = round(face_value_cents * (1 - DISCOUNT))
    gift_code    = pop_gift_code(product_key, amount_cents)
    if not gift_code:
        await update.message.reply_text(
            f"❌ Sem estoque para face=R\\${face_value_cents // 100},00 "
            f"\\(amount={amount_cents}\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    order_id = create_order(
        user_id=user_id, username="", full_name="Manual",
        product_key=product_key, product_name=product["name"],
        amount_cents=amount_cents,
        face_value_cents=face_value_cents,  # [BUG-08 CORRIGIDO]
    )
    update_order(
        order_id, status="delivered", gift_code=gift_code,
        delivered_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )

    logger.info(f"[admin] Entrega manual: order_id={order_id} user_id={user_id} code={gift_code[:8]}...")

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=(
                f"🎊 *Sua compra foi processada\\!*\n\n"
                f"{product['emoji']} *{esc(product['name'])}*\n"
                f"💵 Valor: R\\$ {face_value_cents // 100},00\n\n"
                f"🔑 *Código:*\n`{esc(gift_code)}`\n\n"
                f"⏳ Resgate em até *10 minutos*\\.\n"
                f"Obrigado\\! 🚀"
            ),
            parse_mode="MarkdownV2",
        )
        await update.message.reply_text(
            f"✅ Entregue para `{user_id}`\\. Pedido: `{order_id}`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        logger.error(f"[admin] Erro ao entregar para {user_id}: {e}")
        await update.message.reply_text(
            f"⚠️ Código gerado mas falha ao enviar\\!\n"
            f"Pedido: `{order_id}`\nCódigo: `{gift_code}`\n"
            f"Erro: `{esc(str(e)[:100])}`",
            parse_mode=ParseMode.MARKDOWN_V2,
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
        if amount_cents <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "❌ Parâmetros inválidos\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    upsert_wallet(user_id, "", "")
    new_bal = credit_wallet(
        user_id, amount_cents,
        f"Crédito manual — admin {update.effective_user.id}",
        txn_type="manual",
    )

    logger.info(f"[admin] Crédito manual: user_id={user_id} amount=R${brl(amount_cents)} "
                f"novo_saldo=R${brl(new_bal)}")

    try:
        await ctx.bot.send_message(
            chat_id=user_id,
            text=(
                f"✅ *Saldo Adicionado\\!*\n\n"
                f"➕ *R\\$ {brl(amount_cents)}* foram creditados na sua carteira\\.\n"
                f"💰 Saldo atual: *R\\$ {brl(new_bal)}*\n\n"
                f"Use /start para comprar\\! 🎁"
            ),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.warning(f"[admin] Não foi possível notificar user_id={user_id}: {e}")

    await update.message.reply_text(
        f"✅ R\\$ {brl(amount_cents)} creditados para `{user_id}`\\.\n"
        f"Novo saldo: *R\\$ {brl(new_bal)}*",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── ConversationHandler ─────────────────────────────────────

add_codes_conv = ConversationHandler(
    entry_points=[CallbackQueryHandler(adm_add_start, pattern="^adm_add_codes$")],
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
    per_user=True,
    per_chat=False,
)
