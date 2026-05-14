import base64
import datetime
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import DISCOUNT, MIN_TOPUP_CENTS
from models.database import (
    get_balance, upsert_wallet, debit_wallet, credit_wallet,
    create_order, update_order, get_user_orders,
    count_gift_codes, pop_gift_code,
    create_topup, update_topup,
    get_user_profile, get_top10_spenders,
    get_wallet_txns, get_all_products, get_product,
)
from services.mercadopago import create_pix_charge
from services.history import post_purchase


def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def brl2(cents: int) -> str:
    return f"{cents/100:.2f}".replace(".", ",")


def _main_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁  Comprar Gift Card", callback_data="catalog")],
        [InlineKeyboardButton("👤  Meu Perfil",        callback_data="profile"),
         InlineKeyboardButton("💵  Adicionar Saldo",   callback_data="topup_menu")],
        [InlineKeyboardButton("📜  Histórico",         callback_data="my_orders"),
         InlineKeyboardButton("🏆  Ranking",           callback_data="ranking")],
        [InlineKeyboardButton("🎧  Suporte",           callback_data="support")],
    ])


# ──── /start ─────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_wallet(user.id, user.username or "", user.full_name)
    profile = get_user_profile(user.id)

    text = (
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
        f"✅ *Ao continuar, você concorda com os termos*\n"
    )
    await update.message.reply_text(text, reply_markup=_main_kb(), parse_mode=ParseMode.MARKDOWN_V2)


async def back_to_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    upsert_wallet(user.id, user.username or "", user.full_name)
    profile = get_user_profile(user.id)

    text = (
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
        f"✅ *Ao continuar, você concorda com os termos*\n"
    )
    await query.edit_message_text(text, reply_markup=_main_kb(), parse_mode=ParseMode.MARKDOWN_V2)


# ──── Catálogo — produtos dinâmicos ──────────────────────────

