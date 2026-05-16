"""
config.py — Configurações centralizadas do Credify Bot.

MUDANÇAS:
- HISTORY_GROUP_ID agora tem fallback 0 e é validado no startup
- Adicionados limites de rate limit configuráveis
- DISCOUNT documentado claramente
- Removida dependência de default strings mágicas
"""
import os
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Telegram ────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    sys.exit("❌ FATAL: BOT_TOKEN não configurado no .env")

_admin_raw = os.getenv("ADMIN_IDS", "")
if not _admin_raw:
    sys.exit("❌ FATAL: ADMIN_IDS não configurado no .env")
try:
    ADMIN_IDS: set[int] = set(map(int, _admin_raw.split(",")))
except ValueError:
    sys.exit("❌ FATAL: ADMIN_IDS deve conter apenas números separados por vírgula")

HISTORY_GROUP_ID = int(os.getenv("HISTORY_GROUP_ID", "0"))

# ─── Mercado Pago ────────────────────────────────────────────
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN", "")
if not MP_ACCESS_TOKEN:
    logger.warning("⚠️  MP_ACCESS_TOKEN não configurado — pagamentos PIX não funcionarão")

MP_BASE_URL    = "https://api.mercadopago.com"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
if not WEBHOOK_SECRET:
    logger.warning("⚠️  WEBHOOK_SECRET não configurado — assinatura do webhook não será validada")

# ─── App ─────────────────────────────────────────────────────
DATABASE_PATH = os.getenv("DATABASE_PATH", "giftcards.db")
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "")
PORT          = int(os.getenv("PORT", "8443"))

# ─── Limites de recarga ──────────────────────────────────────
MIN_TOPUP_CENTS = int(os.getenv("MIN_TOPUP_CENTS", "100"))    # R$ 1,00
MAX_TOPUP_CENTS = int(os.getenv("MAX_TOPUP_CENTS", "100_000")) # R$ 1.000,00

# ─── Desconto global ─────────────────────────────────────────
# 0.15 = 15% OFF | 0.20 = 20% OFF
DISCOUNT = float(os.getenv("DISCOUNT", "0.15"))
DISCOUNT_PCT = int(DISCOUNT * 100)

def price(face_value_cents: int) -> int:
    """Calcula preço com desconto a partir do valor de face em centavos."""
    return round(face_value_cents * (1 - DISCOUNT))

# ─── Rate Limiting ───────────────────────────────────────────
# Máximo de ações por usuário por janela de tempo
RATE_LIMIT_ACTIONS = int(os.getenv("RATE_LIMIT_ACTIONS", "10"))
RATE_LIMIT_WINDOW  = int(os.getenv("RATE_LIMIT_WINDOW",  "60"))  # segundos

# ─── PIX ─────────────────────────────────────────────────────
PIX_EXPIRY_MINUTES = int(os.getenv("PIX_EXPIRY_MINUTES", "30"))
