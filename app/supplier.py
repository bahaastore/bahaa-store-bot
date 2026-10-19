"""عميل API المزوّد (ماكس ستور v2) + مزوّد تجريبي للاختبار.

التوثيق: https://maxstore1.com/api-docs
  GET  /api/v2/profile                 الرصيد والملف
  GET  /api/v2/products[?products_id=] المنتجات
  GET  /api/v2/content/{category_id}   الأقسام / منتجات قسم
  POST /api/v2/order                   إنشاء طلب (order_uuid إلزامي — يمنع التكرار)
  GET  /api/v2/check?orders=uuid1,...  حالة الطلبات (حتى 50)
"""
import asyncio
import logging
import re
import uuid

import httpx

from . import config

log = logging.getLogger("supplier")

ERROR_MESSAGES = {
    100: "رصيد المتجر لدى المزوّد غير كافٍ",
    105: "الكمية غير متوفرة",
    106: "حقل مطلوب ناقص",
    109: "المنتج محذوف أو غير موجود",
    110: "المنتج غير متاح حالياً",
    111: "طلبات كثيرة — حاول بعد قليل",
    114: "خطأ غير معروف لدى المزوّد",
    120: "توكن API مفقود",
    121: "توكن API غير صحيح",
    122: "الحساب غير مسموح له باستخدام API",
    123: "عنوان IP الخادم غير مسموح — أضفه في إعدادات مفتاح API",
    130: "المزوّد في وضع الصيانة",
}


