"""الكتالوج: تخزين مؤقت لمنتجات وأقسام ماكس ستور + تطبيق هامش الربح + الأقسام الرئيسية."""
import asyncio
import logging
import math
import time
from collections import Counter

from . import config, db
from .groups import GROUP_BASE, GROUPS, group_of
from .supplier import client, SupplierError, param_key

log = logging.getLogger("catalog")

_products: dict[int, dict] = {}
_products_at = 0.0
_cats: list[dict] = []
_content_cache: dict[int, tuple[float, dict]] = {}
_lock = asyncio.Lock()
CONTENT_TTL = 300


def _ceil(x: float, digits: int) -> float:
    f = 10 ** digits
    return math.ceil(round(x * f, 6)) / f


async def markup_for(product_id: int) -> float:
    overrides = await db.get_setting("markup_overrides") or {}
    if str(product_id) in overrides:
        return float(overrides[str(product_id)])
    return float(await db.get_setting("markup_pct"))


async def sell_price(product_id: int, cost: float) -> float:
    """سعر البيع = سعر المزوّد × (1 + الهامش)، مقرّباً للأعلى حتى لا يقل الربح عن النسبة."""
    price = cost * (1 + await markup_for(product_id) / 100)
    if price >= 1:
        return _ceil(price, 2)
    if price >= 0.01:
        return _ceil(price, 4)
    return _ceil(price, 9)  # منتجات بالوحدة (مثل الرصيد بالليرة أو الماسات)


def media_url(path):
    if not path:
        return None
    return path if str(path).startswith("http") else config.SUPPLIER_BASE_URL + str(path)


async def refresh_products(force: bool = False) -> dict[int, dict]:
    global _products, _products_at, _cats
    async with _lock:
        if not force and _products and time.time() - _products_at < config.CATALOG_REFRESH_SEC:
            return _products
        items = await client.products()
        raw = await client.content(0)
        cats = raw.get("categories", []) if isinstance(raw, dict) else raw
        _products = {int(p["id"]): p for p in items}
        _cats = [c for c in cats if "id" in c]
        _products_at = time.time()
        _content_cache.clear()
        log.info("تم تحديث الكتالوج: %d منتج، %d قسم", len(_products), len(_cats))
        return _products


async def live_product(pid: int) -> dict | None:
    """يجلب سعر المنتج الحالي من المزوّد مباشرة قبل الشراء."""
    try:
        items = await client.products([pid])
    except SupplierError:
        items = []
    if items:
        _products[pid] = items[0]
        return items[0]
    return _products.get(pid)


def _opt(o):
    if isinstance(o, str) and o.strip().startswith("{"):
        try:  # بعض الخيارات تأتي من المزوّد كنص لقاموس بايثون
            import ast
            o = ast.literal_eval(o.strip())
        except (ValueError, SyntaxError):
            pass
    if isinstance(o, dict):
        return {"label": str(o.get("label", o.get("value", ""))), "value": str(o.get("value", o.get("label", "")))}
    return {"label": str(o), "value": str(o)}


def _fields(p: dict) -> list[dict]:
    meta = p.get("params_meta")
    if meta:
        return [{"key": m.get("name") or param_key(m.get("label", "")), "label": m.get("label") or m.get("name"),
                 "type": m.get("type", "text"), "required": m.get("required", True),
                 "options": [_opt(o) for o in (m.get("options") or [])]} for m in meta]
    return [{"key": param_key(l), "label": l, "type": "text", "required": True, "options": []} for l in p.get("params") or []]


def _num(x):
    x = float(x)
    return int(x) if x.is_integer() else x


def _qty(p: dict):
    """qty_values قد تكون: None (كمية ثابتة) | {"min","max"} (مدى) | [قيم مسموحة]."""
    qv = p.get("qty_values")
    if isinstance(qv, list) and qv:
        vals = [_num(v) for v in qv]
        return min(vals), max(vals), vals
    qv = qv if isinstance(qv, dict) else {}
    mn = qv.get("min", p.get("min_qty")) or 1
    mx = qv.get("max", p.get("max_qty")) or mn
    return _num(mn), _num(mx), None


async def public_product(p: dict) -> dict:
    cost = float(p.get("price") or 0)
    mn, mx, vals = _qty(p)
    return {
        "id": int(p["id"]),
        "name": p.get("product_name") or p.get("name", ""),
        "full_name": p.get("name", ""),
        "price": await sell_price(int(p["id"]), cost),
        "fields": _fields(p),
        "category": p.get("category_name"),
        "available": bool(p.get("available", True)),
        "qty": {"min": mn, "max": mx, "values": vals},
        "type": "package" if mx <= mn and not vals else "amount",
        "image": media_url(p.get("image") or p.get("img")),
        "description": p.get("description") or "",
    }


def _cat_counts() -> Counter:
    return Counter(p.get("category_name") for p in _products.values())


async def content(category_id: int) -> dict:
    """يعيد {categories, products} — 0 = الأقسام الرئيسية، GROUP_BASE+n = أقسام قسم رئيسي، غير ذلك = منتجات قسم."""
    cached = _content_cache.get(category_id)
    if cached and time.time() - cached[0] < CONTENT_TTL:
        return cached[1]
    await refresh_products()
    counts = _cat_counts()
    live = [c for c in _cats if counts.get(c.get("name"), 0) > 0]

    if category_id == 0:
        by_group = Counter(group_of(int(c["id"]), c["name"]) for c in live)
        result = {"categories": [{"id": GROUP_BASE + g["gid"], "name": g["name"], "icon": g["icon"], "image": None,
                                  "count": by_group[g["gid"]]} for g in GROUPS if by_group[g["gid"]]],
                  "products": []}
    elif category_id > GROUP_BASE:
        gid = category_id - GROUP_BASE
        result = {"categories": [{"id": int(c["id"]), "name": c["name"], "image": media_url(c.get("image")),
                                  "count": counts[c["name"]]} for c in live if group_of(int(c["id"]), c["name"]) == gid],
                  "products": []}
    else:
        try:
            raw = await client.content(category_id)
            ids = [int(x["id"]) for x in (raw.get("products", []) if isinstance(raw, dict) else raw) if "id" in x]
        except SupplierError:
            ids = []
        if not ids:
            name = next((c["name"] for c in _cats if int(c["id"]) == category_id), None)
            ids = [pid for pid, p in _products.items() if p.get("category_name") == name]
        # الأسعار دائماً من /products (نفس مصدر سعر الشراء) حتى يطابق المعروضُ المخصوم
        img = next((media_url(c.get("image")) for c in _cats if int(c["id"]) == category_id), None)
        prods = [await public_product(_products[i]) for i in ids if i in _products]
        for pr in prods:
            pr["image"] = pr["image"] or img
        prods.sort(key=lambda x: (not x["available"], x["price"]))
        result = {"categories": [], "products": prods}
    _content_cache[category_id] = (time.time(), result)
    return result


def category_image(name: str):
    c = next((c for c in _cats if c.get("name") == name), None)
    return media_url(c.get("image")) if c else None


async def search(q: str, limit: int = 40) -> list[dict]:
    q = q.strip().lower()
    if not q:
        return []
    products = await refresh_products()
    hits = [p for p in products.values() if q in str(p.get("name", "")).lower()]
    out = []
    for p in hits[:limit]:
        pub = await public_product(p)
        pub["name"] = p.get("name", pub["name"])
        pub["image"] = pub["image"] or category_image(p.get("category_name"))
        out.append(pub)
    return out
