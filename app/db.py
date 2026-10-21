"""قاعدة البيانات (SQLite محلياً / PostgreSQL سحابياً) — المستخدمون، الطلبات، الإيداعات، الإعدادات."""
import asyncio
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
    qty           REAL NOT NULL,
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

import os as _os


def _pm(key, label, icon, currency, env, minimum, instructions):
    account = _os.getenv(env, "")
    return {"key": key, "label": label, "icon": icon, "currency": currency, "enabled": bool(account),
            "account": account or "لم يُحدَّد بعد", "min": minimum, "instructions": instructions}


# الحسابات تُقرأ من متغيرات البيئة عند أول تشغيل، ويمكن تعديلها لاحقاً من البوت بـ /setpay و /togglepay
DEFAULT_PAYMENT_METHODS = [
    _pm("sham_cash", "شام كاش (ليرة)", "🏦", "SYP", "PAY_SHAM_CASH", 100,
        "حوّل المبلغ بالليرة السورية الجديدة إلى المحفظة أعلاه، ثم أدخل رقم العملية وأرفق صورة الإيصال."),
    _pm("sham_cash_usd", "شام كاش (دولار)", "💵", "USD", "PAY_SHAM_CASH", 1,
        "حوّل المبلغ بالدولار إلى المحفظة أعلاه، ثم أدخل رقم العملية وأرفق صورة الإيصال."),
    _pm("syriatel_cash", "سيرياتيل كاش", "📱", "SYP", "PAY_SYRIATEL_CASH", 100,
        "حوّل بالليرة السورية الجديدة ثم أدخل رقم العملية للتأكيد."),
    _pm("usdt", "USDT (TRC20)", "🪙", "USD", "PAY_USDT_TRC20", 5,
        "أرسل على شبكة TRC20 فقط، ثم أدخل TXID الخاص بالتحويل."),
    _pm("binance_pay", "Binance Pay", "🟡", "USD", "PAY_BINANCE_ID", 2,
        "أرسل عبر Binance Pay ثم أدخل رقم الطلب (Order ID)."),
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

class _DB:
    """طبقة موحّدة: SQLite محلياً، أو PostgreSQL عند وجود DATABASE_URL (للاستضافة السحابية)."""

    def __init__(self):
        self.pg = bool(config.DATABASE_URL)
        self.conn = None
        self.pool = None
        self.finalize_lock = asyncio.Lock()

    async def open(self):
        if self.pg:
            import asyncpg
            self.pool = await asyncpg.create_pool(config.DATABASE_URL, min_size=1, max_size=5, command_timeout=30)
            schema = (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
                      .replace("INTEGER", "BIGINT").replace("REAL", "DOUBLE PRECISION"))
            async with self.pool.acquire() as c:
                for stmt in [x for x in schema.split(";") if x.strip()]:
                    await c.execute(stmt)
        else:
            self.conn = await aiosqlite.connect(config.DB_PATH)
            self.conn.row_factory = aiosqlite.Row
            await self.conn.execute("PRAGMA journal_mode=WAL")
            await self.conn.executescript(SCHEMA)
            await self.conn.commit()

    def _sql(self, q: str) -> str:
        if not self.pg:
            return q
        n = 0
        out = []
        for ch in q:
            if ch == "?":
                n += 1
                out.append(f"${n}")
            else:
                out.append(ch)
        return "".join(out)

    async def execute(self, q: str, args=()) -> int:
        """ينفّذ ويعيد عدد الصفوف المتأثرة."""
        if self.pg:
            async with self.pool.acquire() as c:
                res = await c.execute(self._sql(q), *args)
            try:
                return int(res.split()[-1])
            except (ValueError, IndexError):
                return 0
        cur = await self.conn.execute(q, args)
        await self.conn.commit()
        return cur.rowcount

    async def insert_id(self, q: str, args=()) -> int:
        if self.pg:
            async with self.pool.acquire() as c:
                return await c.fetchval(self._sql(q) + " RETURNING id", *args)
        cur = await self.conn.execute(q, args)
        await self.conn.commit()
        return cur.lastrowid

    async def one(self, q: str, args=()) -> dict | None:
        if self.pg:
            async with self.pool.acquire() as c:
                r = await c.fetchrow(self._sql(q), *args)
            return dict(r) if r else None
        async with self.conn.execute(q, args) as cur:
            r = await cur.fetchone()
        return dict(r) if r else None

    async def all(self, q: str, args=()) -> list[dict]:
        if self.pg:
            async with self.pool.acquire() as c:
                return [dict(r) for r in await c.fetch(self._sql(q), *args)]
        async with self.conn.execute(q, args) as cur:
            return [dict(r) for r in await cur.fetchall()]


_db: _DB | None = None
_open_lock = None


async def connect() -> _DB:
    global _db, _open_lock
    import asyncio
    if _db is not None:
        return _db
    if _open_lock is None:
        _open_lock = asyncio.Lock()
    async with _open_lock:
        if _db is None:
            d = _DB()
            await d.open()
            for k, v in DEFAULT_SETTINGS.items():
                await d.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO NOTHING",
                                (k, json.dumps(v, ensure_ascii=False)))
            _db = d
    return _db


