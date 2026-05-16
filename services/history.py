"""
Publica mensagens no grupo de histórico.
"""
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from config import HISTORY_GROUP_ID


def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


# Cache do username do bot para não chamar get_me() a cada post
_BOT_USERNAME = None

async def _get_bot_url(bot: Bot) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await bot.get_me()
        _BOT_USERNAME = me.username
    return f"https://t.me/{_BOT_USERNAME}"


async def post_purchase(bot: Bot, full_name: str, username: str, product_name: str, emoji: str):
    display = full_name + (f" (@{username})" if username else "")
    text = (
        f"🎁 *Gift Card Comprado\\!*\n\n"
        f"👤 Cliente: {_esc(display)}\n"
        f"🎮 Gift: {_esc(product_name)}"
    )
    url = await _get_bot_url(bot)
    await _send(bot, text, [[InlineKeyboardButton("🛒 Comprar Agora", url=url)]])


async def post_topup(bot: Bot, full_name: str, username: str, amount_cents: int):
    display = full_name + (f" (@{username})" if username else "")
    text = (
        f"💰 *Nova recarga realizada\\!*\n\n"
        f"👤 Cliente: {_esc(display)}"
    )
    url = await _get_bot_url(bot)
    await _send(bot, text, [[InlineKeyboardButton("💵 Recarregar agora", url=url)]])


async def post_stock_update(bot: Bot, product_name: str, emoji: str, label: str, qty: int):
    text = (
        f"🎁 *NOVO ESTOQUE ADICIONADO\\!*\n\n"
        f"🎮 Gift: {_esc(product_name)}\n"
        f"📦 Quantidade: {qty} gifts\n"
        f"🚀 Corra e garanta o seu\\!"
    )
    url = await _get_bot_url(bot)
    await _send(bot, text, [[InlineKeyboardButton("🛒 Comprar Agora", url=url)]])


async def _send(bot: Bot, text: str, kb=None):
    try:
        await bot.send_message(
            chat_id=HISTORY_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        )
    except Exception as e:
        print(f"[history] Erro ao postar no grupo {HISTORY_GROUP_ID}: {e}")
