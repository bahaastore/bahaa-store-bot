"""قاعدة البيانات (SQLite) — المستخدمون، الطلبات، الإيداعات، الإعدادات."""
import json
import time
from typing import Any

import aiosqlite

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          INTEGER PRIMARY KEY,           -- معرّف تيليجرام
    username    TEXT,
    first_name  TEXT,
    balance     REAL NOT NULL DEFAULT 0,       -- بالدولار
    spent       REAL NOT NULL DEFAULT 0,
    banned      INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id            TEXT PRIMARY KEY,            -- UUIDv4 يُرسل للمزوّد (order_uuid)
    num           INTEGER,                     -- رقم طلب قصير للعرض
    user_id       INTEGER NOT NULL,
    product_id    INTEGER NOT NULL,
    product_name  TEXT,
    qty           INTEGER NOT NULL,
    params        TEXT,                        -- JSON
    unit_price    REAL NOT NULL,               -- سعرنا للوحدة
    price         REAL NOT NULL,               -- ما دفعه العميل
    cost          REAL,                        -- ما دفعناه للمزوّد
    status        TEXT NOT NULL,               -- submitting | wait | accept | reject
    delivery      TEXT,                        -- JSON
    note          TEXT,
    balance_after REAL,
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_user ON orders(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE TABLE IF NOT EXISTS deposits (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    method      TEXT NOT NULL,
    amount      REAL NOT NULL,                 -- بعملة الطريقة
    currency    TEXT NOT NULL,                 -- USD | SYP
    amount_usd  REAL NOT NULL,                 -- ما يُضاف للرصيد
    tx_id       TEXT NOT NULL,
    proof       TEXT,                          -- مسار صورة الإيصال
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | approved | rejected
    admin_note  TEXT,
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL,
    UNIQUE(method, tx_id)
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    amount     REAL NOT NULL,
    reason     TEXT NOT NULL,
    ref        TEXT,
    created_at INTEGER NOT NULL
);
"""

DEFAULT_PAYMENT_METHODS = [
    {"key": "sham_cash", "label": "شام كاش", "icon": "🏦", "currency": "SYP", "enabled": True,
     "account": "ضع رقم حساب شام كاش هنا", "min": 10000,
     "instructions": "حوّل المبلغ إلى الحساب أعلاه ثم أدخل رقم العملية وأرفق صورة الإيصال."},
    {"key": "syriatel_cash", "label": "سيرياتيل كاش", "icon": "📱", "currency": "SYP", "enabled": True,
     "account": "ضع رقم سيرياتيل كاش هنا", "min": 10000,
     "instructions": "حوّل بالليرة السورية ثم أدخل رقم العملية للتأكيد."},
    {"key": "usdt", "label": "USDT (TRC20)", "icon": "💵", "currency": "USD", "enabled": True,
     "account": "ضع عنوان محفظة USDT هنا", "min": 5,
     "instructions": "أرسل على شبكة TRC20 فقط، ثم أدخل TXID الخاص بالتحويل."},
    {"key": "binance_pay", "label": "Binance Pay", "icon": "🟡", "currency": "USD", "enabled": True,
     "account": "ضع Binance Pay ID هنا", "min": 3,
     "instructions": "أرسل عبر Binance Pay ثم أدخل رقم الطلب (Order ID)."},
]

DEFAULT_SETTINGS = {
    "markup_pct": config.DEFAULT_MARKUP_PCT,
    "markup_overrides": {},           # {"product_id": pct}
    "syp_rate": config.DEFAULT_SYP_RATE,
    "support_url": config.SUPPORT_URL,
    "store_name": config.STORE_NAME,
    "maintenance": False,
    "payment_methods": DEFAULT_PAYMENT_METHODS,
    "announcement": "",
}

_db: aiosqlite.Connection | None = None


async def connect() -> aiosqlite.Connection:
    global _db
    if _db is None:
        _db = await aiosqlite.connect(config.DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.executescript(SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            await _db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (k, json.dumps(v, ensure_ascii=False)))
        await _db.commit()
    return _db


def now() -> int:
    return int(time.time())


# ── الإعدادات ────────────────────────────────────────────
async def get_setting(key: str) -> Any:
    db = await connect()
    async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
        row = await cur.fetchone()
    return json.loads(row["value"]) if row else DEFAULT_SETTINGS.get(key)


async def set_setting(key: str, value: Any) -> None:
    db = await connect()
    await db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))
    await db.commit()


# ── المستخدمون ───────────────────────────────────────────
async def upsert_user(uid: int, username: str | None, first_name: str | None) -> dict:
    db = await connect()
    await db.execute(
        "INSERT INTO users(id, username, first_name, created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
        (uid, username, first_name, now()))
    await db.commit()
    return await get_user(uid)


async def get_user(uid: int) -> dict | None:
    db = await connect()
    async with db.execute("SELECT * FROM users WHERE id=?", (uid,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def adjust_balance(uid: int, amount: float, reason: str, ref: str | None = None, require_funds: bool = False) -> float | None:
    """يعدّل الرصيد بشكل ذرّي. يعيد الرصيد الجديد أو None عند عدم كفاية الرصيد."""
    db = await connect()
    if require_funds and amount < 0:
        cur = await db.execute("UPDATE users SET balance = ROUND(balance + ?, 4) WHERE id=? AND balance >= ?", (amount, uid, -amount - 1e-9))
    else:
        cur = await db.execute("UPDATE users SET balance = ROUND(balance + ?, 4) WHERE id=?", (amount, uid))
    if cur.rowcount == 0:
        await db.rollback()
        return None
    await db.execute("INSERT INTO ledger(user_id, amount, reason, ref, created_at) VALUES(?,?,?,?,?)", (uid, amount, reason, ref, now()))
    await db.commit()
    u = await get_user(uid)
    return u["balance"] if u else None


async def set_banned(uid: int, banned: bool) -> None:
    db = await connect()
    await db.execute("UPDATE users SET banned=? WHERE id=?", (1 if banned else 0, uid))
    await db.commit()


async def all_user_ids() -> list[int]:
    db = await connect()
    async with db.execute("SELECT id FROM users WHERE banned=0") as cur:
        return [r["id"] for r in await cur.fetchall()]


# ── الطلبات ──────────────────────────────────────────────
async def create_order(o: dict) -> None:
    db = await connect()
    async with db.execute("SELECT COALESCE(MAX(num), 1000) + 1 AS n FROM orders") as cur:
        o["num"] = (await cur.fetchone())["n"]
    o.setdefault("created_at", now())
    o.setdefault("updated_at", now())
    cols = ",".join(o.keys())
    await db.execute(f"INSERT INTO orders({cols}) VALUES({','.join('?' * len(o))})", tuple(o.values()))
    await db.commit()


async def update_order(oid: str, **fields) -> None:
    db = await connect()
    fields["updated_at"] = now()
    sets = ",".join(f"{k}=?" for k in fields)
    await db.execute(f"UPDATE orders SET {sets} WHERE id=?", (*fields.values(), oid))
    await db.commit()


async def get_order(oid: str) -> dict | None:
    db = await connect()
    async with db.execute("SELECT * FROM orders WHERE id=?", (oid,)) as cur:
        row = await cur.fetchone()
    return _order(row) if row else None


def _order(row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d["params"] or "{}")
    d["delivery"] = json.loads(d["delivery"]) if d.get("delivery") else None
    return d


async def user_orders(uid: int, limit: int = 30, offset: int = 0) -> list[dict]:
    db = await connect()
    async with db.execute("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?", (uid, limit, offset)) as cur:
        return [_order(r) for r in await cur.fetchall()]


async def orders_by_status(*statuses: str) -> list[dict]:
    db = await connect()
    q = f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at"
    async with db.execute(q, statuses) as cur:
        return [_order(r) for r in await cur.fetchall()]


# ── الإيداعات ────────────────────────────────────────────
async def create_deposit(d: dict) -> int:
    db = await connect()
    d.setdefault("created_at", now())
    d.setdefault("updated_at", now())
    cols = ",".join(d.keys())
    cur = await db.execute(f"INSERT INTO deposits({cols}) VALUES({','.join('?' * len(d))})", tuple(d.values()))
    await db.commit()
    return cur.lastrowid


async def get_deposit(did: int) -> dict | None:
    db = await connect()
    async with db.execute("SELECT * FROM deposits WHERE id=?", (did,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def decide_deposit(did: int, status: str, note: str | None = None, amount_usd: float | None = None) -> bool:
    """يغيّر حالة الإيداع من pending فقط (يمنع القبول المزدوج)."""
    db = await connect()
    if amount_usd is not None:
        cur = await db.execute("UPDATE deposits SET status=?, admin_note=?, amount_usd=?, updated_at=? WHERE id=? AND status='pending'",
                               (status, note, amount_usd, now(), did))
    else:
        cur = await db.execute("UPDATE deposits SET status=?, admin_note=?, updated_at=? WHERE id=? AND status='pending'",
                               (status, note, now(), did))
    await db.commit()
    return cur.rowcount == 1


async def user_deposits(uid: int, limit: int = 30) -> list[dict]:
    db = await connect()
    async with db.execute("SELECT * FROM deposits WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid, limit)) as cur:
        return [dict(r) for r in await cur.fetchall()]


async def pending_deposits() -> list[dict]:
    db = await connect()
    async with db.execute("SELECT * FROM deposits WHERE status='pending' ORDER BY created_at") as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── الإحصائيات ───────────────────────────────────────────
async def stats(since: int = 0) -> dict:
    db = await connect()
    out = {}
    async with db.execute("SELECT COUNT(*) c, COALESCE(SUM(balance),0) b FROM users") as cur:
        r = await cur.fetchone(); out["users"] = r["c"]; out["user_balances"] = round(r["b"], 2)
    async with db.execute("SELECT COUNT(*) c, COALESCE(SUM(price),0) s, COALESCE(SUM(price - COALESCE(cost,0)),0) p "
                          "FROM orders WHERE status='accept' AND created_at>=?", (since,)) as cur:
        r = await cur.fetchone(); out["orders"] = r["c"]; out["sales"] = round(r["s"], 2); out["profit"] = round(r["p"], 2)
    async with db.execute("SELECT COUNT(*) c FROM orders WHERE status IN ('wait','submitting')") as cur:
        out["orders_pending"] = (await cur.fetchone())["c"]
    async with db.execute("SELECT COUNT(*) c FROM deposits WHERE status='pending'") as cur:
        out["deposits_pending"] = (await cur.fetchone())["c"]
    async with db.execute("SELECT COALESCE(SUM(amount_usd),0) s FROM deposits WHERE status='approved' AND created_at>=?", (since,)) as cur:
        out["deposits"] = round((await cur.fetchone())["s"], 2)
    return out
