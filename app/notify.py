"""إرسال الإشعارات عبر البوت (يُضبط البوت عند التشغيل)."""
import logging

from . import config

log = logging.getLogger("notify")
bot = None  # aiogram.Bot — يُعيَّن في main.py


async def user(uid: int, text: str, **kw) -> None:
    if bot is None:
        log.info("[إشعار %s] %s", uid, text)
        return
    try:
        await bot.send_message(uid, text, parse_mode="HTML", disable_web_page_preview=True, **kw)
    except Exception as e:  # المستخدم حظر البوت مثلاً
        log.warning("تعذّر إرسال إشعار إلى %s: %s", uid, e)


async def admins(text: str, **kw) -> None:
    for aid in config.ADMIN_IDS:
        await user(aid, text, **kw)


async def admins_photo(path: str, caption: str, **kw) -> None:
    if bot is None:
        log.info("[إشعار المدراء + صورة] %s", caption)
        return
    from aiogram.types import FSInputFile
    for aid in config.ADMIN_IDS:
        try:
            await bot.send_photo(aid, FSInputFile(path), caption=caption, parse_mode="HTML", **kw)
        except Exception as e:
            log.warning("تعذّر إرسال صورة إلى المدير %s: %s", aid, e)
            await user(aid, caption, **kw)
