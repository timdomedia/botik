import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiohttp import web

from .bot import App, build_router
from .catalog import Catalog
from .config import load_settings
from .db import DB
from .sheets import Sheets
from .web import build_web


async def reminder_loop(app: App) -> None:
    """Раз в день пишет в чат, если есть неотправленные заказы."""
    hour = app.settings.reminder_hour
    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        if app.db.not_shipped():
            await app.bot.send_message(app.settings.chat_id, app.pending_text())


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    db = DB(settings.db_path)
    catalog = Catalog.load(settings.products_file)
    logging.info("catalog: %d products", len(catalog.products))
    sheets = Sheets(settings.sheet_id, settings.service_account_file, db, catalog, settings.ship_deadline_days)

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    app = App(bot, db, catalog, sheets, settings)
    dp = Dispatcher()
    dp.include_router(build_router(app))

    runner = web.AppRunner(build_web(app))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", settings.port).start()
    logging.info("tilda webhook listening on :%d/tilda", settings.port)

    if settings.reminder_hour is not None:
        asyncio.create_task(reminder_loop(app))
    asyncio.create_task(sheets.sync())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
