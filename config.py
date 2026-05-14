import os
from dotenv import load_dotenv

load_dotenv()

# ─── Telegram ───────────────────────────────────────────────
BOT_TOKEN        = os.getenv("BOT_TOKEN", "SEU_TOKEN_AQUI")
ADMIN_IDS        = list(map(int, os.getenv("ADMIN_IDS", "123456789").split(",")))
HISTORY_GROUP_ID = int(os.getenv("HISTORY_GROUP_ID", "-100000000001"))

# ─── Mercado Pago ────────────────────────────────────────────
MP_ACCESS_TOKEN  = os.getenv("MP_ACCESS_TOKEN", "SEU_ACCESS_TOKEN_MP")
MP_BASE_URL      = "https://api.mercadopago.com"
# Webhook secret: string que você define e cadastra no painel do MP
WEBHOOK_SECRET   = os.getenv("WEBHOOK_SECRET", "webhook_secret_aqui")

# ─── App ─────────────────────────────────────────────────────
DATABASE_PATH = "giftcards.db"
WEBHOOK_URL   = os.getenv("WEBHOOK_URL", "https://seusite.com/webhook/mercadopago")
PORT          = int(os.getenv("PORT", 8443))

# ─── Saldo ───────────────────────────────────────────────────
MIN_TOPUP_CENTS = 100   # R$ 1,00 mínimo de recarga

# ─── Desconto global ─────────────────────────────────────────
# Para mudar para 20%: DISCOUNT = 0.20
DISCOUNT = 0.15

def price(face_value_cents: int) -> int:
    return round(face_value_cents * (1 - DISCOUNT))

# ─── Catálogo ────────────────────────────────────────────────
