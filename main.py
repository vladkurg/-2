import asyncio
import logging
import os
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import database as db
from handlers_client import router as client_router
from handlers_admin import router as admin_router
from news_generator import generate_news

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")


async def publish_news(bot: Bot):
    """Generate and publish news to channel every 3 days"""
    if not CHANNEL_ID:
        logger.warning("CHANNEL_ID not set, skipping news publish")
        return
    try:
        news_text = await asyncio.get_event_loop().run_in_executor(None, lambda: __import__('asyncio').run(generate_news()))
        await bot.send_message(int(CHANNEL_ID), f"📰 <b>Полезное от Стирка ковров Донецк</b>\n\n{news_text}", parse_mode="HTML")
        logger.info("News published to channel")
    except Exception as e:
        logger.error(f"Failed to publish news: {e}")


async def publish_news_async(bot: Bot):
    try:
        news_text = await generate_news()
        await bot.send_message(int(CHANNEL_ID), f"📰 <b>Полезное от Стирка ковров Донецк</b>\n\n{news_text}", parse_mode="HTML")
        logger.info("News published to channel")
    except Exception as e:
        logger.error(f"Failed to publish news: {e}")


async def main():
    await db.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(admin_router)
    dp.include_router(client_router)

    # Scheduler for news every 3 days
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        publish_news_async,
        "interval",
        days=3,
        args=[bot],
        id="news_job"
    )
    scheduler.start()

    logger.info("Bot started!")
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
