"""
services/history.py — Publica mensagens no grupo de histórico do Telegram.

CORREÇÕES APLICADAS:
- [ARCH-03 CORRIGIDO] _esc() duplicada removida — importa de utils.py
- Cache de username do bot mantido para performance
- Logs estruturados em cada envio
- Silencia falha sem crashar (grupo pode estar indisponível)
"""
import logging
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode

from config import HISTORY_GROUP_ID
from utils import esc, brl

logger = logging.getLogger(__name__)

_BOT_USERNAME: str | None = None


async def _get_bot_url(bot: Bot) -> str:
    global _BOT_USERNAME
    if not _BOT_USERNAME:
        me = await bot.get_me()
        _BOT_USERNAME = me.username
    return f"https://t.me/{_BOT_USERNAME}"


async def post_purchase(bot: Bot, full_name: str, username: str,
                        product_name: str, emoji: str) -> None:
    display = full_name + (f" (@{username})" if username else "")
    text = (
        f"🎁 *Gift Card Vendido\\!*\n\n"
        f"👤 Cliente: {esc(display)}\n"
        f"🎮 Produto: {esc(emoji)} {esc(product_name)}"
    )
    url = await _get_bot_url(bot)
    await _send(bot, text, [[InlineKeyboardButton("🛒 Comprar Agora", url=url)]])


async def post_topup(bot: Bot, full_name: str, username: str,
                     amount_cents: int) -> None:
    display = full_name + (f" (@{username})" if username else "")
    text = (
        f"💰 *Nova Recarga Confirmada\\!*\n\n"
        f"👤 Cliente: {esc(display)}\n"
        f"💵 Valor: R\\$ {brl(amount_cents)}"
    )
    url = await _get_bot_url(bot)
    await _send(bot, text, [[InlineKeyboardButton("💵 Recarregar Agora", url=url)]])


async def post_stock_update(bot: Bot, product_name: str, emoji: str,
                            label: str, qty: int) -> None:
    text = (
        f"📦 *Estoque Atualizado\\!*\n\n"
        f"{esc(emoji)} *{esc(product_name)}*\n"
        f"🏷️ Valor: {esc(label)}\n"
        f"➕ Adicionados: *{qty} gift\\(s\\)*\n\n"
        f"🚀 Corra e garanta o seu\\!"
    )
    url = await _get_bot_url(bot)
    await _send(bot, text, [[InlineKeyboardButton("🛒 Comprar Agora", url=url)]])


async def _send(bot: Bot, text: str, kb: list | None = None) -> None:
    if not HISTORY_GROUP_ID:
        logger.debug("[history] HISTORY_GROUP_ID não configurado — pulando post")
        return
    try:
        await bot.send_message(
            chat_id=HISTORY_GROUP_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup(kb) if kb else None,
        )
    except Exception as e:
        logger.warning(f"[history] Falha ao postar no grupo {HISTORY_GROUP_ID}: {e}")
