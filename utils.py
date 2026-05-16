"""
utils.py — Utilitários compartilhados.

MUDANÇAS:
- Adicionado RateLimiter baseado em sliding window (thread-safe)
- _esc() agora é a única cópia — history.py e outros devem importar daqui
- brl() renomeado de brl2 para consistência
- Adicionada função sanitize_code() para normalizar códigos de gift card
"""
import time
import logging
from collections import defaultdict, deque
from threading import Lock

from config import RATE_LIMIT_ACTIONS, RATE_LIMIT_WINDOW

logger = logging.getLogger(__name__)

# ─── Formatação ──────────────────────────────────────────────

def esc(text: str) -> str:
    """Escapa caracteres especiais do MarkdownV2 do Telegram."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


def brl(cents: int) -> str:
    """Converte centavos para string BRL. Ex: 8500 → '85,00'"""
    return f"{cents / 100:.2f}".replace(".", ",")


def sanitize_code(code: str) -> str:
    """Normaliza um código de gift card: strip + uppercase."""
    return code.strip().upper()


# ─── Rate Limiter ────────────────────────────────────────────

class RateLimiter:
    """
    Sliding window rate limiter thread-safe.
    Limita cada user_id a RATE_LIMIT_ACTIONS ações por RATE_LIMIT_WINDOW segundos.
    """

    def __init__(self, max_actions: int = RATE_LIMIT_ACTIONS,
                 window_seconds: int = RATE_LIMIT_WINDOW):
        self._max = max_actions
        self._window = window_seconds
        self._history: dict[int, deque] = defaultdict(deque)
        self._lock = Lock()

    def is_allowed(self, user_id: int) -> bool:
        """Retorna True se a ação for permitida, False se o limite foi atingido."""
        now = time.monotonic()
        with self._lock:
            dq = self._history[user_id]
            # Remove entradas fora da janela
            cutoff = now - self._window
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                logger.warning(f"[RateLimit] user_id={user_id} bloqueado ({len(dq)} ações/{self._window}s)")
                return False
            dq.append(now)
            return True

    def reset(self, user_id: int):
        """Limpa o histórico de um usuário (ex: após ban/unban)."""
        with self._lock:
            self._history.pop(user_id, None)


# Instância global — importar e usar em todos os handlers
rate_limiter = RateLimiter()


# ─── Whitelists de campos (anti SQL-injection via kwargs) ────

ORDER_FIELDS  = frozenset({"status", "gift_code", "delivered_at", "payment_id"})
TOPUP_FIELDS  = frozenset({"status", "payment_id", "pix_copia_cola", "paid_at"})
WALLET_FIELDS = frozenset({"username", "full_name", "balance_cents", "updated_at"})
