"""تشغيل بهاء ستور: خادم التطبيق المصغّر + البوت + متابعة الطلبات في عملية واحدة.

    python main.py
"""
import asyncio
import logging

import uvicorn

from app import catalog, config, db, notify, orders
from app.api import app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("main")


async def catalog_loop():
    while True:
        try:
            await catalog.refresh_products(force=True)
        except Exception as e:
            log.warning("تعذّر تحديث الكتالوج: %s", e)
        await asyncio.sleep(config.CATALOG_REFRESH_SEC)


async def keep_alive():
    """يطلب رابط الخادم العام كل 10 دقائق حتى لا تُنيمه الاستضافة المجانية (Render)."""
    if not config.PUBLIC_URL.startswith("https://"):
        return
    import httpx
    async with httpx.AsyncClient(timeout=20) as http:
        while True:
            await asyncio.sleep(600)
            try:
                await http.get(config.PUBLIC_URL + "/health")
            except Exception as e:
                log.warning("keep-alive: %s", e)


async def run_bot():
    if not config.BOT_TOKEN or not config.RUN_BOT:
        log.warning("البوت غير مفعّل (BOT_TOKEN فارغ أو RUN_BOT=0) — يعمل التطبيق المصغّر فقط")
        return
    from aiogram import Bot
    from app.bot import setup
    bot = Bot(config.BOT_TOKEN)
    notify.bot = bot
    dp = await setup(bot)
    me = await bot.get_me()
    log.info("البوت يعمل: @%s", me.username)
    await dp.start_polling(bot, handle_signals=False)


async def main():
    await db.connect()
    port = int(__import__("os").getenv("PORT", config.PORT))
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=port, log_level="info"))
    await asyncio.gather(server.serve(), run_bot(), orders.poll_loop(), catalog_loop(), keep_alive())


if __name__ == "__main__":
    asyncio.run(main())
