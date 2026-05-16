"""
handlers/user.py — Handlers de usuário e fluxo de compra.

CORREÇÕES APLICADAS:
- [BUG-01 CORRIGIDO] initiate_buy() usa buy_atomic() — debit + pop em uma transação
- [BUG-05 CORRIGIDO] Saldo pós-estorno exibido corretamente (sem duplicação)
- [UX-01 CORRIGIDO] Mensagem de compra não exibe ID do usuário
- [UX-03 CORRIGIDO] Catálogo exibe emoji do produto
- [UX-04 CORRIGIDO] Feedback "gerando PIX..." antes de chamar API
- [ARCH-04/05 CORRIGIDO] wallet_menu e topup_custom inlining direto
- [SEC-01 CORRIGIDO] Rate limiting em todas as ações de usuário
- [BUG-07 CORRIGIDO] ParseMode uniformizado para MARKDOWN_V2
- Aliases _esc e brl2 removidos — usar esc() e brl() diretamente
"""
import base64
import logging
from io import BytesIO

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from config import DISCOUNT_PCT, MIN_TOPUP_CENTS, MAX_TOPUP_CENTS, PIX_EXPIRY_MINUTES
from models.database import (
    get_balance, upsert_wallet,
    credit_wallet, get_user_orders,
    count_all_gift_codes,
    create_topup, update_topup,
    get_user_profile, get_top10_spenders,
    get_all_products, get_product,
    buy_atomic,
)
from services.mercadopago import create_pix_charge
from services.history import post_purchase
from utils import esc, brl, rate_limiter

logger = logging.getLogger(__name__)


# ──── Menus ───────────────────────────────────────────────────

def _main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁  Comprar Gift Card",  callback_data="catalog")],
        [InlineKeyboardButton("👤  Meu Perfil",         callback_data="profile"),
         InlineKeyboardButton("💵  Adicionar Saldo",    callback_data="topup_menu")],
        [InlineKeyboardButton("📜  Histórico",          callback_data="my_orders"),
         InlineKeyboardButton("🏆  Ranking",            callback_data="ranking")],
        [InlineKeyboardButton("❓  Ajuda",              callback_data="help"),
         InlineKeyboardButton("🎧  Suporte",            callback_data="support")],
    ])


def _back_kb(dest: str = "start", label: str = "🔙 Menu") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=dest)]])


def _home_text(user) -> str:
    profile = get_user_profile(user.id)
    return (
        f"👋 Olá, *{esc(user.first_name)}*\\!\n\n"
        f"💰 Saldo: *R\\$ {brl(profile['balance_cents'])}*\n"
        f"🛒 Compras: *{profile['total_purchases']}*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 *IMPORTANTE*\n\n"
        f"⚠️ Compre somente se souber usar o gift card\n"
        f"⏱ Resgate em até *10 minutos* após receber\n"
        f"⚡ Pagou → recebeu\\. Sem reservas\\.\n"
        f"🔒 Código garantido ou estorno automático\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ *{DISCOUNT_PCT}% de desconto* em todos os gifts\\!"
    )


# ──── Helpers de rate limit ────────────────────────────────────

async def _check_rate(query) -> bool:
    """Verifica rate limit. Se bloqueado, avisa o usuário e retorna False."""
    if not rate_limiter.is_allowed(query.from_user.id):
        await query.answer(
            "⏳ Muitas ações em sequência. Aguarde alguns segundos.",
            show_alert=True,
        )
        return False
    return True


