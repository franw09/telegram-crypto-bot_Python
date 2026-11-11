"""
Точка входа для Telegram-бота проверки курсов валют.
Загружает токен из .env, создаёт бота и диспетчер, запускает polling.
"""

import asyncio
import logging
import sys
from os import getenv

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from handlers import router
from database import init_db


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


async def main():
    """
    Главная функция запуска бота.
    """
    # Загружаем переменные окружения из .env
    load_dotenv()

    # Получаем токен бота
    bot_token = getenv("BOT_TOKEN")

    if not bot_token:
        logger.error("❌ Ошибка: BOT_TOKEN не найден в файле .env")
        logger.error("Убедитесь, что файл .env существует и содержит строку:")
        logger.error("BOT_TOKEN=ваш_токен_здесь")
        sys.exit(1)

    # Создаём бота с настройками по умолчанию
    bot = Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )

    # Создаём диспетчер
    dp = Dispatcher()

    # Подключаем роутер с обработчиками
    dp.include_router(router)

    # Инициализируем базу данных
    logger.info("Инициализация базы данных...")
    await init_db()
    logger.info("База данных готова")

    logger.info("🤖 Бот запущен и готов к работе!")
    logger.info("Нажмите Ctrl+C для остановки")

    try:
        # Удаляем вебхуки (на всякий случай) и запускаем polling
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        # Корректное закрытие сессии бота
        await bot.session.close()
        logger.info("✅ Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹️  Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при запуске: {e}")
        sys.exit(1)
