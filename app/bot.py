"""بوت تيليجرام: قائمة العميل + لوحة تحكم المدير."""
import asyncio
import html
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, Message,
                           WebAppInfo, BotCommand)

from . import catalog, config, db, deposits
from .orders import fmt_usd
from .supplier import client, SupplierError, IS_MOCK

router = Router()
STATUS_AR = {"submitting": "⏱ قيد الإرسال", "wait": "⏱ قيد التنفيذ", "accept": "✅ مكتمل", "reject": "❌ مرفوض"}


def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS


def webapp_url(tab: str = "") -> str:
    return f"{config.PUBLIC_URL}/app/" + (f"#{tab}" if tab else "")


def main_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if config.PUBLIC_URL.startswith("https://"):
        rows.append([InlineKeyboardButton(text="🛒 فتح المتجر", web_app=WebAppInfo(url=webapp_url()))])
        rows.append([InlineKeyboardButton(text="💳 شحن الرصيد", web_app=WebAppInfo(url=webapp_url("topup"))),
                     InlineKeyboardButton(text="📦 طلباتي", web_app=WebAppInfo(url=webapp_url("orders")))])
    rows.append([InlineKeyboardButton(text="💰 رصيدي", callback_data="me:balance"),
                 InlineKeyboardButton(text="🧾 آخر الطلبات", callback_data="me:orders")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def support_row() -> list:
    url = await db.get_setting("support_url")
    return [[InlineKeyboardButton(text="🎧 الدعم الفني", url=url)]] if url else []


# ── العميل ───────────────────────────────────────────────
@router.message(CommandStart())
async def start(m: Message):
    u = await db.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
    if u["banned"]:
        return await m.answer("⛔ حسابك موقوف. للاستفسار تواصل مع الدعم الفني.")
    name = await db.get_setting("store_name")
    kb = main_keyboard()
    kb.inline_keyboard += await support_row()
    text = (f"أهلاً بك في <b>{html.escape(name)}</b> 🌿\n\n"
            "متجرك الموثوق لـ:\n"
            "🎮 شحن الألعاب\n💬 شحن تطبيقات الدردشة\n📱 تحويل الرصيد\n🎁 البطاقات الرقمية\n\n"
            f"رصيدك الحالي: <b>{fmt_usd(u['balance'])}$</b>\n"
            f"معرّفك: <code>{u['id']}</code>\n\n"
            "اضغط «فتح المتجر» للبدء — التنفيذ آلي على مدار الساعة.")
    await m.answer(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "me:balance")
async def cb_balance(c: CallbackQuery):
    u = await db.get_user(c.from_user.id) or await db.upsert_user(c.from_user.id, c.from_user.username, c.from_user.first_name)
    rate = await db.get_setting("syp_rate")
    await c.message.answer(f"💰 رصيدك: <b>{fmt_usd(u['balance'])}$</b> ≈ {u['balance'] * rate:,.0f} ل.س\n"
                           f"إجمالي مشترياتك: {fmt_usd(u['spent'])}$", parse_mode="HTML")
    await c.answer()


@router.callback_query(F.data == "me:orders")
async def cb_orders(c: CallbackQuery):
    rows = await db.user_orders(c.from_user.id, 10)
    if not rows:
        await c.message.answer("لا توجد طلبات بعد — افتح المتجر وابدأ التسوّق.")
    else:
        lines = [f"#{o['num']} · {html.escape(o['product_name'])} · {fmt_usd(o['price'])}$ · {STATUS_AR.get(o['status'], o['status'])}"
                 for o in rows]
        await c.message.answer("🧾 <b>آخر طلباتك</b>\n\n" + "\n".join(lines), parse_mode="HTML")
    await c.answer()


@router.message(Command("id"))
async def my_id(m: Message):
    await m.answer(f"معرّفك: <code>{m.from_user.id}</code>", parse_mode="HTML")


# ── المدير ───────────────────────────────────────────────
ADMIN_HELP = """<b>🛠 لوحة تحكم المدير</b>

<b>الإحصائيات</b>
/admin — ملخّص اليوم والإجمالي ورصيد المزوّد
/pending — الإيداعات المعلّقة

<b>الأسعار</b>
/markup 30 — هامش الربح العام (%)
/pmarkup 12 20 — هامش خاص لمنتج (رقم المنتج، النسبة)  |  /pmarkup 12 off للإلغاء
/rate 13000 — سعر صرف الدولار بالليرة
/refresh — تحديث الكتالوج من المزوّد

<b>الإيداعات</b>
/dep_ok 15 — قبول الإيداع 15  |  /dep_ok 15 9.5 — قبول بمبلغ مختلف ($)
/dep_no 15 السبب — رفض الإيداع

<b>العملاء</b>
/user 123456 — معلومات عميل
/addbal 123456 5 — إضافة رصيد ($)  |  /subbal 123456 5 — خصم رصيد
/ban 123456  |  /unban 123456

<b>طرق الدفع</b>
/pay — عرض طرق الدفع
/setpay sham_cash الحساب أو الرقم — تعديل بيانات الحساب
/togglepay usdt — تفعيل/إيقاف طريقة

<b>عام</b>
/support https://t.me/username — رابط الدعم
/notice نص — إعلان يظهر في المتجر  |  /notice off
/maintenance on|off — وضع الصيانة
/broadcast نص — رسالة لكل العملاء"""


def admin_only(handler):
    import inspect
    wanted = set(inspect.signature(handler).parameters) - {"m"}

    async def wrapper(m: Message, **kw):
        if not is_admin(m.from_user.id):
            return
        return await handler(m, **{k: v for k, v in kw.items() if k in wanted})
    wrapper.__name__ = handler.__name__
    return wrapper


@router.message(Command("help", "panel"))
@admin_only
async def admin_help(m: Message):
    await m.answer(ADMIN_HELP, parse_mode="HTML")


@router.message(Command("admin"))
@admin_only
async def admin_stats(m: Message):
    day = int(time.time()) - 86400
    t, a = await db.stats(day), await db.stats(0)
    try:
        prof = await client.profile()
        sup = f"{float(prof.get('balance', 0)):,.2f}$" + (" (تجريبي)" if IS_MOCK else "")
    except SupplierError as e:
        sup = f"تعذّر الجلب: {e.message}"
    await m.answer(
        f"<b>📊 {html.escape(await db.get_setting('store_name'))}</b>\n\n"
        f"<b>آخر 24 ساعة</b>\nطلبات مكتملة: {t['orders']} · مبيعات: {t['sales']}$ · ربح: <b>{t['profit']}$</b>\n"
        f"إيداعات مقبولة: {t['deposits']}$\n\n"
        f"<b>الإجمالي</b>\nعملاء: {a['users']} · طلبات: {a['orders']} · مبيعات: {a['sales']}$ · ربح: <b>{a['profit']}$</b>\n"
        f"أرصدة العملاء: {a['user_balances']}$\n\n"
        f"⏱ طلبات قيد التنفيذ: {a['orders_pending']}\n💳 إيداعات معلّقة: {a['deposits_pending']}\n"
        f"🏦 رصيدك لدى المزوّد: <b>{sup}</b>\n"
        f"📈 الهامش: {await db.get_setting('markup_pct')}% · الصرف: {await db.get_setting('syp_rate'):,.0f} ل.س\n\n/help لكل الأوامر",
        parse_mode="HTML")


@router.message(Command("markup"))
@admin_only
async def set_markup(m: Message, command: CommandObject):
    try:
        pct = float(command.args)
        assert 0 <= pct <= 500
    except Exception:
        return await m.answer("الاستخدام: /markup 30")
    await db.set_setting("markup_pct", pct)
    catalog._content_cache.clear()
    await m.answer(f"✅ تم ضبط هامش الربح العام على {pct}%")


@router.message(Command("pmarkup"))
@admin_only
async def set_pmarkup(m: Message, command: CommandObject):
    parts = (command.args or "").split()
    if len(parts) != 2:
        return await m.answer("الاستخدام: /pmarkup رقم_المنتج النسبة  أو  /pmarkup رقم_المنتج off")
    ov = await db.get_setting("markup_overrides") or {}
    if parts[1] == "off":
        ov.pop(parts[0], None); msg = f"✅ أُلغي الهامش الخاص للمنتج {parts[0]}"
    else:
        ov[parts[0]] = float(parts[1]); msg = f"✅ هامش المنتج {parts[0]} أصبح {parts[1]}%"
    await db.set_setting("markup_overrides", ov)
    catalog._content_cache.clear()
    await m.answer(msg)


@router.message(Command("rate"))
@admin_only
async def set_rate(m: Message, command: CommandObject):
    try:
        rate = float((command.args or "").replace(",", ""))
        assert rate > 0
    except Exception:
        return await m.answer("الاستخدام: /rate 13000")
    await db.set_setting("syp_rate", rate)
    await m.answer(f"✅ سعر الصرف: 1$ = {rate:,.0f} ل.س")


@router.message(Command("refresh"))
@admin_only
async def refresh(m: Message):
    try:
        items = await catalog.refresh_products(force=True)
        catalog._content_cache.clear()
        await m.answer(f"✅ تم تحديث الكتالوج: {len(items)} منتج")
    except SupplierError as e:
        await m.answer(f"❌ {e.message}")


@router.message(Command("pending"))
@admin_only
async def pending(m: Message):
    rows = await db.pending_deposits()
    if not rows:
        return await m.answer("لا توجد إيداعات معلّقة ✓")
    for d in rows[:20]:
        cur = "ل.س" if d["currency"] == "SYP" else "$"
        await m.answer(f"💳 #{d['id']} · {d['method']} · {d['amount']:,.2f} {cur} ≈ {fmt_usd(d['amount_usd'])}$\n"
                       f"العميل: <code>{d['user_id']}</code> · العملية: <code>{html.escape(d['tx_id'])}</code>",
                       parse_mode="HTML", reply_markup=deposits.admin_keyboard(d["id"]))


@router.callback_query(F.data.startswith("dep:"))
async def cb_deposit(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer("غير مسموح", show_alert=True)
    _, action, did = c.data.split(":")
    msg = await (deposits.approve(int(did), by=c.from_user.id) if action == "ok"
                 else deposits.reject(int(did), by=c.from_user.id))
    await c.answer(msg, show_alert=True)
    try:
        await c.message.edit_reply_markup(reply_markup=None)
        await c.message.reply(msg)
    except Exception:
        pass


@router.message(Command("dep_ok"))
@admin_only
async def dep_ok(m: Message, command: CommandObject):
    parts = (command.args or "").split()
    if not parts:
        return await m.answer("الاستخدام: /dep_ok رقم_الإيداع [المبلغ_بالدولار]")
    amount = float(parts[1]) if len(parts) > 1 else None
    await m.answer(await deposits.approve(int(parts[0]), amount, by=m.from_user.id))


@router.message(Command("dep_no"))
@admin_only
async def dep_no(m: Message, command: CommandObject):
    parts = (command.args or "").split(maxsplit=1)
    if not parts:
        return await m.answer("الاستخدام: /dep_no رقم_الإيداع السبب")
    await m.answer(await deposits.reject(int(parts[0]), parts[1] if len(parts) > 1 else "لم يتم العثور على العملية", by=m.from_user.id))


@router.message(Command("user"))
@admin_only
async def user_info(m: Message, command: CommandObject):
    try:
        u = await db.get_user(int(command.args))
    except Exception:
        u = None
    if not u:
        return await m.answer("العميل غير موجود")
    await m.answer(f"👤 {html.escape(u['first_name'] or '')} {'@' + u['username'] if u['username'] else ''}\n"
                   f"المعرّف: <code>{u['id']}</code>\nالرصيد: <b>{fmt_usd(u['balance'])}$</b>\n"
                   f"المشتريات: {fmt_usd(u['spent'])}$\nالحالة: {'⛔ محظور' if u['banned'] else '✅ نشط'}",
                   parse_mode="HTML")


async def _bal(m: Message, args: str | None, sign: int):
    try:
        uid, amt = args.split()
        uid, amt = int(uid), float(amt)
    except Exception:
        return await m.answer("الاستخدام: /addbal المعرّف المبلغ")
    if not await db.get_user(uid):
        return await m.answer("العميل غير موجود (يجب أن يضغط /start أولاً)")
    bal = await db.adjust_balance(uid, sign * amt, "admin", str(m.from_user.id))
    await m.answer(f"✅ الرصيد الجديد للعميل {uid}: {fmt_usd(bal)}$")
    if sign > 0:
        from . import notify
        await notify.user(uid, f"💰 أُضيف <b>{fmt_usd(amt)}$</b> إلى رصيدك من الإدارة.\nرصيدك الحالي: <b>{fmt_usd(bal)}$</b>")


@router.message(Command("addbal"))
@admin_only
async def addbal(m: Message, command: CommandObject):
    await _bal(m, command.args, 1)


@router.message(Command("subbal"))
@admin_only
async def subbal(m: Message, command: CommandObject):
    await _bal(m, command.args, -1)


@router.message(Command("ban", "unban"))
@admin_only
async def ban(m: Message, command: CommandObject):
    try:
        uid = int(command.args)
    except Exception:
        return await m.answer("الاستخدام: /ban المعرّف")
    banned = command.command == "ban"
    await db.set_banned(uid, banned)
    await m.answer(f"{'⛔ تم حظر' if banned else '✅ تم إلغاء حظر'} {uid}")


@router.message(Command("pay"))
@admin_only
async def pay_list(m: Message):
    lines = [f"{'🟢' if p['enabled'] else '🔴'} <code>{p['key']}</code> — {p['label']} ({p['currency']})\n"
             f"   الحساب: {html.escape(str(p['account']))}" for p in await db.get_setting("payment_methods")]
    await m.answer("<b>طرق الدفع</b>\n\n" + "\n".join(lines), parse_mode="HTML")


@router.message(Command("setpay", "togglepay"))
@admin_only
async def setpay(m: Message, command: CommandObject):
    parts = (command.args or "").split(maxsplit=1)
    methods = await db.get_setting("payment_methods")
    pm = next((p for p in methods if parts and p["key"] == parts[0]), None)
    if not pm:
        return await m.answer("حدّد المفتاح الصحيح — أرسل /pay لعرض المفاتيح")
    if command.command == "togglepay":
        pm["enabled"] = not pm["enabled"]
        msg = f"{pm['label']}: {'🟢 مفعّلة' if pm['enabled'] else '🔴 موقوفة'}"
    else:
        if len(parts) < 2:
            return await m.answer("الاستخدام: /setpay المفتاح بيانات_الحساب")
        pm["account"] = parts[1]
        msg = f"✅ حساب {pm['label']}: {parts[1]}"
    await db.set_setting("payment_methods", methods)
    await m.answer(msg)


@router.message(Command("support"))
@admin_only
async def set_support(m: Message, command: CommandObject):
    await db.set_setting("support_url", (command.args or "").strip())
    await m.answer("✅ تم حفظ رابط الدعم")


@router.message(Command("notice"))
@admin_only
async def set_notice(m: Message, command: CommandObject):
    txt = (command.args or "").strip()
    await db.set_setting("announcement", "" if txt == "off" else txt)
    await m.answer("✅ تم تحديث الإعلان")


@router.message(Command("maintenance"))
@admin_only
async def maintenance(m: Message, command: CommandObject):
    on = (command.args or "").strip() == "on"
    await db.set_setting("maintenance", on)
    await m.answer("🛠 وضع الصيانة مفعّل" if on else "✅ المتجر يعمل")


@router.message(Command("broadcast"))
@admin_only
async def broadcast(m: Message, command: CommandObject):
    if not command.args:
        return await m.answer("الاستخدام: /broadcast النص")
    ids = await db.all_user_ids()
    await m.answer(f"⏳ جارٍ الإرسال إلى {len(ids)} عميل…")
    ok = 0
    for uid in ids:
        try:
            await m.bot.send_message(uid, command.args)
            ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await m.answer(f"✅ وصلت الرسالة إلى {ok} من {len(ids)}")


async def setup(bot: Bot) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="القائمة الرئيسية"),
                               BotCommand(command="id", description="معرّفي")])
    if config.PUBLIC_URL.startswith("https://"):
        await bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="المتجر", web_app=WebAppInfo(url=webapp_url())))
    return dp