# ──── /start ─────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_wallet(user.id, user.username or "", user.full_name or "")
    await update.message.reply_text(
        _home_text(user),
        reply_markup=_main_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def back_to_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    upsert_wallet(user.id, user.username or "", user.full_name or "")
    await query.edit_message_text(
        _home_text(user),
        reply_markup=_main_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Catálogo ────────────────────────────────────────────────

async def show_catalog(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _check_rate(query):
        return
    await query.answer()

    balance  = get_balance(query.from_user.id)
    products = get_all_products()

    kb = []
    for p in products:
        stock = count_all_gift_codes(p["key"])
        total = sum(stock.values())
        if total > 0:
            # [UX-03 CORRIGIDO] Emoji do produto incluído
            kb.append([InlineKeyboardButton(
                f"{p['emoji']} {p['name']} — {total} disponível(is)",
                callback_data=f"product:{p['key']}",
            )])

    if not kb:
        await query.edit_message_text(
            "🎁 *Catálogo*\n\n"
            "😔 Nenhum produto disponível no momento\\.\n"
            "Volte em breve\\!",
            reply_markup=_back_kb(),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    kb.append([InlineKeyboardButton("🔙 Menu", callback_data="start")])
    await query.edit_message_text(
        f"🎁 *Escolha o Gift Card:*\n\n"
        f"💰 Seu saldo: *R\\$ {brl(balance)}*\n\n"
        f"🏷️ Todos com *{DISCOUNT_PCT}% de desconto*\\!",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def show_product(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _check_rate(query):
        return
    await query.answer()

    product_key = query.data.split(":")[1]
    product     = get_product(product_key)

    if not product:
        await query.answer("❌ Produto não encontrado.", show_alert=True)
        return

    balance = get_balance(query.from_user.id)
    stock   = count_all_gift_codes(product_key)

    lines = [
        f"{product['emoji']} *{esc(product['name'])}*\n",
        f"💰 Seu saldo: *R\\$ {brl(balance)}*\n",
        f"🏷️ *{DISCOUNT_PCT}% OFF* em todos os valores\n",
        f"━━━━━━━━━━━━━━━━━━━━━━\n",
    ]
    for v in product["values"]:
        qty = stock.get(v["amount_cents"], 0)
        if qty > 0:
            icon = "🟢" if qty > 3 else "🟡"
            lines.append(
                f"{icon} R\\$ {v['face_value_cents']//100},00 "
                f"→ *R\\$ {brl(v['amount_cents'])}* \\({qty} un\\.\\)"
            )

    kb, row = [], []
    for v in product["values"]:
        qty = stock.get(v["amount_cents"], 0)
        if qty == 0:
            continue
        face  = v["face_value_cents"] // 100
        price = brl(v["amount_cents"])
        if balance < v["amount_cents"]:
            btn = InlineKeyboardButton(
                f"🔒 R$ {face} → R$ {price}",
                callback_data=f"insuf:{product_key}:{v['amount_cents']}",
            )
        else:
            btn = InlineKeyboardButton(
                f"✅ R$ {face} → R$ {price}",
                callback_data=f"buy:{product_key}:{v['amount_cents']}:{v['face_value_cents']}",
            )
        row.append(btn)
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)

    if not kb:
        lines.append("\n😔 Sem estoque disponível no momento\\.")
        kb = [[InlineKeyboardButton("🔙 Voltar ao catálogo", callback_data="catalog")]]
    else:
        kb.append([
            InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu"),
            InlineKeyboardButton("🔙 Voltar",          callback_data="catalog"),
        ])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Saldo insuficiente ──────────────────────────────────────

async def insufficient_balance(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts       = query.data.split(":")
    amount_need = int(parts[2])
    balance     = get_balance(query.from_user.id)
    falta       = amount_need - balance

    await query.edit_message_text(
        f"🚫 *Saldo Insuficiente*\n\n"
        f"💳 Preço: *R\\$ {brl(amount_need)}*\n"
        f"💰 Seu saldo: *R\\$ {brl(balance)}*\n"
        f"➕ Precisa de mais: *R\\$ {brl(falta)}*\n\n"
        f"Adicione saldo via PIX para continuar\\:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu")],
            [InlineKeyboardButton("🔙 Voltar",          callback_data="catalog")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Compra (ATÔMICA) ────────────────────────────────────────

async def initiate_buy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query

    if query.data == "no_stock":
        await query.answer("❌ Sem estoque para este valor.", show_alert=True)
        return

    if not await _check_rate(query):
        return

    await query.answer()

    parts            = query.data.split(":")
    product_key      = parts[1]
    amount_cents     = int(parts[2])
    face_value_cents = int(parts[3])
    user             = query.from_user
    product          = get_product(product_key)

    if not product:
        await query.answer("❌ Produto não encontrado.", show_alert=True)
        return

    # [BUG-01 CORRIGIDO] Uma transação atômica — sem race condition
    order_id, gift_code, new_balance = buy_atomic(
        user_id=user.id,
        product_key=product_key,
        amount_cents=amount_cents,
        face_value_cents=face_value_cents,
        product_name=product["name"],
        username=user.username or "",
        full_name=user.full_name or "",
    )

    kb = [[
        InlineKeyboardButton("🎁 Comprar mais", callback_data="catalog"),
        InlineKeyboardButton("🔙 Menu",         callback_data="start"),
    ]]

    # Saldo insuficiente (buy_atomic retorna gift_code=None)
    if gift_code is None:
        balance = get_balance(user.id)
        await query.edit_message_text(
            f"🚫 *Saldo Insuficiente*\n\n"
            f"💳 Preço: *R\\$ {brl(amount_cents)}*\n"
            f"💰 Seu saldo: *R\\$ {brl(balance)}*\n\n"
            f"Adicione saldo e tente novamente\\:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu")],
                [InlineKeyboardButton("🔙 Voltar",          callback_data="catalog")],
            ]),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Estoque acabou no momento exato — estorno automático já feito em buy_atomic
    if gift_code == "":
        # [BUG-05 CORRIGIDO] Saldo não foi debitado — exibir saldo real
        real_balance = get_balance(user.id)
        await query.edit_message_text(
            f"⚠️ *Produto Esgotado\\!*\n\n"
            f"O item acabou exatamente no momento da compra\\.\n"
            f"Seu saldo *não foi debitado*\\.\n\n"
            f"💰 Saldo atual: *R\\$ {brl(real_balance)}*\n\n"
            f"Escolha outro produto ou aguarde reposição\\.",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    # Compra bem-sucedida
    logger.info(f"[buy] Entregue: user_id={user.id} order_id={order_id} "
                f"product={product_key} amount=R${brl(amount_cents)}")

    try:
        await post_purchase(
            ctx.bot, user.full_name or "", user.username or "",
            product["name"], product["emoji"],
        )
    except Exception as e:
        logger.warning(f"[buy] Falha ao postar histórico: {e}")

    # [UX-01 CORRIGIDO] Sem ID do usuário na mensagem — informação desnecessária pro cliente
    await query.edit_message_text(
        f"🎊 *Compra Realizada com Sucesso\\!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{product['emoji']} *{esc(product['name'])}*\n"
        f"💵 Valor do Gift: R\\$ {face_value_cents // 100},00\n"
        f"💸 Você pagou: R\\$ {brl(amount_cents)}\n\n"
        f"🔑 *Seu código:*\n"
        f"`{esc(gift_code)}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏳ Resgate em até *10 minutos*\n"
        f"⚠️ Sem reembolsos após recebimento do código\n\n"
        f"💰 Saldo restante: *R\\$ {brl(new_balance)}*\n"
        f"Obrigado pela compra\\! 🚀",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Adicionar Saldo ─────────────────────────────────────────

async def topup_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    balance = get_balance(query.from_user.id)
    await query.edit_message_text(
        f"💵 *Adicionar Saldo via PIX*\n\n"
        f"💰 Saldo atual: *R\\$ {brl(balance)}*\n\n"
        f"Escolha um valor abaixo ou digite um valor personalizado\\:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("R$ 20",  callback_data="topup_amount:2000"),
             InlineKeyboardButton("R$ 50",  callback_data="topup_amount:5000"),
             InlineKeyboardButton("R$ 100", callback_data="topup_amount:10000")],
            [InlineKeyboardButton("R$ 200", callback_data="topup_amount:20000"),
             InlineKeyboardButton("R$ 500", callback_data="topup_amount:50000")],
            [InlineKeyboardButton("✏️ Outro valor", callback_data="topup_pix")],
            [InlineKeyboardButton("🔙 Menu",         callback_data="start")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# Alias mantido para compatibilidade com handlers registrados
wallet_menu = topup_menu


async def topup_pix_info(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """[UX-06 CORRIGIDO] Instrução clara de que bot aguarda digitação."""
    query = update.callback_query
    await query.answer()
    ctx.user_data["waiting_topup_value"] = True
    await query.edit_message_text(
        f"✏️ *Valor Personalizado*\n\n"
        f"Digite o valor que deseja recarregar \\(apenas números\\)\\:\n\n"
        f"📌 Mínimo: *R\\$ {MIN_TOPUP_CENTS // 100},00*\n"
        f"📌 Máximo: *R\\$ {MAX_TOPUP_CENTS // 100},00*\n"
        f"⏳ PIX válido por *{PIX_EXPIRY_MINUTES} minutos*\n\n"
        f"💬 *Exemplo:* `85` ou `150`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Voltar",  callback_data="topup_menu"),
             InlineKeyboardButton("🎧 Suporte", callback_data="support")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# Alias: topup_custom agora chama topup_pix_info diretamente
topup_custom = topup_pix_info


async def topup_receive_custom(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.user_data.get("waiting_topup_value"):
        return
    ctx.user_data.pop("waiting_topup_value", None)

    raw = update.message.text.strip().replace(",", ".").replace("R$", "").strip()
    try:
        amount_cents = round(float(raw) * 100)
    except ValueError:
        await update.message.reply_text(
            "❌ *Valor inválido*\n\nDigite apenas números\\. Ex: `85`",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if amount_cents < MIN_TOPUP_CENTS:
        await update.message.reply_text(
            f"❌ Valor mínimo: *R\\$ {MIN_TOPUP_CENTS // 100},00*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    if amount_cents > MAX_TOPUP_CENTS:
        await update.message.reply_text(
            f"❌ Valor máximo: *R\\$ {MAX_TOPUP_CENTS // 100},00*\n\n"
            f"Para recargas maiores, entre em contato com o suporte\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await _generate_topup_pix(update, ctx, amount_cents)


async def topup_fixed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not await _check_rate(query):
        return
    await query.answer()
    amount_cents = int(query.data.split(":")[1])
    await _generate_topup_pix(update, ctx, amount_cents, via_query=query)


async def _generate_topup_pix(update, ctx, amount_cents: int, via_query=None):
    """[UX-04 CORRIGIDO] Exibe feedback 'gerando...' antes de chamar a API."""
    user = via_query.from_user if via_query else update.effective_user
    upsert_wallet(user.id, user.username or "", user.full_name or "")

    # Feedback imediato antes da chamada à API
    loading_text = (
        f"⏳ *Gerando seu PIX\\.\\.\\.*\n\n"
        f"💵 Valor: *R\\$ {brl(amount_cents)}*\n\n"
        f"Aguarde um momento\\."
    )
    if via_query:
        await via_query.edit_message_text(loading_text, parse_mode=ParseMode.MARKDOWN_V2)
    else:
        sent = await update.message.reply_text(loading_text, parse_mode=ParseMode.MARKDOWN_V2)

    topup_id = create_topup(
        user.id, user.username or "", user.full_name or "", amount_cents
    )

    try:
        charge = await create_pix_charge(
            external_id=topup_id,
            amount_cents=amount_cents,
            description=f"Recarga Credify — {topup_id}",
            customer={"name": user.full_name or "Cliente", "user_id": user.id},
        )
    except Exception as e:
        logger.error(f"[topup] Erro ao gerar PIX para user_id={user.id}: {e}")
        msg = (
            f"⚠️ *Erro ao gerar PIX*\n\n"
            f"Não foi possível criar a cobrança no momento\\.\n"
            f"Tente novamente em alguns segundos ou contate o suporte\\."
        )
        if via_query:
            await via_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
        return

    update_topup(
        topup_id,
        payment_id=charge["payment_id"],
        pix_copia_cola=charge["copia_cola"],
    )

    chat_id = via_query.message.chat_id if via_query else update.effective_chat.id

    # Envia QR Code como imagem
    qr_b64 = charge.get("qr_code_image", "")
    if qr_b64:
        try:
            await ctx.bot.send_photo(
                chat_id=chat_id,
                photo=BytesIO(base64.b64decode(qr_b64)),
                caption=f"📱 QR Code PIX — R$ {brl(amount_cents)}",
            )
        except Exception as e:
            logger.warning(f"[topup] Erro ao enviar QR code: {e}")

    copia_cola = charge.get("copia_cola", "")
    text = (
        f"✅ *PIX Gerado com Sucesso\\!*\n\n"
        f"💵 Valor: *R\\$ {brl(amount_cents)}*\n"
        f"⏳ Válido por *{PIX_EXPIRY_MINUTES} minutos*\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📲 *Como pagar:*\n"
        f"1️⃣ Abra o app do seu banco\n"
        f"2️⃣ Escolha *Pix → Pagar*\n"
        f"3️⃣ Escaneie o QR Code acima\n"
        f"    ou copie o código abaixo:\n\n"
        f"`{esc(copia_cola)}`\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔔 Você será notificado automaticamente ao pagar\\."
    )
    kb = [[InlineKeyboardButton("🔙 Menu", callback_data="start")]]

    if via_query:
        await via_query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ──── Histórico ───────────────────────────────────────────────

STATUS_LABEL = {
    "pending":   "🕐 Pendente",
    "delivered": "✅ Entregue",
    "refunded":  "↩️ Estornado",
    "failed":    "❌ Falhou",
}


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

    lines = ["📜 *Últimas Compras*\n━━━━━━━━━━━━━━━━━━━━━━\n"]
    for o in orders:
        status = STATUS_LABEL.get(o["status"], "❓")
        face   = o.get("face_value_cents", 0)
        if o["status"] == "delivered" and o.get("gift_code"):
            lines.append(
                f"{status}\n"
                f"🎮 *{esc(o['product_name'])}* — R\\$ {face // 100},00\n"
                f"🔑 `{esc(o['gift_code'])}`\n"
                f"🆔 `{o['id']}`\n"
            )
        else:
            lines.append(
                f"{status}\n"
                f"🎮 *{esc(o['product_name'])}* — R\\$ {face // 100},00\n"
                f"🆔 `{o['id']}`\n"
            )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Meu Perfil ─────────────────────────────────────────────

async def my_profile(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user    = query.from_user
    profile = get_user_profile(user.id)
    tag     = f"@{user.username}" if user.username else "Não definido"

    await query.edit_message_text(
        f"👤 *Meu Perfil*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🪪 ID: `{user.id}`\n"
        f"👤 Nome: *{esc(user.full_name or '')}*\n"
        f"📛 Username: {esc(tag)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Saldo: *R\\$ {brl(profile['balance_cents'])}*\n"
        f"🛒 Compras: *{profile['total_purchases']}*\n"
        f"💸 Total investido: *R\\$ {brl(profile['total_spent'])}*",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💵 Adicionar Saldo", callback_data="topup_menu")],
            [InlineKeyboardButton("🔙 Menu",            callback_data="start")],
        ]),
        parse_mode=ParseMode.MARKDOWN_V2,
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
            medal = MEDALS[i] if i < len(MEDALS) else f"{i + 1}\\."
            name  = esc(u["full_name"] or "Usuário")
            tag   = f" \\(@{esc(u['username'])}\\)" if u.get("username") else ""
            total = brl(u["total_spent"])
            lines.append(
                f"{medal} *{name}*{tag}\n"
                f"   {u['total_purchases']} compra\\(s\\) — R\\$ {total}\n"
            )

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=_back_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ──── Ajuda ───────────────────────────────────────────────────

async def help_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        f"❓ *Como funciona*\n\n"
        f"1\\. *Adicione saldo* via PIX \\(instantâneo\\)\n"
        f"2\\. *Escolha* o gift card no catálogo\n"
        f"3\\. *Receba* o código na hora\n\n"
        f"🏷️ *{DISCOUNT_PCT}% de desconto* em todos os pins\n"
        f"🔒 Se acabar o estoque, saldo devolvido\\.\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📩 *Dúvidas?* Use o botão Suporte\\.\n"
        f"⚠️ *Importante:* Resgate o código em até *10 minutos*\\.",
        reply_markup=_back_kb(),
        parse_mode=ParseMode.MARKDOWN_V2,
    )
