from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import CATALOG, DISCOUNT, MIN_TOPUP_CENTS
from models.database import (
    get_balance, upsert_wallet, debit_wallet, credit_wallet,
    create_order, update_order, get_user_orders,
    count_gift_codes, pop_gift_code,
    create_topup, update_topup,
    get_user_profile, get_top10_spenders,
    get_wallet_txns,
)
from services.mercadopago import create_pix_charge
from services.history import post_purchase


def _esc(text: str) -> str:
    """Escapa caracteres especiais do MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def brl(cents: int) -> str:
    """Formata centavos em reais já escapado para MarkdownV2. Ex: 8500 → '85,00'"""
    return str(cents / 100).replace(".", ",") if "." in f"{cents/100:.2f}" else f"{cents/100:.2f}".replace(".", ",")


def brl2(cents: int) -> str:
    """Versão com 2 casas decimais sempre, escapada."""
    return f"{cents/100:.2f}".replace(".", ",")


def _main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍️ Catálogo",     callback_data="catalog"),
         InlineKeyboardButton("💳 Recarregar",    callback_data="topup_menu")],
        [InlineKeyboardButton("👛 Carteira",      callback_data="wallet"),
         InlineKeyboardButton("📦 Meus pedidos",  callback_data="my_orders")],
        [InlineKeyboardButton("👤 Meu perfil",    callback_data="profile"),
         InlineKeyboardButton("🏆 Ranking",       callback_data="ranking")],
        [InlineKeyboardButton("🎧 Suporte",       callback_data="support")],
    ])


# ──── /start ─────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_wallet(user.id, user.username or "", user.full_name)
    balance = get_balance(user.id)
    await update.message.reply_text(
        f"👋 Olá, *{_esc(user.first_name)}*\\!\n\n"
        f"💰 Saldo: *R\\$ {brl2(balance)}*\n\n"
        "O que deseja fazer?",
        reply_markup=_main_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def back_to_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    upsert_wallet(user.id, user.username or "", user.full_name)
    balance = get_balance(user.id)
    await query.edit_message_text(
        f"👋 Olá, *{_esc(user.first_name)}*\\!\n\n"
        f"💰 Saldo: *R\\$ {brl2(balance)}*\n\n"
        "O que deseja fazer?",
        reply_markup=_main_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Carteira ────────────────────────────────────────────────

async def wallet_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = get_balance(query.from_user.id)
    txns = get_wallet_txns(query.from_user.id, limit=5)

    lines = [f"👛 *Sua Carteira*\n\n💰 Saldo disponível: *R\\$ {brl2(balance)}*"]
    if txns:
        lines.append("\n📋 *Últimas movimentações:*")
        icons = {"topup": "➕", "purchase": "🛒", "refund": "↩️"}
        for t in txns:
            sign = "\\+" if t["type"] in ("topup", "refund") else "\\-"
            ico  = icons.get(t["type"], "•")
            lines.append(f"{ico} {sign}R\\$ {brl2(t['amount_cents'])} — {_esc(t['description'] or '')}")

    kb = [
        [InlineKeyboardButton("💳 Recarregar", callback_data="topup_menu")],
        [InlineKeyboardButton("🔙 Menu",        callback_data="start")],
    ]
    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Recarga ─────────────────────────────────────────────────

TOPUP_AMOUNTS = [1500, 3000, 5000, 10000, 20000]


async def topup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = [[InlineKeyboardButton(f"R$ {a//100}", callback_data=f"topup_amount:{a}")]
          for a in TOPUP_AMOUNTS]
    kb.append([InlineKeyboardButton("✏️ Outro valor", callback_data="topup_custom")])
    kb.append([InlineKeyboardButton("🔙 Menu",        callback_data="start")])
    await query.edit_message_text(
        "💳 *Recarregar saldo*\n\nEscolha o valor da recarga:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def topup_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["waiting_topup_value"] = True
    kb = [[InlineKeyboardButton("❌ Cancelar", callback_data="topup_menu")]]
    await query.edit_message_text(
        "✏️ Digite o valor que deseja recarregar \\(mínimo R\\$ 1,00\\):\n\nExemplo: `25` ou `37,50`",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def topup_receive_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("waiting_topup_value"):
        return
    ctx.user_data.pop("waiting_topup_value", None)
    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        amount_cents = round(float(raw) * 100)
    except ValueError:
        await update.message.reply_text("❌ Valor inválido\\. Use /start e tente novamente\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    if amount_cents < MIN_TOPUP_CENTS:
        await update.message.reply_text(
            f"❌ Mínimo de recarga: R\\$ {brl2(MIN_TOPUP_CENTS)}\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    await _generate_topup_pix(update, ctx, amount_cents)


async def topup_fixed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    amount_cents = int(query.data.split(":")[1])
    await _generate_topup_pix(update, ctx, amount_cents, via_query=query)


async def _generate_topup_pix(update, ctx, amount_cents: int, via_query=None):
    user = via_query.from_user if via_query else update.effective_user
    upsert_wallet(user.id, user.username or "", user.full_name)
    topup_id = create_topup(user.id, user.username or "", user.full_name, amount_cents)

    try:
        charge = await create_pix_charge(
            external_id=topup_id,
            amount_cents=amount_cents,
            description=f"Recarga de saldo — {topup_id}",
            customer={"name": user.full_name},
        )
    except Exception as e:
        msg = f"⚠️ Erro ao gerar PIX: `{_esc(str(e))}`"
        if via_query:
            await via_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    update_topup(topup_id, payment_id=charge["payment_id"], pix_copia_cola=charge["copia_cola"])

    text = (
        f"💳 *Recarga de R\\$ {brl2(amount_cents)}*\n\n"
        f"🆔 ID: `{topup_id}`\n\n"
        f"*PIX Copia e Cola:*\n`{_esc(charge['copia_cola'])}`\n\n"
        f"⏳ Expira em *30 minutos*\\. Após confirmação o saldo é creditado automaticamente\\!"
    )
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]

    # Envia mensagem com texto
    if via_query:
        await via_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2)
        chat_id = via_query.message.chat_id
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2)
        chat_id = update.effective_chat.id

    # Envia QR code como imagem se disponível
    qr_b64 = charge.get("qr_code_image", "")
    if qr_b64:
        import base64
        from io import BytesIO
        try:
            img_bytes = base64.b64decode(qr_b64)
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=BytesIO(img_bytes),
                caption="📷 QR Code PIX — escaneie para pagar",
            )
        except Exception as e:
            print(f"[topup] Erro ao enviar QR code: {e}")




# ──── Catálogo ────────────────────────────────────────────────

async def show_catalog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = get_balance(query.from_user.id)
    discount_pct = int(DISCOUNT * 100)
    kb = [[InlineKeyboardButton(f"{v['emoji']} {v['name']}", callback_data=f"product:{k}")]
          for k, v in CATALOG.items()]
    kb.append([InlineKeyboardButton("🔙 Menu", callback_data="start")])
    await query.edit_message_text(
        f"🛍️ *Catálogo de Giftcards*\n"
        f"💸 *{discount_pct}% de desconto* em todos os pins\\!\n"
        f"💰 Seu saldo: *R\\$ {brl2(balance)}*\n\n"
        "Escolha o produto:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def show_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.split(":")[1]
    item = CATALOG.get(product_key)
    if not item:
        await query.answer("Produto não encontrado.", show_alert=True)
        return

    balance = get_balance(query.from_user.id)
    kb = []
    for v in item["values"]:
        stock = count_gift_codes(product_key, v["amount"])
        if stock == 0:
            kb.append([InlineKeyboardButton(f"{v['label']}  ❌ Sem estoque", callback_data="no_stock")])
        elif balance < v["amount"]:
            kb.append([InlineKeyboardButton(f"{v['label']}  💸 Saldo insuficiente", callback_data="no_balance")])
        else:
            kb.append([InlineKeyboardButton(f"{v['label']}  ✅", callback_data=f"buy:{product_key}:{v['amount']}")])

    kb.append([InlineKeyboardButton("💳 Recarregar", callback_data="topup_menu"),
               InlineKeyboardButton("🔙 Catálogo",   callback_data="catalog")])
    await query.edit_message_text(
        f"{item['emoji']} *{_esc(item['name'])}*\n"
        f"💰 Seu saldo: *R\\$ {brl2(balance)}*\n\nEscolha o valor:",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Compra ──────────────────────────────────────────────────

async def initiate_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "no_stock":
        await query.answer("❌ Sem estoque para este valor.", show_alert=True)
        return
    if query.data == "no_balance":
        await query.answer("💸 Saldo insuficiente. Recarregue sua carteira.", show_alert=True)
        return

    await query.answer()
    parts = query.data.split(":")
    product_key, amount_cents = parts[1], int(parts[2])
    item = CATALOG[product_key]
    value_info   = next((v for v in item["values"] if v["amount"] == amount_cents), {})
    face_value   = value_info.get("face_value", amount_cents)
    label        = value_info.get("label", "?")
    discount_pct = int(DISCOUNT * 100)
    user = query.from_user

    ok, new_balance = debit_wallet(user.id, amount_cents, f"Compra {item['name']} {label}")
    if not ok:
        await query.answer("💸 Saldo insuficiente.", show_alert=True)
        return

    gift_code = pop_gift_code(product_key, amount_cents)
    order_id  = create_order(
        user_id=user.id, username=user.username or "",
        full_name=user.full_name, product_key=product_key,
        product_name=item["name"], amount_cents=amount_cents,
        face_value_cents=face_value,
    )

    if gift_code:
        import datetime
        update_order(order_id, status="delivered", gift_code=gift_code,
                     delivered_at=datetime.datetime.utcnow().isoformat())
        await post_purchase(ctx.bot, user.full_name, user.username or "", item["name"], item["emoji"])
        text = (
            f"✅ *Compra realizada\\!*\n\n"
            f"🆔 Pedido: `{order_id}`\n"
            f"🎁 Produto: *{_esc(item['name'])}*\n"
            f"💳 Saldo do pin: R\\$ {face_value//100}\n"
            f"💸 Pago: R\\$ {brl2(amount_cents)} \\({discount_pct}% OFF\\)\n"
            f"💰 Saldo restante: *R\\$ {brl2(new_balance)}*\n\n"
            f"🔑 *Seu código:*\n`{_esc(gift_code)}`\n\n"
            f"Obrigado pela compra\\! 🙏"
        )
    else:
        credit_wallet(user.id, amount_cents, f"Estorno automático sem estoque — {order_id}")
        update_order(order_id, status="refunded")
        text = (
            f"⚠️ *Ops\\!* Esse item acabou de esgotar\\.\n"
            f"Seu saldo de *R\\$ {brl2(amount_cents)}* foi estornado automaticamente\\.\n\n"
            f"💰 Saldo atual: *R\\$ {brl2(new_balance + amount_cents)}*"
        )

    kb = [[InlineKeyboardButton("🛍️ Continuar comprando", callback_data="catalog"),
           InlineKeyboardButton("🔙 Menu",                 callback_data="start")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2)


# ──── Meus Pedidos ────────────────────────────────────────────

STATUS_LABEL = {"pending": "🕐", "delivered": "✅", "refunded": "↩️", "failed": "❌"}

async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    orders = get_user_orders(query.from_user.id)
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]

    if not orders:
        await query.edit_message_text("📭 Nenhum pedido ainda\\.", reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2)
        return

    lines = ["📦 *Histórico de Compras*\n"]
    for o in orders:
        ico  = STATUS_LABEL.get(o["status"], "❓")
        face = o.get("face_value_cents", 0)
        if o["status"] == "delivered" and o.get("gift_code"):
            lines.append(
                f"{ico} `{o['id']}` — *{_esc(o['product_name'])}* R\\$ {face//100}\n"
                f"   🔑 `{_esc(o['gift_code'])}`"
            )
        else:
            lines.append(f"{ico} `{o['id']}` — *{_esc(o['product_name'])}* — {_esc(o['status'])}")

    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Meu Perfil ─────────────────────────────────────────────

async def my_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user    = query.from_user
    profile = get_user_profile(user.id)
    tag = f"@{user.username}" if user.username else "—"
    text = (
        f"👤 *Meu Perfil*\n\n"
        f"🆔 ID Telegram: `{user.id}`\n"
        f"👤 Nome: *{_esc(user.full_name)}*\n"
        f"📛 Username: {_esc(tag)}\n\n"
        f"💰 Saldo atual: *R\\$ {brl2(profile['balance_cents'])}*\n"
        f"🛒 Total de compras: *{profile['total_purchases']}*\n"
        f"💸 Total gasto: *R\\$ {brl2(profile['total_spent'])}*"
    )
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2)


# ──── Ranking Top 10 ─────────────────────────────────────────

MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

async def ranking(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    top = get_top10_spenders()

    lines = ["🏆 *Top 10 — Maiores Compradores*\n"]
    if not top:
        lines.append("Nenhuma compra registrada ainda\\.")
    else:
        for i, u in enumerate(top):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}\\."
            name  = _esc(u["full_name"] or "Usuário")
            tag   = f" \\(@{_esc(u['username'])}\\)" if u.get("username") else ""
            lines.append(f"{medal} *{name}*{tag} — {u['total_purchases']} compra\\(s\\)")

    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]
    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Ajuda ───────────────────────────────────────────────────

async def help_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    discount_pct = int(DISCOUNT * 100)
    text = (
        f"❓ *Como funciona:*\n\n"
        f"1\\. Recarregue sua carteira via PIX\n"
        f"2\\. Escolha um giftcard no catálogo\n"
        f"3\\. O saldo é descontado e o código entregue na hora\n\n"
        f"💸 Todos os pins com *{discount_pct}% de desconto*\n\n"
        f"📩 Dúvidas? Use o botão *🎧 Suporte* no menu principal\\."
    )
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2)
