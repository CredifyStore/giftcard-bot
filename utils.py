"""
Funções utilitárias compartilhadas entre todos os módulos.
Centraliza _esc(), brl2() e constantes de formatação.
"""


def esc(text: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 do Telegram."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def brl(cents: int) -> str:
    """Converte centavos para string BRL formatada. Ex: 8500 → '85,00'"""
    return f"{cents / 100:.2f}".replace(".", ",")


# Campos permitidos para update dinâmico — evita SQL injection via kwargs
ORDER_FIELDS   = frozenset({"status", "gift_code", "delivered_at", "payment_id"})
TOPUP_FIELDS   = frozenset({"status", "payment_id", "pix_copia_cola", "paid_at"})
WALLET_FIELDS  = frozenset({"username", "full_name", "balance_cents", "updated_at"})
