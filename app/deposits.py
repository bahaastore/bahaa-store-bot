"""الإيداعات: يرسل العميل رقم العملية، ويقبلها المدير من البوت بضغطة زر."""
import html

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import db, notify
from .orders import fmt_usd


class DepositError(Exception):
    pass


async def methods() -> list[dict]:
    return [m for m in (await db.get_setting("payment_methods") or []) if m.get("enabled")]


async def create(uid: int, method_key: str, amount: float, tx_id: str, proof: str | None) -> dict:
    m = next((x for x in await methods() if x["key"] == method_key), None)
    if not m:
        raise DepositError("طريقة الدفع غير متاحة")
    tx_id = tx_id.strip()
    if len(tx_id) < 4:
        raise DepositError("أدخل رقم العملية بشكل صحيح")
    if amount <= 0 or amount < float(m.get("min") or 0):
        raise DepositError(f"الحد الأدنى {m.get('min')} {'ل.س' if m['currency'] == 'SYP' else '$'}")
    rate = float(await db.get_setting("syp_rate"))
    amount_usd = round(amount / rate, 4) if m["currency"] == "SYP" else round(amount, 4)
    try:
        did = await db.create_deposit({"user_id": uid, "method": m["key"], "amount": amount, "currency": m["currency"],
                                       "amount_usd": amount_usd, "tx_id": tx_id, "proof": proof})
    except Exception:
        raise DepositError("رقم العملية هذا مستخدم مسبقاً")
    dep = await db.get_deposit(did)
    await _alert_admins(dep, m)
    return dep


def admin_keyboard(did: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ قبول", callback_data=f"dep:ok:{did}"),
        InlineKeyboardButton(text="❌ رفض", callback_data=f"dep:no:{did}"),
    ]])


async def _alert_admins(dep: dict, m: dict) -> None:
    u = await db.get_user(dep["user_id"]) or {}
    cur = "ل.س" if dep["currency"] == "SYP" else "$"
    text = (f"💳 <b>طلب إيداع جديد #{dep['id']}</b>\n"
            f"العميل: {html.escape(u.get('first_name') or '')} "
            f"{'@' + u['username'] if u.get('username') else ''} — <code>{dep['user_id']}</code>\n"
            f"الطريقة: {m['label']}\n"
            f"المبلغ: <b>{dep['amount']:,.0f} {cur}</b> ≈ <b>{fmt_usd(dep['amount_usd'])}$</b>\n"
            f"رقم العملية: <code>{html.escape(dep['tx_id'])}</code>\n\n"
            f"للقبول بمبلغ مختلف: <code>/dep_ok {dep['id']} المبلغ_بالدولار</code>")
    if dep.get("proof"):
        await notify.admins_photo(dep["proof"], text, reply_markup=admin_keyboard(dep["id"]))
    else:
        await notify.admins(text, reply_markup=admin_keyboard(dep["id"]))


async def approve(did: int, amount_usd: float | None = None, by: int | None = None) -> str:
    dep = await db.get_deposit(did)
    if not dep:
        return "الإيداع غير موجود"
    if not await db.decide_deposit(did, "approved", f"by {by}", amount_usd):
        return f"الإيداع #{did} تمت معالجته مسبقاً ({dep['status']})"
    amt = amount_usd if amount_usd is not None else dep["amount_usd"]
    bal = await db.adjust_balance(dep["user_id"], amt, "deposit", str(did))
    await notify.user(dep["user_id"], f"✅ <b>تم قبول إيداعك #{did}</b>\n"
                                      f"أُضيف <b>{fmt_usd(amt)}$</b> إلى رصيدك.\n"
                                      f"رصيدك الحالي: <b>{fmt_usd(bal or 0)}$</b>")
    return f"✅ تم قبول الإيداع #{did} وإضافة {fmt_usd(amt)}$"


async def reject(did: int, reason: str = "لم يتم العثور على العملية", by: int | None = None) -> str:
    dep = await db.get_deposit(did)
    if not dep:
        return "الإيداع غير موجود"
    if not await db.decide_deposit(did, "rejected", reason):
        return f"الإيداع #{did} تمت معالجته مسبقاً ({dep['status']})"
    await notify.user(dep["user_id"], f"❌ <b>تم رفض إيداعك #{did}</b>\nالسبب: {html.escape(reason)}\n"
                                      f"للاستفسار تواصل مع الدعم الفني.")
    return f"❌ تم رفض الإيداع #{did}"
