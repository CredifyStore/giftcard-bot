"""
Camada de acesso ao banco de dados (SQLite).

Correções aplicadas:
- WAL mode habilitado para suporte a leituras concorrentes
- pop_gift_code() e debit_wallet() agora são atômicos (sem race condition)
- update_order/update_topup têm whitelist de campos permitidos
- Todas as conexões usam context manager para evitar leaks
- Linha morta removida em delete_product_value
"""
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DATABASE_PATH
from utils import ORDER_FIELDS, TOPUP_FIELDS


# ──── Conexão ─────────────────────────────────────────────────

@contextmanager
def get_conn():
    """Context manager que garante fechamento mesmo em caso de exceção."""
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL permite leituras simultâneas sem bloquear escritas
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


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
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                full_name    TEXT,
                balance_cents INTEGER NOT NULL DEFAULT 0 CHECK(balance_cents >= 0),
                updated_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wallet_txns (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                type         TEXT NOT NULL CHECK(type IN ('topup','purchase','refund')),
                amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
                description  TEXT,
                payment_id   TEXT,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS topups (
                id            TEXT PRIMARY KEY,
                user_id       INTEGER NOT NULL,
                username      TEXT,
                full_name     TEXT,
                amount_cents  INTEGER NOT NULL,
                status        TEXT NOT NULL DEFAULT 'pending',
                payment_id    TEXT UNIQUE,
                pix_copia_cola TEXT,
                created_at    TEXT NOT NULL,
                paid_at       TEXT
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
                status           TEXT NOT NULL DEFAULT 'pending',
                gift_code        TEXT,
                created_at       TEXT NOT NULL,
                delivered_at     TEXT,
                payment_id       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_gift_codes_lookup
                ON gift_codes(product_key, amount_cents, used);
            CREATE INDEX IF NOT EXISTS idx_orders_user
                ON orders(user_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_topups_payment
                ON topups(payment_id);
            CREATE INDEX IF NOT EXISTS idx_wallet_txns_user
                ON wallet_txns(user_id, created_at);
        """)
    print("[DB] Banco inicializado.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──── Produtos ────────────────────────────────────────────────

def get_all_products() -> list:
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


def get_product(product_key: str) -> dict | None:
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
    return {"key": p["key"], "name": p["name"], "emoji": p["emoji"],
            "values": [dict(v) for v in values]}


def upsert_product(key: str, name: str, emoji: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO products (key, name, emoji, created_at)
            VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET name=excluded.name,
                emoji=excluded.emoji, active=1
        """, (key, name, emoji, _now()))


def upsert_product_value(product_key: str, face_value_cents: int, amount_cents: int):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO product_values (product_key, face_value_cents, amount_cents)
            VALUES (?,?,?)
            ON CONFLICT(product_key, face_value_cents)
            DO UPDATE SET amount_cents=excluded.amount_cents
        """, (product_key, face_value_cents, amount_cents))


def delete_product_value(product_key: str, face_value_cents: int):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM gift_codes WHERE product_key=? AND face_value_cents=? AND used=0",
            (product_key, face_value_cents)
        )
        conn.execute(
            "DELETE FROM product_values WHERE product_key=? AND face_value_cents=?",
            (product_key, face_value_cents)
        )


def delete_product(product_key: str):
    with get_conn() as conn:
        conn.execute("UPDATE products SET active=0 WHERE key=?", (product_key,))


# ──── Gift Codes ─────────────────────────────────────────────

def add_gift_code(product_key: str, face_value_cents: int,
                  amount_cents: int, code: str) -> bool:
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


def pop_gift_code(product_key: str, amount_cents: int) -> str | None:
    """
    Reserva e retorna um código de forma ATÔMICA.
    Usa BEGIN IMMEDIATE para evitar race condition entre usuários simultâneos.
    """
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, code FROM gift_codes "
            "WHERE product_key=? AND amount_cents=? AND used=0 LIMIT 1",
            (product_key, amount_cents),
        ).fetchone()
        if not row:
            conn.rollback()
            return None
        conn.execute("UPDATE gift_codes SET used=1 WHERE id=?", (row["id"],))
        conn.commit()
        return row["code"]
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def count_gift_codes(product_key: str, amount_cents: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM gift_codes "
            "WHERE product_key=? AND amount_cents=? AND used=0",
            (product_key, amount_cents),
        ).fetchone()
    return row["n"]


def count_all_gift_codes(product_key: str) -> dict:
    """Retorna {amount_cents: qty} em uma única query."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT amount_cents, COUNT(*) as n FROM gift_codes "
            "WHERE product_key=? AND used=0 GROUP BY amount_cents",
            (product_key,)
        ).fetchall()
    return {r["amount_cents"]: r["n"] for r in rows}


# ──── Wallet ─────────────────────────────────────────────────

def get_balance(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["balance_cents"] if row else 0


def upsert_wallet(user_id: int, username: str, full_name: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO wallets (user_id, username, full_name, balance_cents, updated_at)
            VALUES (?,?,?,0,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                full_name=excluded.full_name,
                updated_at=excluded.updated_at
        """, (user_id, username, full_name, _now()))


def credit_wallet(user_id: int, amount_cents: int,
                  description: str, payment_id: str = None) -> int:
    """Credita saldo. Retorna novo saldo."""
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
            (user_id, "topup", amount_cents, description, payment_id, now)
        )
        row = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["balance_cents"]


def debit_wallet(user_id: int, amount_cents: int,
                 description: str) -> tuple[bool, int]:
    """
    Debita saldo de forma ATÔMICA.
    O UPDATE só acontece se o saldo for suficiente (WHERE balance_cents >= amount).
    Retorna (sucesso, saldo_atual).
    """
    now = _now()
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute("BEGIN IMMEDIATE")
        affected = conn.execute(
            "UPDATE wallets SET balance_cents=balance_cents-?, updated_at=? "
            "WHERE user_id=? AND balance_cents >= ?",
            (amount_cents, now, user_id, amount_cents)
        ).rowcount
        if affected == 0:
            balance = (conn.execute(
                "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
            ).fetchone() or {"balance_cents": 0})["balance_cents"]
            conn.rollback()
            return False, balance
        conn.execute(
            "INSERT INTO wallet_txns "
            "(user_id, type, amount_cents, description, created_at) "
            "VALUES (?,?,?,?,?)",
            (user_id, "purchase", amount_cents, description, now)
        )
        new_balance = conn.execute(
            "SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)
        ).fetchone()["balance_cents"]
        conn.commit()
        return True, new_balance
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_wallet_txns(user_id: int, limit: int = 10) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallet_txns WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]


def list_wallets_with_balance() -> list:
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
            (topup_id, user_id, username, full_name, amount_cents, _now())
        )
    return topup_id


def get_topup(topup_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM topups WHERE id=?", (topup_id,)
        ).fetchone()
    return dict(row) if row else None


def find_topup_by_payment_id(payment_id: str) -> dict | None:
    """Busca topup exclusivamente pelo payment_id — sem fallbacks perigosos."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM topups WHERE payment_id=?", (payment_id,)
        ).fetchone()
    return dict(row) if row else None


def update_topup(topup_id: str, **kwargs):
    """Atualiza campos de um topup. Apenas campos da whitelist são aceitos."""
    invalid = set(kwargs) - TOPUP_FIELDS
    if invalid:
        raise ValueError(f"Campos não permitidos em update_topup: {invalid}")
    fields = ", ".join(f"{k}=?" for k in kwargs)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE topups SET {fields} WHERE id=?",
            list(kwargs.values()) + [topup_id]
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
        """, (order_id, user_id, username, full_name, product_key,
              product_name, amount_cents, face_value_cents, _now()))
    return order_id


def get_order(order_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
    return dict(row) if row else None


def update_order(order_id: str, **kwargs):
    """Atualiza campos de um pedido. Apenas campos da whitelist são aceitos."""
    invalid = set(kwargs) - ORDER_FIELDS
    if invalid:
        raise ValueError(f"Campos não permitidos em update_order: {invalid}")
    fields = ", ".join(f"{k}=?" for k in kwargs)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE orders SET {fields} WHERE id=?",
            list(kwargs.values()) + [order_id]
        )


def get_user_orders(user_id: int, limit: int = 8) -> list:
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


def get_top10_spenders() -> list:
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