async def show_catalog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance  = get_balance(query.from_user.id)
    products = get_all_products()

    if not products:
        await query.edit_message_text(
            "🎁 *Catálogo*\n\n😔 Nenhum produto disponível no momento\\.\nVolte em breve\\!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menu", callback_data="start")]]),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    kb = []
    for p in products:
        total = sum(count_gift_codes(p["key"], v["amount_cents"]) for v in p["values"])
        if total > 0:
            kb.append([InlineKeyboardButton(
                f"{p['name']} ({total} Disponíveis)",
                callback_data=f"product:{p['key']}",
            )])
    kb.append([InlineKeyboardButton("🔙 Menu", callback_data="start")])

    await query.edit_message_text(
        f"🎁 *Escolha o Gift Card:*\n\n💰 Seu saldo: *R\\$ {brl2(balance)}*",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def show_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    product_key = query.data.split(":")[1]
    product     = get_product(product_key)

    if not product:
        await query.answer("Produto não encontrado.", show_alert=True)
        return

    balance      = get_balance(query.from_user.id)
    discount_pct = int(DISCOUNT * 100)

    # Lista de estoque
    lines = [f"{product['emoji']} *{_esc(product['name'])}* \\- Estoque disponível\n"]
    for v in product["values"]:
        qty = count_gift_codes(product_key, v["amount_cents"])
        if qty > 0:
            lines.append(f"R\\$ {v['face_value_cents']//100},00 \\- {qty} Unidades Disponíveis")

    lines.append(f"\n💰 Seu saldo: *R\\$ {brl2(balance)}*")
    lines.append(f"Escolha o valor \\(saldo / preço com {discount_pct}% OFF\\):")

    # Botões em grade 2x2 — só valores com estoque
    kb, row = [], []
    for i, v in enumerate(product["values"]):
        qty   = count_gift_codes(product_key, v["amount_cents"])
        face  = v["face_value_cents"] // 100
        price = brl2(v["amount_cents"])

        if qty == 0:
            continue  # Sem estoque: não exibe o botão

        if balance < v["amount_cents"]:
            btn = InlineKeyboardButton(
                f"R$ {face} (R$ {price})",
                callback_data=f"insuf:{product_key}:{v['amount_cents']}",
            )
        else:
            btn = InlineKeyboardButton(
                f"R$ {face} (R$ {price})",
                callback_data=f"buy:{product_key}:{v['amount_cents']}:{v['face_value_cents']}",
            )
        row.append(btn)
        if len(row) == 2:
            kb.append(row); row = []
    if row:
        kb.append(row)
    
    if not kb:
        kb = [[InlineKeyboardButton("😔 Sem estoque disponível", callback_data="no_stock")]]

    kb.append([InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu"),
               InlineKeyboardButton("🔙 Voltar",          callback_data="catalog")])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Saldo insuficiente ──────────────────────────────────────

async def insufficient_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts        = query.data.split(":")
    amount_need  = int(parts[2])
    balance      = get_balance(query.from_user.id)

    await query.edit_message_text(
        f"🚫 *Saldo Insuficiente*\n\n"
        f"💳 Precisa: *R\\$ {brl2(amount_need)}*\n"
        f"💰 Você tem: *R\\$ {brl2(balance)}*\n\n"
        f"Adicione saldo via PIX para continuar\\:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu")],
            [InlineKeyboardButton("🔙 Voltar",          callback_data="catalog")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Compra ──────────────────────────────────────────────────

async def initiate_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "no_stock":
        await query.answer("❌ Sem estoque para este valor.", show_alert=True)
        return

    await query.answer()
    parts            = query.data.split(":")
    product_key      = parts[1]
    amount_cents     = int(parts[2])
    face_value_cents = int(parts[3])
    product          = get_product(product_key)
    discount_pct     = int(DISCOUNT * 100)
    user             = query.from_user

    ok, new_balance = debit_wallet(
        user.id, amount_cents,
        f"Compra {product['name']} R${face_value_cents//100}",
    )
    if not ok:
        balance = get_balance(user.id)
        await query.edit_message_text(
            f"🚫 *Saldo Insuficiente*\n\n"
            f"💳 Precisa: *R\\$ {brl2(amount_cents)}*\n"
            f"💰 Você tem: *R\\$ {brl2(balance)}*\n\n"
            f"Adicione saldo para continuar\\:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu")],
                [InlineKeyboardButton("🔙 Voltar",          callback_data="catalog")],
            ]),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    gift_code = pop_gift_code(product_key, amount_cents)
    order_id  = create_order(
        user_id=user.id, username=user.username or "",
        full_name=user.full_name, product_key=product_key,
        product_name=product["name"], amount_cents=amount_cents,
        face_value_cents=face_value_cents,
    )

    if gift_code:
        update_order(order_id, status="delivered", gift_code=gift_code,
                     delivered_at=datetime.datetime.utcnow().isoformat())
        await post_purchase(ctx.bot, user.full_name, user.username or "",
                            product["name"], product["emoji"])
        tag = f"@{user.username}" if user.username else user.full_name
        text = (
            f"🎉 *Compra Realizada com Sucesso\\!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 Usuário: {_esc(tag)} \\(ID: `{user.id}`\\)\n"
            f"💰 Valor do Gift: R\\$ {face_value_cents//100},00\n"
            f"🎮 Gift Card: {_esc(product['name'])}\n"
            f"🔑 Código: `{_esc(gift_code)}`\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱ Prazo para resgatar: *10 minutos*\n\n"
            f"⚠️ *Lembre\\-se:*\n"
            f"▫️ Não realizamos reembolsos ou cancelamentos\n"
            f"▫️ Resgate dentro do prazo indicado\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Saldo Atual: R\\$ {brl2(new_balance)}\n"
            f"Obrigado por comprar conosco\\! 🚀"
        )
    else:
        credit_wallet(user.id, amount_cents,
                      f"Estorno automático sem estoque — {order_id}")
        update_order(order_id, status="refunded")
        text = (
            f"⚠️ *Produto Esgotado\\!*\n\n"
            f"O item acabou no momento da compra\\.\n"
            f"Seu saldo de *R\\$ {brl2(amount_cents)}* foi devolvido automaticamente\\.\n\n"
            f"💰 Saldo atual: *R\\$ {brl2(new_balance + amount_cents)}*"
        )

    kb = [[InlineKeyboardButton("🎁 Comprar mais", callback_data="catalog"),
           InlineKeyboardButton("🔙 Menu",         callback_data="start")]]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Adicionar Saldo ─────────────────────────────────────────

MIN_TOPUP = 100
MAX_TOPUP = 100000


