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
    (5011, "PUBG Mobile"), (5012, "Free Fire"), (5013, "Mobile Legends"), (5021, "Bigo Live"), (5022, "Likee"),
    (5023, "Jawaker"), (5031, "SyriaTel"), (5032, "MTN"), (5041, "Google Play USA"), (5042, "iTunes USA"),
]
_PID = [{"name": "Player_ID", "label": "ID Player", "type": "text", "required": True}]
_PHONE = [{"name": "Player_ID", "label": "رقم المشترك", "type": "number", "required": True}]
_ML = _PID + [{"name": "server_id", "label": "السيرفر", "type": "select", "required": True,
               "options": [{"label": "Zone 1001", "value": "1001"}, {"label": "Zone 2002", "value": "2002"}]}]
_MOCK_PRODUCTS = [
    (101, "60 UC", 0.95, _PID, 5011, 1, 1), (102, "325 UC", 4.6, _PID, 5011, 1, 1),
    (103, "660 UC", 9.1, _PID, 5011, 1, 1), (104, "1800 UC", 23.5, _PID, 5011, 1, 1),
    (111, "110 جوهرة", 1.05, _PID, 5012, 1, 1), (112, "341 جوهرة", 3.2, _PID, 5012, 1, 1),
    (113, "عضوية أسبوعية", 1.9, _PID, 5012, 1, 1),
    (121, "86 ماسة", 1.5, _ML, 5013, 1, 1), (122, "172 ماسة", 2.95, _ML, 5013, 1, 1),
    (211, "Bigo ماسات", 0.0021, _PID, 5021, 100, 100000),
    (221, "Likee 100 ماسة", 1.4, _PID, 5022, 1, 1), (231, "Jawaker 10,000 توكنز", 1.7, _PID, 5023, 1, 1),
    (311, "رصيد سيرياتيل (ليرة)", 0.0000769, _PHONE, 5031, 5000, 1000000),
    (312, "باقة سيرياتيل 4GB", 1.2, _PHONE, 5031, 1, 1),
    (321, "رصيد MTN (ليرة)", 0.0000769, _PHONE, 5032, 5000, 1000000),
    (411, "Google Play 10$", 10.4, [], 5041, 1, 1), (412, "Google Play 25$", 25.9, [], 5041, 1, 1),
    (421, "iTunes 10$", 10.5, [], 5042, 1, 1),
]


class MockClient:
    """يحاكي ردود ماكس ستور الحقيقية لتجربة المتجر بدون رصيد حقيقي."""

    def __init__(self):
        self.balance = 500.0
        self.orders: dict[str, dict] = {}
        self.cat_names = dict(_MOCK_CATS)

    def _products(self):
        out = []
        for i, n, p, meta, c, mn, mx in _MOCK_PRODUCTS:
            out.append({"id": i, "name": f"{n} - {self.cat_names[c]}", "product_name": n, "category_name": self.cat_names[c],
                        "price": p, "params": [m["label"] for m in meta], "params_meta": meta, "available": i != 113,
                        "qty_values": None if mn == mx else {"min": mn, "max": mx}, "min_qty": mn, "max_qty": mx})
        return out

    async def profile(self):
        return {"balance": f"{self.balance:.4f}", "user_id": 1, "username": "demo"}

    async def products(self, ids=None):
        items = self._products()
        return [p for p in items if p["id"] in ids] if ids else items

    async def content(self, category_id: int = 0):
        if category_id == 0:
            return {"categories": [{"id": i, "name": n, "image": None} for i, n in _MOCK_CATS]}
        name = self.cat_names.get(category_id)
        return {"products": [{"id": p["id"], "name": p["product_name"], "price": p["price"]}
                             for p in self._products() if p["category_name"] == name]}

    async def create_order(self, product_id, qty, order_uuid, params):
        if order_uuid in self.orders:
            return self.orders[order_uuid]
        p = next((x for x in self._products() if x["id"] == product_id), None)
        if not p:
            raise SupplierError(109, ERROR_MESSAGES[109])
        if not p["available"]:
            raise SupplierError(110, ERROR_MESSAGES[110])
        price = round(p["price"] * qty, 6)
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
