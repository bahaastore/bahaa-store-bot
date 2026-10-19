"""الكتالوج: تخزين مؤقت لمنتجات وأقسام المزوّد + تطبيق هامش الربح."""
import asyncio
import logging
import math
import time

from . import db
from .supplier import client, SupplierError

log = logging.getLogger("catalog")

_products: dict[int, dict] = {}
_products_at = 0.0
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
    """سعر البيع = سعر المزوّد × (1 + الهامش). يُقرّب للأعلى حتى لا يقل الربح عن النسبة."""
    pct = await markup_for(product_id)
    price = cost * (1 + pct / 100)
    return _ceil(price, 2) if price >= 1 else _ceil(price, 4)


async def refresh_products(force: bool = False) -> dict[int, dict]:
    global _products, _products_at
    from . import config
    async with _lock:
        if not force and _products and time.time() - _products_at < config.CATALOG_REFRESH_SEC:
            return _products
        items = await client.products()
        _products = {int(p["id"]): p for p in items}
        _products_at = time.time()
        log.info("تم تحديث الكتالوج: %d منتج", len(_products))
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


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


async def public_product(p: dict) -> dict:
    cost = float(p.get("price") or 0)
    qv = p.get("qty_values") or {}
    ptype = p.get("product_type", "package")
    if ptype == "package":
        qv = {"min": 1, "max": 1}
    return {
        "id": int(p["id"]),
        "name": p.get("name", ""),
        "price": await sell_price(int(p["id"]), cost),
        "params": p.get("params") or [],
        "category_id": p.get("category_id"),
        "available": bool(p.get("available", True)),
        "qty": {"min": int(qv.get("min") or 1), "max": int(qv.get("max") or 1)},
        "type": ptype,
        "image": _first(p, "image", "img", "icon", "photo"),
        "description": _first(p, "description", "desc", default=""),
    }


async def content(category_id: int) -> dict:
    """يعيد {categories: [...], products: [...]} لقسم معيّن (0 = الرئيسية)."""
    cached = _content_cache.get(category_id)
    if cached and time.time() - cached[0] < CONTENT_TTL:
        return cached[1]
    raw = await client.content(category_id)
    cats_raw, prods_raw = [], []
    if isinstance(raw, dict):
        for k in ("categories", "sub_categories", "children"):
            cats_raw += raw.get(k) or []
        for k in ("products", "items"):
            prods_raw += raw.get(k) or []
        if not cats_raw and not prods_raw and isinstance(raw.get("data"), list):
            raw = raw["data"]
    if isinstance(raw, list):
        for it in raw:
            (prods_raw if "price" in it else cats_raw).append(it)

    products = await refresh_products()
    cats = [{"id": int(c["id"]), "name": _first(c, "name", "title", default=""),
             "image": _first(c, "image", "img", "icon", "photo")} for c in cats_raw if "id" in c]
    prods = []
    for p in prods_raw:
        full = {**products.get(int(p["id"]), {}), **p}
        prods.append(await public_product(full))
    if not cats and not prods and category_id:
        prods = [await public_product(p) for p in products.values() if p.get("category_id") == category_id]
    result = {"categories": cats, "products": prods}
    _content_cache[category_id] = (time.time(), result)
    return result


async def search(q: str, limit: int = 40) -> list[dict]:
    q = q.strip().lower()
    if not q:
        return []
    products = await refresh_products()
    hits = [p for p in products.values() if q in str(p.get("name", "")).lower()]
    return [await public_product(p) for p in hits[:limit]]