async def topup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = get_balance(query.from_user.id)
    await query.edit_message_text(
        f"💵 *Adicionar Saldo*\n\n💰 Saldo atual: *R\\$ {brl2(balance)}*\n\nEscolha a forma de pagamento:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💠  PIX (Automático)", callback_data="topup_pix")],
            [InlineKeyboardButton("🔙 Menu",              callback_data="start")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def topup_pix_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["waiting_topup_value"] = True
    await query.edit_message_text(
        f"💠 *Recarga via PIX*\n\n"
        f"Digite o valor que deseja adicionar\\.\n"
        f"📌 Mínimo: *R\\$ {MIN_TOPUP//100},00*\n"
        f"📌 Máximo: *R\\$ {MAX_TOPUP//100},00*\n"
        f"⏳ Expiração: *15 minutos*\n\n"
        f"Digite apenas números\\. Ex: `85`\n\n"
        f"⚠️ *Para recargas acima do valor máximo, entre em contato com o suporte\\!*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar",  callback_data="topup_menu"),
             InlineKeyboardButton("🎧 Suporte", callback_data="support")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def topup_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await topup_pix_info(update, ctx)


async def topup_receive_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("waiting_topup_value"):
        return
    ctx.user_data.pop("waiting_topup_value", None)
    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        amount_cents = round(float(raw) * 100)
    except ValueError:
        await update.message.reply_text(
            "❌ Valor inválido\\. Digite apenas números\\. Ex: `85`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    if amount_cents < MIN_TOPUP:
        await update.message.reply_text(
            f"❌ Valor mínimo: *R\\$ {MIN_TOPUP//100},00*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    if amount_cents > MAX_TOPUP:
        await update.message.reply_text(
            f"❌ Valor máximo: *R\\$ {MAX_TOPUP//100},00*\n\nPara recargas maiores, contate o suporte\\.",
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
            description=f"Recarga — {topup_id}",
            customer={"name": user.full_name},
        )
    except Exception as e:
        msg = f"⚠️ Erro ao gerar PIX\\. Tente novamente\\.\n\n`{_esc(str(e))}`"
        if via_query:
            await via_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    update_topup(topup_id, payment_id=charge["payment_id"], pix_copia_cola=charge["copia_cola"])
    chat_id = via_query.message.chat_id if via_query else update.effective_chat.id

    # QR Code como imagem primeiro
    qr_b64 = charge.get("qr_code_image", "")
    if qr_b64:
        try:
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=BytesIO(base64.b64decode(qr_b64)),
            )
        except Exception as e:
            print(f"[topup] Erro QR code: {e}")

    text = (
        f"🟢 *PAGAMENTO VIA PIX GERADO*\n\n"
        f"💰 Valor: R\\$ {brl2(amount_cents)}\n"
        f"⏱ Validade: 15 minutos\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 *Como pagar:*\n"
        f"1️⃣ Abra o app do seu banco\n"
        f"2️⃣ Escolha pagar via PIX\n"
        f"3️⃣ Escaneie o QR Code acima\n\n"
        f"👇 Ou copie o código:\n"
        f"`{_esc(charge['copia_cola'])}`"
    )
    kb = [[InlineKeyboardButton("❌ Cancelar", callback_data="start")]]
    if via_query:
        await via_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
        )


# ──── Histórico ───────────────────────────────────────────────

STATUS_LABEL = {"pending": "🕐", "delivered": "✅", "refunded": "↩️", "failed": "❌"}


async def my_orders(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    orders = get_user_orders(query.from_user.id)
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]

    if not orders:
        await query.edit_message_text(
            "📜 *Histórico de Compras*\n\nVocê ainda não realizou nenhuma compra\\.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    lines = ["📜 *Histórico de Compras*\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for o in orders:
        ico  = STATUS_LABEL.get(o["status"], "❓")
        face = o.get("face_value_cents", 0)
        if o["status"] == "delivered" and o.get("gift_code"):
            lines.append(
                f"{ico} *{_esc(o['product_name'])}* \\- R\\$ {face//100},00\n"
                f"🔑 `{_esc(o['gift_code'])}`\n"
                f"🆔 `{o['id']}`\n"
            )
        else:
            lines.append(f"{ico} *{_esc(o['product_name'])}* \\- {_esc(o['status'])}\n🆔 `{o['id']}`\n")

    await query.edit_message_text(
        "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Meu Perfil ─────────────────────────────────────────────

async def my_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user    = query.from_user
    profile = get_user_profile(user.id)
    tag = f"@{user.username}" if user.username else "Não definido"
    text = (
        f"👤 *Meu Perfil*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪪 ID: `{user.id}`\n"
        f"👤 Nome: *{_esc(user.full_name)}*\n"
        f"📛 Username: {_esc(tag)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Saldo disponível: *R\\$ {brl2(profile['balance_cents'])}*\n"
        f"🛒 Compras realizadas: *{profile['total_purchases']}*\n"
        f"💸 Total investido: *R\\$ {brl2(profile['total_spent'])}*"
    )
    kb = [
        [InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu")],
        [InlineKeyboardButton("🔙 Menu",            callback_data="start")],
    ]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Ranking ─────────────────────────────────────────────────

MEDALS = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


async def ranking(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    top   = get_top10_spenders()
    lines = ["🏆 *Top 10 Compradores*\n━━━━━━━━━━━━━━━━━━━━━━\n"]

    if not top:
        lines.append("Nenhuma compra registrada ainda\\.")
    else:
        for i, u in enumerate(top):
            medal = MEDALS[i] if i < len(MEDALS) else f"{i+1}\\."
            name  = _esc(u["full_name"] or "Usuário")
            tag   = f" \\(@{_esc(u['username'])}\\)" if u.get("username") else ""
            lines.append(f"{medal} *{name}*{tag} \\- {u['total_purchases']} compra\\(s\\)\n")

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
        f"1\\. Adicione saldo via PIX\n"
        f"2\\. Escolha um gift card no catálogo\n"
        f"3\\. O saldo é debitado e o código entregue na hora\n\n"
        f"💸 Todos os pins com *{discount_pct}% de desconto*\n\n"
        f"📩 Dúvidas? Use o botão *🎧 Suporte* no menu\\."
    )
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN_V2,
    )


async def wallet_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await topup_menu(update, ctx)