async def close() -> None:
    if _db and _db.pool:
        await _db.pool.close()
    elif _db and _db.conn:
        await _db.conn.close()


def now() -> int:
    return int(time.time())


# ── الإعدادات ────────────────────────────────────────────
async def get_setting(key: str) -> Any:
    db = await connect()
    row = await db.one("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(row["value"]) if row else DEFAULT_SETTINGS.get(key)


async def set_setting(key: str, value: Any) -> None:
    db = await connect()
    await db.execute("INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, json.dumps(value, ensure_ascii=False)))


# ── المستخدمون ───────────────────────────────────────────
async def upsert_user(uid: int, username: str | None, first_name: str | None) -> dict:
    db = await connect()
    await db.execute(
        "INSERT INTO users(id, username, first_name, created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
        (uid, username, first_name, now()))
    return await get_user(uid)


async def get_user(uid: int) -> dict | None:
    db = await connect()
    return await db.one("SELECT * FROM users WHERE id=?", (uid,))


async def adjust_balance(uid: int, amount: float, reason: str, ref: str | None = None, require_funds: bool = False) -> float | None:
    """يعدّل الرصيد بشكل ذرّي. يعيد الرصيد الجديد أو None عند عدم كفاية الرصيد."""
    db = await connect()
    amount = float(amount)
    if require_funds and amount < 0:
        n = await db.execute("UPDATE users SET balance = ROUND(CAST(balance + ? AS NUMERIC), 6) WHERE id=? AND balance >= ?",
                             (amount, uid, -amount - 1e-9))
    else:
        n = await db.execute("UPDATE users SET balance = ROUND(CAST(balance + ? AS NUMERIC), 6) WHERE id=?", (amount, uid))
    if n == 0:
        return None
    await db.execute("INSERT INTO ledger(user_id, amount, reason, ref, created_at) VALUES(?,?,?,?,?)", (uid, amount, reason, ref, now()))
    u = await get_user(uid)
    return float(u["balance"]) if u else None


async def add_spent(uid: int, amount: float) -> None:
    db = await connect()
    await db.execute("UPDATE users SET spent = ROUND(CAST(spent + ? AS NUMERIC), 6) WHERE id=?", (float(amount), uid))


async def set_banned(uid: int, banned: bool) -> None:
    db = await connect()
    await db.execute("UPDATE users SET banned=? WHERE id=?", (1 if banned else 0, uid))


async def all_user_ids() -> list[int]:
    db = await connect()
    return [r["id"] for r in await db.all("SELECT id FROM users WHERE banned=0")]


# ── الطلبات ──────────────────────────────────────────────
async def create_order(o: dict) -> None:
    db = await connect()
    o["num"] = (await db.one("SELECT COALESCE(MAX(num), 1000) + 1 AS n FROM orders"))["n"]
    o.setdefault("created_at", now())
    o.setdefault("updated_at", now())
    cols = ",".join(o.keys())
    await db.execute(f"INSERT INTO orders({cols}) VALUES({','.join('?' * len(o))})", tuple(o.values()))


async def update_order(oid: str, **fields) -> None:
    db = await connect()
    fields["updated_at"] = now()
    sets = ",".join(f"{k}=?" for k in fields)
    await db.execute(f"UPDATE orders SET {sets} WHERE id=?", (*fields.values(), oid))


async def finalize_order(oid: str, status: str, delivery: str | None = None,
                         note: str | None = None) -> dict | None:
    """Atomically make a pending order terminal and apply its financial effects once.

    Returns the updated order plus refunded balance for a first transition, or None
    when another worker already finalized it (or it is not in a pending state).
    """
    if status not in {"accept", "reject"}:
        raise ValueError("terminal status must be accept or reject")
    d = await connect()
    timestamp = now()
    if d.pg:
        async with d.pool.acquire() as c:
            async with c.transaction():
                row = await c.fetchrow(
                    "UPDATE orders SET status=$1, delivery=$2, note=$3, updated_at=$4 "
                    "WHERE id=$5 AND status IN ('submitting','wait') RETURNING *",
                    status, delivery, note, timestamp, oid)
                if row is None:
                    return None
                order = dict(row)
                balance = None
                if status == "reject":
                    balance = await c.fetchval(
                        "UPDATE users SET balance=ROUND(CAST(balance + $1 AS NUMERIC), 6) "
                        "WHERE id=$2 RETURNING balance", float(order["price"]), order["user_id"])
                    if balance is None:
                        raise RuntimeError("user missing while refunding order")
                    await c.execute(
                        "INSERT INTO ledger(user_id,amount,reason,ref,created_at) VALUES($1,$2,'refund',$3,$4)",
                        order["user_id"], float(order["price"]), oid, timestamp)
                else:
                    await c.execute(
                        "UPDATE users SET spent=ROUND(CAST(spent + $1 AS NUMERIC),6) WHERE id=$2",
                        float(order["price"]), order["user_id"])
                order["params"] = json.loads(order["params"] or "{}")
                order["delivery"] = json.loads(order["delivery"]) if order.get("delivery") else None
                q = float(order["qty"])
                order["qty"] = int(q) if q.is_integer() else q
                order["refund_balance"] = float(balance) if balance is not None else None
                return order

    async with d.finalize_lock:
        await d.conn.execute("BEGIN IMMEDIATE")
        try:
            cur = await d.conn.execute(
                "UPDATE orders SET status=?, delivery=?, note=?, updated_at=? "
                "WHERE id=? AND status IN ('submitting','wait')",
                (status, delivery, note, timestamp, oid))
            if cur.rowcount != 1:
                await d.conn.rollback()
                return None
            async with d.conn.execute("SELECT * FROM orders WHERE id=?", (oid,)) as cur:
                row = await cur.fetchone()
            order = _order(dict(row))
            balance = None
            if status == "reject":
                cur = await d.conn.execute(
                    "UPDATE users SET balance=ROUND(CAST(balance + ? AS NUMERIC),6) WHERE id=?",
                    (float(order["price"]), order["user_id"]))
                if cur.rowcount != 1:
                    raise RuntimeError("user missing while refunding order")
                async with d.conn.execute("SELECT balance FROM users WHERE id=?", (order["user_id"],)) as cur:
                    balance = float((await cur.fetchone())["balance"])
                await d.conn.execute(
                    "INSERT INTO ledger(user_id,amount,reason,ref,created_at) VALUES(?,?,'refund',?,?)",
                    (order["user_id"], float(order["price"]), oid, timestamp))
            else:
                await d.conn.execute(
                    "UPDATE users SET spent=ROUND(CAST(spent + ? AS NUMERIC),6) WHERE id=?",
                    (float(order["price"]), order["user_id"]))
            await d.conn.commit()
            order["refund_balance"] = balance
            return order
        except Exception:
            await d.conn.rollback()
            raise


async def get_order(oid: str) -> dict | None:
    db = await connect()
    row = await db.one("SELECT * FROM orders WHERE id=?", (oid,))
    return _order(row) if row else None


def _order(d: dict) -> dict:
    d = dict(d)
    d["params"] = json.loads(d["params"] or "{}")
    d["delivery"] = json.loads(d["delivery"]) if d.get("delivery") else None
    q = float(d["qty"])
    d["qty"] = int(q) if q.is_integer() else q
    return d


async def user_orders(uid: int, limit: int = 30, offset: int = 0) -> list[dict]:
    db = await connect()
    return [_order(r) for r in await db.all("SELECT * FROM orders WHERE user_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                                            (uid, limit, offset))]


async def orders_by_status(*statuses: str) -> list[dict]:
    db = await connect()
    q = f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(statuses))}) ORDER BY created_at"
    return [_order(r) for r in await db.all(q, statuses)]


async def pending_orders(limit: int = 30) -> list[dict]:
    db = await connect()
    rows = await db.all(
        "SELECT * FROM orders WHERE status IN ('submitting','wait') "
        "ORDER BY created_at DESC LIMIT ?", (limit,))
    return [_order(r) for r in rows]


# ── الإيداعات ────────────────────────────────────────────
async def create_deposit(d: dict) -> int:
    db = await connect()
    d.setdefault("created_at", now())
    d.setdefault("updated_at", now())
    cols = ",".join(d.keys())
    return await db.insert_id(f"INSERT INTO deposits({cols}) VALUES({','.join('?' * len(d))})", tuple(d.values()))


async def get_deposit(did: int) -> dict | None:
    db = await connect()
    return await db.one("SELECT * FROM deposits WHERE id=?", (did,))


async def decide_deposit(did: int, status: str, note: str | None = None, amount_usd: float | None = None) -> bool:
    """يغيّر حالة الإيداع من pending فقط (يمنع القبول المزدوج)."""
    db = await connect()
    if amount_usd is not None:
        n = await db.execute("UPDATE deposits SET status=?, admin_note=?, amount_usd=?, updated_at=? WHERE id=? AND status='pending'",
                             (status, note, float(amount_usd), now(), did))
    else:
        n = await db.execute("UPDATE deposits SET status=?, admin_note=?, updated_at=? WHERE id=? AND status='pending'",
                             (status, note, now(), did))
    return n == 1


async def user_deposits(uid: int, limit: int = 30) -> list[dict]:
    db = await connect()
    return await db.all("SELECT * FROM deposits WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (uid, limit))


async def pending_deposits() -> list[dict]:
    db = await connect()
    return await db.all("SELECT * FROM deposits WHERE status='pending' ORDER BY created_at")


# ── الإحصائيات ───────────────────────────────────────────
async def stats(since: int = 0) -> dict:
    db = await connect()
    out = {}
    r = await db.one("SELECT COUNT(*) AS c, COALESCE(SUM(balance),0) AS b FROM users")
    out["users"] = r["c"]; out["user_balances"] = round(float(r["b"]), 2)
    r = await db.one("SELECT COUNT(*) AS c, COALESCE(SUM(price),0) AS s, COALESCE(SUM(price - COALESCE(cost,0)),0) AS p "
                     "FROM orders WHERE status='accept' AND created_at>=?", (since,))
    out["orders"] = r["c"]; out["sales"] = round(float(r["s"]), 2); out["profit"] = round(float(r["p"]), 2)
    out["orders_pending"] = (await db.one("SELECT COUNT(*) AS c FROM orders WHERE status IN ('wait','submitting')"))["c"]
    out["deposits_pending"] = (await db.one("SELECT COUNT(*) AS c FROM deposits WHERE status='pending'"))["c"]
    r = await db.one("SELECT COALESCE(SUM(amount_usd),0) AS s FROM deposits WHERE status='approved' AND created_at>=?", (since,))
    out["deposits"] = round(float(r["s"]), 2)
    return out
