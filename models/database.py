import sqlite3
import uuid
from datetime import datetime
from config import DATABASE_PATH


def get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            product_key TEXT NOT NULL,
            product_name TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            face_value_cents INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            gift_code TEXT,
            created_at TEXT NOT NULL,
            delivered_at TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS gift_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL,
            amount_cents INTEGER NOT NULL,
            code TEXT NOT NULL UNIQUE,
            used INTEGER NOT NULL DEFAULT 0,
            order_id TEXT,
            added_at TEXT NOT NULL
        )
    """)

    # Carteira de saldo dos clientes
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance_cents INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )
    """)

    # Histórico de transações da carteira
    c.execute("""
        CREATE TABLE IF NOT EXISTS wallet_txns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type TEXT NOT NULL,        -- 'topup' | 'purchase' | 'refund'
            amount_cents INTEGER NOT NULL,
            description TEXT,
            payment_id TEXT,
            created_at TEXT NOT NULL
        )
    """)

    # Recargas PIX pendentes
    c.execute("""
        CREATE TABLE IF NOT EXISTS topups (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            username TEXT,
            full_name TEXT,
            amount_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            payment_id TEXT,
            pix_copia_cola TEXT,
            created_at TEXT NOT NULL,
            paid_at TEXT
        )
    """)

    conn.commit()
    conn.close()
    print("[DB] Banco inicializado com sucesso.")


# ──── Wallet ─────────────────────────────────────────────────

def get_balance(user_id: int) -> int:
    conn = get_conn()
    row = conn.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["balance_cents"] if row else 0


def upsert_wallet(user_id: int, username: str, full_name: str):
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("""
        INSERT INTO wallets (user_id, username, full_name, balance_cents, updated_at)
        VALUES (?, ?, ?, 0, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
            full_name=excluded.full_name, updated_at=excluded.updated_at
    """, (user_id, username, full_name, now))
    conn.commit()
    conn.close()


def credit_wallet(user_id: int, amount_cents: int, description: str, payment_id: str = None) -> int:
    """Adiciona saldo. Retorna novo saldo."""
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("""
        UPDATE wallets SET balance_cents = balance_cents + ?, updated_at=? WHERE user_id=?
    """, (amount_cents, now, user_id))
    conn.execute("""
        INSERT INTO wallet_txns (user_id, type, amount_cents, description, payment_id, created_at)
        VALUES (?, 'topup', ?, ?, ?, ?)
    """, (user_id, amount_cents, description, payment_id, now))
    conn.commit()
    row = conn.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["balance_cents"]


def debit_wallet(user_id: int, amount_cents: int, description: str) -> tuple[bool, int]:
    """Debita saldo se suficiente. Retorna (sucesso, saldo_restante)."""
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    row = conn.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    balance = row["balance_cents"] if row else 0
    if balance < amount_cents:
        conn.close()
        return False, balance
    conn.execute("""
        UPDATE wallets SET balance_cents = balance_cents - ?, updated_at=? WHERE user_id=?
    """, (amount_cents, now, user_id))
    conn.execute("""
        INSERT INTO wallet_txns (user_id, type, amount_cents, description, created_at)
        VALUES (?, 'purchase', ?, ?, ?)
    """, (user_id, amount_cents, description, now))
    conn.commit()
    new_bal = conn.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)).fetchone()["balance_cents"]
    conn.close()
    return True, new_bal


