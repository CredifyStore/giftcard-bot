"""
Publica mensagens no grupo de histórico.
Formato baseado na referência visual do usuário.
"""
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from config import HISTORY_GROUP_ID, BOT_TOKEN


def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


async def post_purchase(bot: Bot, full_name: str, username: str, product_name: str, emoji: str):
    """Posta quando uma compra é concluída."""
    display = full_name
    if username:
        display += f" (@{username})"

    text = (
        f"🎁 *Gift Card Comprado\\!*\n\n"
        f"👤 Cliente: {_esc(display)}\n"
        f"🎮 Gift: {_esc(product_name)}"
    )
    kb = [[InlineKeyboardButton("🛒 Comprar Agora", url=f"https://t.me/{(await bot.get_me()).username}")]]
    await _send(bot, text, kb)


async def post_topup(bot: Bot, full_name: str, username: str, amount_cents: int):
    """Posta quando uma recarga é confirmada."""
    display = full_name
    if username:
        display += f" (@{username})"

    text = (
        f"💰 *Nova recarga realizada\\!*\n\n"
        f"👤 Cliente: {_esc(display)}"
    )
    kb = [[InlineKeyboardButton("💵 Recarregar agora", url=f"https://t.me/{(await bot.get_me()).username}")]]
    await _send(bot, text, kb)


async def post_stock_update(bot: Bot, product_name: str, emoji: str, label: str, qty: int):
    """Posta quando admin adiciona estoque."""
    text = (
        f"🎁 *NOVO ESTOQUE ADICIONADO\\!*\n\n"
        f"🎮 Gift: {_esc(product_name)}\n"
        f"📦 Quantidade: {qty} gifts\n"
        f"🚀 Corra e garanta o seu\\!"
    )
    kb = [[InlineKeyboardButton("🛒 Comprar Agora", url=f"https://t.me/{(await bot.get_me()).username}")]]
    await _send(bot, text, kb)


async def _send(bot: Bot, text: str, kb=None):
    try:
        reply_markup = InlineKeyboardMarkup(kb) if kb else None
        await bot.send_message(
            chat_id=HISTORY_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=reply_markup,
        )
    except Exception as e:
        print(f"[history] Erro ao postar no grupo: {e}")
