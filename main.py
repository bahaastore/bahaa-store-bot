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


async def run_bot():
    if not config.BOT_TOKEN:
        log.warning("لا يوجد BOT_TOKEN — يعمل التطبيق المصغّر فقط (وضع التجربة)")
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
    server = uvicorn.Server(uvicorn.Config(app, host=config.HOST, port=config.PORT, log_level="info"))
    await asyncio.gather(server.serve(), run_bot(), orders.poll_loop(), catalog_loop())


if __name__ == "__main__":
    asyncio.run(main())
