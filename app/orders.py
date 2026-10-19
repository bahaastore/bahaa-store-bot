"""منطق الطلبات: الخصم، الإرسال للمزوّد، المتابعة، الاسترداد التلقائي."""
import asyncio
import math
import json
import logging
import uuid

from . import catalog, config, db, notify
from .supplier import client, SupplierError, param_key, IS_MOCK

log = logging.getLogger("orders")


class OrderError(Exception):
    pass


def fmt_usd(x: float) -> str:
    x = float(x or 0)
    return (f"{x:,.4f}".rstrip("0").rstrip(".") or "0") if abs(x) < 1 else f"{x:,.2f}"


async def place_order(uid: int, product_id: int, qty: float, fields: dict) -> dict:
    user = await db.get_user(uid)
    if not user or user["banned"]:
        raise OrderError("حسابك موقوف")
    if await db.get_setting("maintenance"):
        raise OrderError("المتجر في وضع الصيانة حالياً")

    p = await catalog.live_product(product_id)
    if not p:
        raise OrderError("المنتج غير موجود")
    pub = await catalog.public_product(p)
    if not pub["available"]:
        raise OrderError("المنتج غير متاح حالياً")
    if pub["type"] == "package":
        qty = pub["qty"]["min"]
    else:
        qty = float(qty)
        qty = int(qty) if qty.is_integer() else qty
    vals = pub["qty"].get("values")
    if vals and qty not in vals:
        raise OrderError("اختر الكمية من القيم المتاحة")
    if not (pub["qty"]["min"] <= qty <= pub["qty"]["max"]):
        raise OrderError(f"الكمية يجب أن تكون بين {pub['qty']['min']} و {pub['qty']['max']}")

    params = {}
    for f in pub["fields"]:
        val = str(fields.get(f["key"], fields.get(f["label"], ""))).strip()
        if not val and f.get("required", True):
            raise OrderError(f"أدخل {f['label']}")
        if val and f["options"] and val not in {str(o["value"]) for o in f["options"]}:
            raise OrderError(f"اختر {f['label']} من القائمة")
        if val:
            params[f["key"]] = val[:200]

    total = math.ceil(round(pub["price"] * qty * 10000, 6)) / 10000  # للأعلى لأقرب 0.0001$
    oid = str(uuid.uuid4())
    new_balance = await db.adjust_balance(uid, -total, "order", oid, require_funds=True)
    if new_balance is None:
        raise OrderError("رصيدك غير كافٍ — اشحن رصيدك أولاً")

    await db.create_order({
        "id": oid, "user_id": uid, "product_id": product_id, "product_name": pub["full_name"] or pub["name"], "qty": qty,
        "params": json.dumps(params, ensure_ascii=False), "unit_price": pub["price"], "price": total,
        "cost": round(float(p.get("price") or 0) * qty, 6), "status": "submitting", "balance_after": new_balance,
    })
    await submit(oid)
    return await db.get_order(oid)


async def submit(oid: str) -> None:
    """يرسل الطلب للمزوّد. آمن لإعادة المحاولة لأن order_uuid يمنع التكرار."""
    o = await db.get_order(oid)
    if not o or o["status"] != "submitting":
        return
    try:
        res = await client.create_order(o["product_id"], o["qty"], oid, o["params"])
    except SupplierError as e:
        if e.retryable or e.code is None:
            log.warning("سيُعاد إرسال الطلب %s لاحقاً: %s", oid, e.message)
            return
        if e.code == 100:
            await notify.admins(f"⚠️ رصيدك لدى المزوّد غير كافٍ — رُفض الطلب #{o['num']} وأُعيد المبلغ للعميل. اشحن رصيد ماكس ستور.")
        await finish(o, "reject", note=e.message if e.code != 100 else "تعذّر تنفيذ الطلب حالياً، أُعيد المبلغ لرصيدك")
        return
    status = res.get("status", "wait")
    cost = res.get("price")
    await db.update_order(oid, status="wait", cost=float(cost) if cost is not None else o["cost"])
    if status in ("accept", "reject"):
        await finish(await db.get_order(oid), status, delivery=res.get("delivery"))


async def finish(o: dict, status: str, delivery=None, note: str | None = None) -> None:
    if o["status"] in ("accept", "reject"):
        return
    if status == "reject":
        await db.update_order(o["id"], status="reject", note=note)
        bal = await db.adjust_balance(o["user_id"], o["price"], "refund", o["id"])
        await notify.user(o["user_id"],
                          f"❌ <b>تم رفض طلبك #{o['num']}</b>\n"
                          f"المنتج: {o['product_name']}\n"
                          f"{'السبب: ' + note + chr(10) if note else ''}"
                          f"↩️ أُعيد المبلغ <b>{fmt_usd(o['price'])}$</b> إلى رصيدك.\n"
                          f"رصيدك الحالي: <b>{fmt_usd(bal or 0)}$</b>")
        return
    await db.update_order(o["id"], status="accept", delivery=json.dumps(delivery or [], ensure_ascii=False))
    await db.add_spent(o["user_id"], o["price"])
    lines = [f"✅ <b>تم تنفيذ طلبك #{o['num']} بنجاح</b>", f"المنتج: {o['product_name']}"]
    if o["qty"] > 1:
        lines.append(f"الكمية: {o['qty']}")
    for k, v in (o["params"] or {}).items():
        lines.append(f"{k.replace('_', ' ')}: <code>{v}</code>")
    if delivery:
        lines.append("\n📨 <b>التسليم:</b>")
        lines += [f"<code>{d}</code>" for d in (delivery if isinstance(delivery, list) else [delivery])]
    lines.append(f"\nالمبلغ: {fmt_usd(o['price'])}$")
    await notify.user(o["user_id"], "\n".join(lines))


async def poll_loop() -> None:
    """يتابع الطلبات المعلّقة كل ORDER_POLL_SEC ثانية."""
    while True:
        try:
            for o in await db.orders_by_status("submitting"):
                await submit(o["id"])
            waiting = await db.orders_by_status("wait")
            if waiting:
                by_id = {o["id"]: o for o in waiting}
                for r in await client.check(list(by_id)):
                    o = by_id.get(r.get("order_id"))
                    if o and r.get("status") in ("accept", "reject"):
                        await finish(o, r["status"], delivery=r.get("delivery"),
                                     note=r.get("note") or r.get("reason"))
        except SupplierError as e:
            log.warning("فشل فحص الطلبات: %s", e.message)
        except Exception:
            log.exception("خطأ في حلقة متابعة الطلبات")
        await asyncio.sleep(5 if IS_MOCK else config.ORDER_POLL_SEC)