def refund_wallet(user_id: int, amount_cents: int, description: str, admin_id: int) -> int:
    """Estorna saldo para o usuário. Retorna novo saldo."""
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("""
        UPDATE wallets SET balance_cents = balance_cents + ?, updated_at=? WHERE user_id=?
    """, (amount_cents, now, user_id))
    conn.execute("""
        INSERT INTO wallet_txns (user_id, type, amount_cents, description, created_at)
        VALUES (?, 'refund', ?, ?, ?)
    """, (user_id, amount_cents, f"Estorno por admin {admin_id}: {description}", now))
    conn.commit()
    row = conn.execute("SELECT balance_cents FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    conn.close()
    return row["balance_cents"]


def get_wallet_txns(user_id: int, limit: int = 10):
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM wallet_txns WHERE user_id=? ORDER BY created_at DESC LIMIT ?
    """, (user_id, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_wallets_with_balance():
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM wallets WHERE balance_cents > 0 ORDER BY balance_cents DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ──── Topups ─────────────────────────────────────────────────

def create_topup(user_id, username, full_name, amount_cents):
    topup_id = "TOP-" + str(uuid.uuid4())[:8].upper()
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("""
        INSERT INTO topups (id, user_id, username, full_name, amount_cents, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', ?)
    """, (topup_id, user_id, username, full_name, amount_cents, now))
    conn.commit()
    conn.close()
    return topup_id


def get_topup(topup_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_topup(topup_id, **kwargs):
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [topup_id]
    conn = get_conn()
    conn.execute(f"UPDATE topups SET {fields} WHERE id=?", values)
    conn.commit()
    conn.close()


# ──── Orders ─────────────────────────────────────────────────

def create_order(user_id, username, full_name, product_key, product_name, amount_cents, face_value_cents):
    order_id = "ORD-" + str(uuid.uuid4())[:8].upper()
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    conn.execute("""
        INSERT INTO orders
        (id, user_id, username, full_name, product_key, product_name, amount_cents, face_value_cents, status, created_at)
        VALUES (?,?,?,?,?,?,?,?,'pending',?)
    """, (order_id, user_id, username, full_name, product_key, product_name, amount_cents, face_value_cents, now))
    conn.commit()
    conn.close()
    return order_id


def get_order(order_id):
    conn = get_conn()
    row = conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_order(order_id, **kwargs):
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [order_id]
    conn = get_conn()
    conn.execute(f"UPDATE orders SET {fields} WHERE id=?", values)
    conn.commit()
    conn.close()


def get_user_orders(user_id, limit=8):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ──── Gift Codes ─────────────────────────────────────────────

def add_gift_code(product_key, amount_cents, code):
    now = datetime.utcnow().isoformat()
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO gift_codes (product_key, amount_cents, code, added_at) VALUES (?,?,?,?)",
            (product_key, amount_cents, code, now),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()


def pop_gift_code(product_key, amount_cents):
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM gift_codes WHERE product_key=? AND amount_cents=? AND used=0 LIMIT 1",
        (product_key, amount_cents),
    ).fetchone()
    if row:
        conn.execute("UPDATE gift_codes SET used=1 WHERE id=?", (row["id"],))
        conn.commit()
        conn.close()
        return row["code"]
    conn.close()
    return None


def count_gift_codes(product_key, amount_cents):
    conn = get_conn()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM gift_codes WHERE product_key=? AND amount_cents=? AND used=0",
        (product_key, amount_cents),
    ).fetchone()
    conn.close()
    return row["n"]


# ──── Ranking & Perfil ───────────────────────────────────────

def get_user_profile(user_id: int) -> dict:
    """Retorna total gasto, total de compras e saldo do usuário."""
    conn = get_conn()
    wallet = conn.execute("SELECT * FROM wallets WHERE user_id=?", (user_id,)).fetchone()
    spent = conn.execute(
        "SELECT COALESCE(SUM(amount_cents),0) as total FROM orders WHERE user_id=? AND status='delivered'",
        (user_id,)
    ).fetchone()
    count = conn.execute(
        "SELECT COUNT(*) as n FROM orders WHERE user_id=? AND status='delivered'",
        (user_id,)
    ).fetchone()
    conn.close()
    return {
        "balance_cents":   wallet["balance_cents"] if wallet else 0,
        "username":        wallet["username"] if wallet else "",
        "full_name":       wallet["full_name"] if wallet else "",
        "total_spent":     spent["total"],
        "total_purchases": count["n"],
    }


def get_top10_spenders() -> list:
    """Top 10 usuários que mais gastaram (apenas pedidos entregues)."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT o.user_id,
               w.full_name,
               w.username,
               SUM(o.amount_cents) as total_spent,
               COUNT(*) as total_purchases
        FROM orders o
        LEFT JOIN wallets w ON w.user_id = o.user_id
        WHERE o.status = 'delivered'
        GROUP BY o.user_id
        ORDER BY total_spent DESC
        LIMIT 10
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]
