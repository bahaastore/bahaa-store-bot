"""منطق الطلبات: الخصم، الإرسال للمزوّد، المتابعة، الاسترداد التلقائي."""
import asyncio
import json
import logging
import uuid

from . import catalog, config, db, notify
from .supplier import client, SupplierError, param_key, IS_MOCK

log = logging.getLogger("orders")


class OrderError(Exception):
    pass


def fmt_usd(x: float) -> str:
    return f"{x:,.4f}".rstrip("0").rstrip(".") if x < 1 else f"{x:,.2f}"


async def place_order(uid: int, product_id: int, qty: int, fields: dict) -> dict:
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
    qty = 1 if pub["type"] == "package" else int(qty)
    if not (pub["qty"]["min"] <= qty <= pub["qty"]["max"]):
        raise OrderError(f"الكمية يجب أن تكون بين {pub['qty']['min']} و {pub['qty']['max']}")

    params = {}
    for label in pub["params"]:
        val = str(fields.get(label, "")).strip()
        if not val:
            raise OrderError(f"أدخل {label}")
        params[param_key(label)] = val

    total = round(pub["price"] * qty, 4)
    oid = str(uuid.uuid4())
    new_balance = await db.adjust_balance(uid, -total, "order", oid, require_funds=True)
    if new_balance is None:
        raise OrderError("رصيدك غير كافٍ — اشحن رصيدك أولاً")

    await db.create_order({
        "id": oid, "user_id": uid, "product_id": product_id, "product_name": pub["name"], "qty": qty,
        "params": json.dumps(params, ensure_ascii=False), "unit_price": pub["price"], "price": total,
        "cost": round(float(p.get("price") or 0) * qty, 4), "status": "submitting", "balance_after": new_balance,
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
    dbu = await db.connect()
    await dbu.execute("UPDATE users SET spent = ROUND(spent + ?, 4) WHERE id=?", (o["price"], o["user_id"]))
    await dbu.commit()
    lines = [f"✅ <b>تم تنفيذ طلبك #{o['num']} بنجاح</b>", f"المنتج: {o['product_name']}"]
    if o["qty"] > 1:
        lines.append(f"الكمية: {o['qty']}")
    for k, v in (o["params"] or {}).items():
        lines.append(f"{k}: <code>{v}</code>")
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
