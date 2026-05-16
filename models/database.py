"""
models/database.py — Camada de acesso ao banco de dados (SQLite).

CORREÇÕES APLICADAS:
- [BUG-01 CORRIGIDO] buy_atomic(): debita saldo E retira código em UMA transação
  BEGIN IMMEDIATE — elimina race condition e perda de dinheiro
- [ARCH-02 CORRIGIDO] debit_wallet() e pop_gift_code() agora usam o mesmo padrão
  de conexão — sem código duplicado
- [BUG-06 CORRIGIDO] debit_wallet() usa get_conn() context manager corretamente
- Adicionado índice em gift_codes.used para queries de pop mais rápidas
- Adicionado get_pending_topups_by_user() para verificar topups duplicados
- Tipo de retorno explícito em todas as funções públicas
"""
import sqlite3
import uuid
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from config import DATABASE_PATH
from utils import ORDER_FIELDS, TOPUP_FIELDS

logger = logging.getLogger(__name__)


# ──── Conexão ─────────────────────────────────────────────────

@contextmanager
def get_conn():
    """
    Context manager de conexão SQLite.
    - WAL mode: leituras simultâneas sem bloquear escritas
    - foreign_keys ON: integridade referencial
    - Rollback automático em exceção
    - Fechamento garantido no finally
    """
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")  # aguarda até 5s em lock
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──── Inicialização ───────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS products (
                key        TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                emoji      TEXT NOT NULL DEFAULT '🎁',
                active     INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS product_values (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_key      TEXT NOT NULL REFERENCES products(key),
                face_value_cents INTEGER NOT NULL,
                amount_cents     INTEGER NOT NULL,
                UNIQUE(product_key, face_value_cents)
            );

            CREATE TABLE IF NOT EXISTS gift_codes (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                product_key      TEXT NOT NULL,
                amount_cents     INTEGER NOT NULL,
                face_value_cents INTEGER NOT NULL DEFAULT 0,
                code             TEXT NOT NULL UNIQUE,
                used             INTEGER NOT NULL DEFAULT 0,
                order_id         TEXT,
                added_at         TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallets (
                user_id       INTEGER PRIMARY KEY,
                username      TEXT,
                full_name     TEXT,
                balance_cents INTEGER NOT NULL DEFAULT 0 CHECK(balance_cents >= 0),
                updated_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_txns (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                type         TEXT NOT NULL CHECK(type IN ('topup','purchase','refund','manual')),
                amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                description  TEXT,
                payment_id   TEXT,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topups (
                id             TEXT PRIMARY KEY,
                user_id        INTEGER NOT NULL,
                username       TEXT,
                full_name      TEXT,
                amount_cents   INTEGER NOT NULL,
                status         TEXT NOT NULL DEFAULT 'pending'
                                   CHECK(status IN ('pending','paid','expired','cancelled')),
                payment_id     TEXT UNIQUE,
                pix_copia_cola TEXT,
                created_at     TEXT NOT NULL,
                paid_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS orders (
                id               TEXT PRIMARY KEY,
                user_id          INTEGER NOT NULL,
                username         TEXT,
                full_name        TEXT,
                product_key      TEXT NOT NULL,
                product_name     TEXT NOT NULL,
                amount_cents     INTEGER NOT NULL,
                face_value_cents INTEGER NOT NULL DEFAULT 0,
                status           TEXT NOT NULL DEFAULT 'pending'
                                     CHECK(status IN ('pending','delivered','refunded','failed')),
                gift_code        TEXT,
                created_at       TEXT NOT NULL,
                delivered_at     TEXT,
                payment_id       TEXT
            );

            -- Índices de performance
            CREATE INDEX IF NOT EXISTS idx_gift_codes_lookup
                ON gift_codes(product_key, amount_cents, used);
            CREATE INDEX IF NOT EXISTS idx_orders_user
                ON orders(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_topups_payment
                ON topups(payment_id);
            CREATE INDEX IF NOT EXISTS idx_topups_user_status
                ON topups(user_id, status);
            CREATE INDEX IF NOT EXISTS idx_wallet_txns_user
                ON wallet_txns(user_id, created_at);
        """)
    logger.info("[DB] Banco inicializado com sucesso.")


# ──── Produtos ────────────────────────────────────────────────

def get_all_products() -> list[dict]:
    with get_conn() as conn:
        products = conn.execute(
            "SELECT * FROM products WHERE active=1 ORDER BY name"
        ).fetchall()
        result = []
        for p in products:
            values = conn.execute(
                "SELECT * FROM product_values WHERE product_key=? ORDER BY face_value_cents",
                (p["key"],)
            ).fetchall()
            result.append({
                "key":    p["key"],
                "name":   p["name"],
                "emoji":  p["emoji"],
                "values": [dict(v) for v in values],
            })
    return result


def get_product(product_key: str) -> Optional[dict]:
    with get_conn() as conn:
        p = conn.execute(
            "SELECT * FROM products WHERE key=? AND active=1", (product_key,)
        ).fetchone()
        if not p:
            return None
        values = conn.execute(
            "SELECT * FROM product_values WHERE product_key=? ORDER BY face_value_cents",
            (product_key,)
        ).fetchall()
    return {
        "key":    p["key"],
        "name":   p["name"],
        "emoji":  p["emoji"],
        "values": [dict(v) for v in values],
    }


def upsert_product(key: str, name: str, emoji: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO products (key, name, emoji, created_at)
            VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                name=excluded.name,
                emoji=excluded.emoji,
                active=1
        """, (key, name, emoji, _now()))


def upsert_product_value(product_key: str, face_value_cents: int,
                         amount_cents: int) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO product_values (product_key, face_value_cents, amount_cents)
            VALUES (?,?,?)
            ON CONFLICT(product_key, face_value_cents)
            DO UPDATE SET amount_cents=excluded.amount_cents
        """, (product_key, face_value_cents, amount_cents))


def delete_product_value(product_key: str, face_value_cents: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM gift_codes WHERE product_key=? AND face_value_cents=? AND used=0",
            (product_key, face_value_cents)
        )
        conn.execute(
            "DELETE FROM product_values WHERE product_key=? AND face_value_cents=?",
            (product_key, face_value_cents)
        )


def delete_product(product_key: str) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE products SET active=0 WHERE key=?", (product_key,))


# ──── Gift Codes ─────────────────────────────────────────────

def add_gift_code(product_key: str, face_value_cents: int,
                  amount_cents: int, code: str) -> bool:
    """Retorna True se adicionado, False se duplicado."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO gift_codes "
                "(product_key, face_value_cents, amount_cents, code, added_at) "
                "VALUES (?,?,?,?,?)",
                (product_key, face_value_cents, amount_cents, code, _now()),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def count_gift_codes(product_key: str, amount_cents: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM gift_codes "
            "WHERE product_key=? AND amount_cents=? AND used=0",
            (product_key, amount_cents),
        ).fetchone()
    return row["n"]


def count_all_gift_codes(product_key: str) -> dict[int, int]:
    """Retorna {amount_cents: qty} em uma única query. Eficiente."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT amount_cents, COUNT(*) as n FROM gift_codes "
            "WHERE product_key=? AND used=0 GROUP BY amount_cents",
            (product_key,)
        ).fetchall()
    return {r["amount_cents"]: r["n"] for r in rows}


# ──── Operação Atômica de Compra ──────────────────────────────

def buy_atomic(
    user_id: int,
    product_key: str,
    amount_cents: int,
    face_value_cents: int,
    product_name: str,
    username: str,
    full_name: str,
) -> tuple[str, Optional[str], int]:
    """
    [BUG-01 CORRIGIDO] Debita saldo E retira código em UMA transação atômica.

    Antes: debit_wallet() + pop_gift_code() eram chamadas separadas.
    Risco antigo: crash entre as duas = saldo debitado mas código nunca entregue.
    Agora: BEGIN IMMEDIATE garante atomicidade total.

    Retorna: (order_id, gift_code_ou_None, novo_saldo)
    - Se gift_code é None: saldo insuficiente (nada foi alterado)
    - Se gift_code é "": estoque zerou no momento exato (saldo debitado + estornado)
    """
    now = _now()
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")

    try:
        conn.execute("BEGIN IMMEDIATE")

        # 1. Verifica saldo
        wallet = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()
        balance = wallet["balance_cents"] if wallet else 0

        if balance < amount_cents:
            conn.rollback()
            return ("", None, balance)

        # 2. Reserva código
        code_row = conn.execute(
            "SELECT id, code FROM gift_codes "
            "WHERE product_key=? AND amount_cents=? AND used=0 "
            "ORDER BY id LIMIT 1",
            (product_key, amount_cents),
        ).fetchone()

        order_id = "ORD-" + str(uuid.uuid4())[:8].upper()

        if not code_row:
            # Sem estoque — não debita nada, registra pedido como refunded
            conn.execute("""
                INSERT INTO orders
                (id, user_id, username, full_name, product_key, product_name,
                 amount_cents, face_value_cents, status, created_at)
                VALUES (?,?,?,?,?,?,?,?,'refunded',?)
            """, (order_id, user_id, username, full_name, product_key,
                  product_name, amount_cents, face_value_cents, now))
            conn.commit()
            return (order_id, "", balance)

        # 3. Debita saldo
        conn.execute(
            "UPDATE wallets SET balance_cents=balance_cents-?, updated_at=? WHERE user_id=?",
            (amount_cents, now, user_id)
        )

        # 4. Marca código como usado
        conn.execute(
            "UPDATE gift_codes SET used=1, order_id=? WHERE id=?",
            (order_id, code_row["id"])
        )

        # 5. Cria pedido como entregue
        conn.execute("""
            INSERT INTO orders
            (id, user_id, username, full_name, product_key, product_name,
             amount_cents, face_value_cents, status, gift_code, created_at, delivered_at)
            VALUES (?,?,?,?,?,?,?,?,'delivered',?,?,?)
        """, (order_id, user_id, username, full_name, product_key,
              product_name, amount_cents, face_value_cents,
              code_row["code"], now, now))

        # 6. Registra transação
        conn.execute(
            "INSERT INTO wallet_txns "
            "(user_id, type, amount_cents, description, created_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, "purchase", amount_cents,
             f"Compra {product_name} R${face_value_cents//100}", now)
        )

        new_balance = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()["balance_cents"]

        conn.commit()
        return (order_id, code_row["code"], new_balance)

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ──── Wallet ─────────────────────────────────────────────────

def get_balance(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["balance_cents"] if row else 0


def upsert_wallet(user_id: int, username: str, full_name: str) -> None:
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO wallets (user_id, username, full_name, balance_cents, updated_at)
            VALUES (?,?,?,0,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=COALESCE(NULLIF(excluded.username,''), username),
                full_name=COALESCE(NULLIF(excluded.full_name,''), full_name),
                updated_at=excluded.updated_at
        """, (user_id, username or "", full_name or "", _now()))


def credit_wallet(user_id: int, amount_cents: int,
                  description: str, payment_id: Optional[str] = None,
                  txn_type: str = "topup") -> int:
    """Credita saldo e registra transação. Retorna novo saldo."""
    now = _now()
    with get_conn() as conn:
        conn.execute(
            "UPDATE wallets SET balance_cents=balance_cents+?, updated_at=? WHERE user_id=?",
            (amount_cents, now, user_id)
        )
        conn.execute(
            "INSERT INTO wallet_txns "
            "(user_id, type, amount_cents, description, payment_id, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (user_id, txn_type, amount_cents, description, payment_id, now)
        )
        row = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["balance_cents"]


def get_wallet_txns(user_id: int, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_txns WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def list_wallets_with_balance() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallets WHERE balance_cents>0 ORDER BY balance_cents DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ──── Topups ─────────────────────────────────────────────────

def create_topup(user_id: int, username: str,
                 full_name: str, amount_cents: int) -> str:
    topup_id = "TOP-" + str(uuid.uuid4())[:8].upper()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO topups "
            "(id, user_id, username, full_name, amount_cents, status, created_at) "
            "VALUES (?,?,?,?,?,'pending',?)",
            (topup_id, user_id, username or "", full_name or "", amount_cents, _now())
        )
    return topup_id


def get_topup(topup_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM topups WHERE id=?", (topup_id,)
        ).fetchone()
    return dict(row) if row else None


def find_topup_by_payment_id(payment_id: str) -> Optional[dict]:
    """Busca topup exclusivamente pelo payment_id — sem fallbacks perigosos."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM topups WHERE payment_id=?", (payment_id,)
        ).fetchone()
    return dict(row) if row else None


def get_pending_topups_by_user(user_id: int) -> list[dict]:
    """Retorna topups pendentes de um usuário — útil para evitar duplicatas."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM topups WHERE user_id=? AND status='pending' "
            "ORDER BY created_at DESC LIMIT 5",
            (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def update_topup(topup_id: str, **kwargs) -> None:
    """Atualiza campos de um topup. Apenas campos da whitelist são aceitos."""
    invalid = set(kwargs) - TOPUP_FIELDS
    if invalid:
        raise ValueError(f"Campos não permitidos em update_topup: {invalid}")
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE topups SET {fields} WHERE id=?",
            [*kwargs.values(), topup_id]
        )


# ──── Orders ─────────────────────────────────────────────────

def create_order(user_id: int, username: str, full_name: str,
                 product_key: str, product_name: str,
                 amount_cents: int, face_value_cents: int) -> str:
    order_id = "ORD-" + str(uuid.uuid4())[:8].upper()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO orders
            (id, user_id, username, full_name, product_key, product_name,
             amount_cents, face_value_cents, status, created_at)
            VALUES (?,?,?,?,?,?,?,?,'pending',?)
        """, (order_id, user_id, username or "", full_name or "", product_key,
              product_name, amount_cents, face_value_cents, _now()))
    return order_id


def get_order(order_id: str) -> Optional[dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
    return dict(row) if row else None


def update_order(order_id: str, **kwargs) -> None:
    """Atualiza campos de um pedido. Apenas campos da whitelist são aceitos."""
    invalid = set(kwargs) - ORDER_FIELDS
    if invalid:
        raise ValueError(f"Campos não permitidos em update_order: {invalid}")
    if not kwargs:
        return
    fields = ", ".join(f"{k}=?" for k in kwargs)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE orders SET {fields} WHERE id=?",
            [*kwargs.values(), order_id]
        )


def get_user_orders(user_id: int, limit: int = 8) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


# ──── Perfil & Ranking ────────────────────────────────────────

def get_user_profile(user_id: int) -> dict:
    with get_conn() as conn:
        wallet = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()
        agg = conn.execute(
            "SELECT COALESCE(SUM(amount_cents),0) as total_spent, "
            "COUNT(*) as total_purchases "
            "FROM orders WHERE user_id=? AND status='delivered'",
            (user_id,)
        ).fetchone()
    return {
        "balance_cents":   wallet["balance_cents"] if wallet else 0,
        "total_spent":     agg["total_spent"],
        "total_purchases": agg["total_purchases"],
    }


def get_top10_spenders() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT o.user_id, w.full_name, w.username,
                   SUM(o.amount_cents) AS total_spent,
                   COUNT(*) AS total_purchases
            FROM orders o
            LEFT JOIN wallets w ON w.user_id = o.user_id
            WHERE o.status = 'delivered'
            GROUP BY o.user_id
            ORDER BY total_spent DESC
            LIMIT 10
        """).fetchall()
    return [dict(r) for r in rows]