class SupplierError(Exception):
    def __init__(self, code: int | None, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def param_key(label: str) -> str:
    if config.SUPPLIER_PARAM_KEYS == "raw":
        return label
    key = re.sub(r"[^0-9a-zA-Z\u0600-\u06FF]+", "_", label.strip()).strip("_").lower()
    return key or label


class MaxStoreClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url
        self.http = httpx.AsyncClient(base_url=base_url, timeout=30,
                                      headers={"api-token": token, "Accept": "application/json"})

    async def _req(self, method: str, path: str, **kw):
        for attempt in range(3):
            try:
                r = await self.http.request(method, path, **kw)
            except httpx.HTTPError as e:
                if attempt == 2:
                    raise SupplierError(None, f"تعذّر الاتصال بالمزوّد: {e}", retryable=True)
                await asyncio.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", "5"))
                await asyncio.sleep(min(wait, 30))
                continue
            try:
                data = r.json()
            except ValueError:
                raise SupplierError(None, f"رد غير متوقع من المزوّد (HTTP {r.status_code})", retryable=r.status_code >= 500)
            if isinstance(data, dict) and isinstance(data.get("detail"), dict):
                code = data["detail"].get("code")
                raise SupplierError(code, ERROR_MESSAGES.get(code, data["detail"].get("message", "خطأ")), retryable=code in (111, 130))
            if r.status_code >= 400:
                raise SupplierError(None, f"HTTP {r.status_code}", retryable=r.status_code >= 500)
            return data
        raise SupplierError(111, ERROR_MESSAGES[111], retryable=True)

    async def profile(self) -> dict:
        return await self._req("GET", "/api/v2/profile")

    async def products(self, ids: list[int] | None = None) -> list[dict]:
        params = {"products_id": ",".join(map(str, ids))} if ids else None
        data = await self._req("GET", "/api/v2/products", params=params)
        return data if isinstance(data, list) else data.get("data", [])

    async def content(self, category_id: int = 0):
        return await self._req("GET", f"/api/v2/content/{category_id}")

    async def create_order(self, product_id: int, qty: int, order_uuid: str, params: dict) -> dict:
        body = {"product_id": product_id, "qty": qty, "order_uuid": order_uuid, "params": params}
        data = await self._req("POST", "/api/v2/order", json=body)
        return data.get("data", data)

    async def check(self, uuids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(uuids), 50):
            data = await self._req("GET", "/api/v2/check", params={"orders": ",".join(uuids[i:i + 50])})
            out.extend(data.get("data", []) if isinstance(data, dict) else data)
        return out


# ────────────────────────────────────────────────────────
# مزوّد تجريبي — يُستخدم تلقائياً إذا لم يوجد SUPPLIER_API_TOKEN
# ────────────────────────────────────────────────────────
_MOCK_CATS = [
    {"id": 1, "name": "شحن الألعاب", "parent_id": 0},
    {"id": 2, "name": "تطبيقات الدردشة", "parent_id": 0},
    {"id": 3, "name": "تحويل الرصيد", "parent_id": 0},
    {"id": 4, "name": "بطاقات رقمية", "parent_id": 0},
    {"id": 11, "name": "PUBG Mobile", "parent_id": 1},
    {"id": 12, "name": "Free Fire", "parent_id": 1},
    {"id": 13, "name": "Mobile Legends", "parent_id": 1},
    {"id": 21, "name": "Bigo Live", "parent_id": 2},
    {"id": 22, "name": "Likee", "parent_id": 2},
    {"id": 23, "name": "Jawaker", "parent_id": 2},
    {"id": 31, "name": "سيرياتيل", "parent_id": 3},
    {"id": 32, "name": "MTN", "parent_id": 3},
    {"id": 41, "name": "Google Play", "parent_id": 4},
    {"id": 42, "name": "iTunes", "parent_id": 4},
]
_MOCK_PRODUCTS = [
    (101, "UC 60", 0.95, ["Player ID"], 11, "package"), (102, "UC 325", 4.6, ["Player ID"], 11, "package"),
    (103, "UC 660", 9.1, ["Player ID"], 11, "package"), (104, "UC 1800", 23.5, ["Player ID"], 11, "package"),
    (111, "110 جوهرة", 1.05, ["Player ID"], 12, "package"), (112, "341 جوهرة", 3.2, ["Player ID"], 12, "package"),
    (113, "عضوية أسبوعية", 1.9, ["Player ID"], 12, "package"),
    (121, "86 ماسة", 1.5, ["Player ID", "Zone ID"], 13, "package"), (122, "172 ماسة", 2.95, ["Player ID", "Zone ID"], 13, "package"),
    (211, "Bigo — ماسة (للوحدة)", 0.021, ["Bigo ID"], 21, "amount"),
    (221, "Likee — 100 ماسة", 1.4, ["Likee ID"], 22, "package"),
    (231, "Jawaker — 10,000 توكنز", 1.7, ["Player ID"], 23, "package"),
    (311, "رصيد سيرياتيل (لكل 1000 ل.س)", 0.08, ["Phone Number"], 31, "amount"),
    (312, "باقة سيرياتيل 4GB", 1.2, ["Phone Number"], 31, "package"),
    (321, "رصيد MTN (لكل 1000 ل.س)", 0.08, ["Phone Number"], 32, "amount"),
    (411, "Google Play 10$ (US)", 10.4, [], 41, "package"), (412, "Google Play 25$ (US)", 25.9, [], 41, "package"),
    (421, "iTunes 10$ (US)", 10.5, [], 42, "package"),
]


class MockClient:
    """يحاكي ردود ماكس ستور لتجربة المتجر بدون رصيد حقيقي."""

    def __init__(self):
        self.balance = 500.0
        self.orders: dict[str, dict] = {}

    def _products(self):
        return [{"id": i, "name": n, "price": p, "params": prm, "category_id": c, "available": i != 113,
                 "qty_values": {"min": 1, "max": 1} if t == "package" else {"min": 10, "max": 5000},
                 "product_type": t} for i, n, p, prm, c, t in _MOCK_PRODUCTS]

    async def profile(self):
        return {"balance": f"{self.balance:.2f}", "user_id": 1, "username": "demo"}

    async def products(self, ids=None):
        items = self._products()
        return [p for p in items if p["id"] in ids] if ids else items

    async def content(self, category_id: int = 0):
        cats = [c for c in _MOCK_CATS if c["parent_id"] == category_id]
        if cats:
            return cats
        return [p for p in self._products() if p["category_id"] == category_id]

    async def create_order(self, product_id, qty, order_uuid, params):
        if order_uuid in self.orders:
            return self.orders[order_uuid]
        p = next((x for x in self._products() if x["id"] == product_id), None)
        if not p:
            raise SupplierError(109, ERROR_MESSAGES[109])
        if not p["available"]:
            raise SupplierError(110, ERROR_MESSAGES[110])
        price = round(p["price"] * qty, 4)
        self.balance -= price
        o = {"order_id": order_uuid, "status": "wait", "price": price, "data": params,
             "product_name": p["name"], "quantity": qty, "_t": asyncio.get_event_loop().time()}
        self.orders[order_uuid] = o
        return {k: v for k, v in o.items() if not k.startswith("_")}

    async def check(self, uuids):
        out = []
        now = asyncio.get_event_loop().time()
        for u in uuids:
            o = self.orders.get(u)
            if not o:
                continue
            if o["status"] == "wait" and now - o["_t"] > 8:
                o["status"] = "accept"
                o["delivery"] = ["CODE-" + uuid.uuid4().hex[:4].upper() + "-" + uuid.uuid4().hex[:4].upper()] \
                    if not o["data"] else []
            out.append({k: v for k, v in o.items() if not k.startswith("_")})
        return out


def make_client():
    if config.SUPPLIER_API_TOKEN:
        return MaxStoreClient(config.SUPPLIER_BASE_URL, config.SUPPLIER_API_TOKEN)
    if not config.DEMO_MODE:
        raise SystemExit("❌ SUPPLIER_API_TOKEN غير موجود في ملف .env — أضف مفتاح API من ماكس ستور، "
                         "أو شغّل وضع التجربة DEMO_MODE=1")
    log.warning("وضع التجربة: يعمل المتجر على مزوّد وهمي (لا طلبات حقيقية)")
    return MockClient()


client = make_client()
IS_MOCK = isinstance(client, MockClient)
