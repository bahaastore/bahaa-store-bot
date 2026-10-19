"""واجهة التطبيق المصغّر (FastAPI) — كل الطلبات موثّقة ببيانات تيليجرام initData."""
import hashlib
import hmac
import json
import time
import uuid
from urllib.parse import parse_qsl

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import catalog, config, db, deposits
from .orders import OrderError, place_order
from .supplier import SupplierError, IS_MOCK

app = FastAPI(title=config.STORE_NAME, docs_url=None, redoc_url=None)
if IS_MOCK:  # معاينة وضع التجربة فقط
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
WEBAPP_DIR = config.BASE_DIR / "webapp"
DEMO_USER = {"id": 100000001, "first_name": "زائر تجريبي", "username": "demo"}


def verify_init_data(init_data: str) -> dict:
    """التحقق من توقيع تيليجرام: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app"""
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received = pairs.pop("hash", None)
    if not received or not config.BOT_TOKEN:
        raise HTTPException(401, "فشل تسجيل الدخول — أعد فتح المتجر من البوت")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received):
        raise HTTPException(401, "فشل تسجيل الدخول — أعد فتح المتجر من البوت")
    if time.time() - int(pairs.get("auth_date", "0")) > 86400:
        raise HTTPException(401, "انتهت الجلسة — أعد فتح المتجر من البوت")
    return json.loads(pairs.get("user", "{}"))


async def current_user(x_init_data: str = Header(default="")) -> dict:
    if not x_init_data and config.DEMO_MODE and IS_MOCK:  # وضع التجربة فقط — لا يعمل مع مزوّد حقيقي
        tg = DEMO_USER
    else:
        tg = verify_init_data(x_init_data)
    user = await db.upsert_user(int(tg["id"]), tg.get("username"), tg.get("first_name"))
    if user["banned"]:
        raise HTTPException(403, "حسابك موقوف — تواصل مع الدعم الفني")
    if IS_MOCK and user["id"] == DEMO_USER["id"] and user["balance"] == 0 and not user["spent"]:
        await db.adjust_balance(user["id"], 25, "demo")
        user = await db.get_user(user["id"])
    return user


def _supplier_guard(fn):
    async def wrapper(*a, **kw):
        try:
            return await fn(*a, **kw)
        except SupplierError as e:
            raise HTTPException(503, e.message)
    wrapper.__name__ = fn.__name__
    wrapper.__signature__ = __import__("inspect").signature(fn)
    return wrapper


@app.get("/api/app/me")
async def me(user: dict = Depends(current_user)):
    return {
        "user": {"id": user["id"], "name": user["first_name"], "username": user["username"],
                 "balance": round(user["balance"], 4), "spent": round(user["spent"], 4),
                 "is_admin": user["id"] in config.ADMIN_IDS},
        "store": {"name": await db.get_setting("store_name"), "syp_rate": await db.get_setting("syp_rate"),
                  "support_url": await db.get_setting("support_url"), "announcement": await db.get_setting("announcement"),
                  "maintenance": await db.get_setting("maintenance"), "demo": IS_MOCK},
    }


@app.get("/api/app/content/{category_id}")
@_supplier_guard
async def content(category_id: int, user: dict = Depends(current_user)):
    return await catalog.content(category_id)


@app.get("/api/app/search")
@_supplier_guard
async def search(q: str, user: dict = Depends(current_user)):
    return {"products": await catalog.search(q)}


@app.get("/api/app/product/{pid}")
@_supplier_guard
async def product(pid: int, user: dict = Depends(current_user)):
    p = await catalog.live_product(pid)
    if not p:
        raise HTTPException(404, "المنتج غير موجود")
    return await catalog.public_product(p)


class OrderIn(BaseModel):
    product_id: int
    qty: int = 1
    fields: dict = {}


def _order_out(o: dict) -> dict:
    return {k: o[k] for k in ("id", "num", "product_name", "qty", "params", "price", "status", "delivery",
                              "note", "balance_after", "created_at")}


@app.post("/api/app/order")
async def order(body: OrderIn, user: dict = Depends(current_user)):
    try:
        o = await place_order(user["id"], body.product_id, body.qty, body.fields)
    except OrderError as e:
        raise HTTPException(400, str(e))
    return _order_out(o)


@app.get("/api/app/orders")
async def orders(offset: int = 0, user: dict = Depends(current_user)):
    return {"orders": [_order_out(o) for o in await db.user_orders(user["id"], 30, offset)]}


@app.get("/api/app/wallet/methods")
async def wallet_methods(user: dict = Depends(current_user)):
    return {"methods": await deposits.methods(), "syp_rate": await db.get_setting("syp_rate")}


@app.post("/api/app/wallet/deposit")
async def wallet_deposit(method: str = Form(...), amount: float = Form(...), tx_id: str = Form(...),
                         proof: UploadFile | None = File(default=None), user: dict = Depends(current_user)):
    path = None
    if proof is not None and proof.filename:
        data = await proof.read()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(400, "حجم الصورة كبير (الحد 8MB)")
        ext = (proof.filename.rsplit(".", 1)[-1] or "jpg").lower()[:5]
        if ext not in {"jpg", "jpeg", "png", "webp", "heic"}:
            ext = "jpg"
        path = str(config.UPLOAD_DIR / f"{uuid.uuid4().hex}.{ext}")
        with open(path, "wb") as f:
            f.write(data)
    try:
        dep = await deposits.create(user["id"], method, amount, tx_id, path)
    except deposits.DepositError as e:
        raise HTTPException(400, str(e))
    return {"id": dep["id"], "status": dep["status"]}


@app.get("/api/app/wallet/deposits")
async def wallet_deposits(user: dict = Depends(current_user)):
    labels = {m["key"]: m["label"] for m in (await db.get_setting("payment_methods") or [])}
    rows = await db.user_deposits(user["id"])
    return {"deposits": [{**{k: d[k] for k in ("id", "amount", "currency", "amount_usd", "tx_id", "status",
                                                 "admin_note", "created_at")},
                          "method_label": labels.get(d["method"], d["method"])} for d in rows]}


@app.get("/health")
async def health():
    return {"ok": True}


# ── ملفات التطبيق المصغّر ────────────────────────────────
@app.get("/")
async def root():
    return RedirectResponse("/app/")


@app.get("/app")
async def app_noslash():
    return RedirectResponse("/app/")


@app.get("/app/")
async def app_index():
    return FileResponse(WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


app.mount("/app/assets", StaticFiles(directory=WEBAPP_DIR / "assets"), name="assets")
