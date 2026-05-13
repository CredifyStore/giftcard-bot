"""
Publica mensagens no grupo de histórico.
O grupo deve ter o bot como admin e estar configurado
para que apenas admins possam enviar mensagens.

Eventos publicados:
- Compra realizada
- Recarga de saldo confirmada
- Estoque atualizado pelo admin
"""
from telegram import Bot
from telegram.constants import ParseMode
from config import HISTORY_GROUP_ID


def _esc(text: str) -> str:
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def _display(full_name: str, username: str) -> str:
    d = full_name
    if username:
        d += f" (@{username})"
    return d


async def post_purchase(bot: Bot, full_name: str, username: str, product_name: str, emoji: str):
    """Posta quando uma compra é concluída. Sem valores."""
    text = (
        f"🎉 *Gift Card Comprado\\!*\n\n"
        f"👤 *Cliente:* {_esc(_display(full_name, username))}\n"
        f"🎁 *Gift:* {emoji} {_esc(product_name)}"
    )
    await _send(bot, text)


async def post_topup(bot: Bot, full_name: str, username: str, amount_cents: int):
    """Posta quando uma recarga de saldo é confirmada."""
    text = (
        f"💳 *Recarga Realizada\\!*\n\n"
        f"👤 *Cliente:* {_esc(_display(full_name, username))}\n"
        f"💰 *Valor:* R\\$ {amount_cents/100:.2f}"
    )
    await _send(bot, text)


async def post_stock_update(bot: Bot, product_name: str, emoji: str, label: str, qty: int):
    """Posta quando o admin adiciona códigos ao estoque."""
    text = (
        f"📦 *Estoque Atualizado\\!*\n\n"
        f"{emoji} *{_esc(product_name)}*\n"
        f"💳 Valor: {_esc(label)}\n"
        f"✅ *\\+{qty} código\\(s\\) disponível\\(is\\)*"
    )
    await _send(bot, text)


async def _send(bot: Bot, text: str):
    try:
        await bot.send_message(
            chat_id=HISTORY_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        print(f"[history] Erro ao postar no grupo: {e}")
