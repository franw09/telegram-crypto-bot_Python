"""
Модуль для работы с базой данных SQLite.
Хранит историю расчётов валют для каждого пользователя.
"""

import aiosqlite
from typing import List, Dict, Optional
from datetime import datetime


# Путь к файлу базы данных
DB_PATH = "currency_bot.db"


async def init_db() -> None:
    """
    Инициализирует базу данных и создаёт таблицу истории, если её нет.

    Таблица user_history хранит:
    - id: уникальный идентификатор записи
    - user_id: Telegram ID пользователя
    - created_at: дата и время расчёта (ISO 8601 формат)
    - currency: код валюты (USD, EUR, BTC и т.д.)
    - amount: исходное количество валюты
    - rate: курс на момент расчёта
    - result_rub: результат расчёта в рублях
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount TEXT NOT NULL,
                rate TEXT NOT NULL,
                result_rub TEXT NOT NULL
            )
        """)

        # Создаём индекс для ускорения поиска по user_id
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id
            ON user_history(user_id)
        """)

        await db.commit()


async def add_history(
    user_id: int,
    currency: str,
    amount: str,
    rate: str,
    result_rub: str
) -> None:
    """
    Добавляет запись о расчёте в историю пользователя.

    Args:
        user_id: Telegram ID пользователя
        currency: Код валюты (USD, EUR, BTC и т.д.)
        amount: Количество валюты в виде строки
        rate: Курс на момент расчёта в виде строки
        result_rub: Результат в рублях в виде строки
    """
    # Получаем текущее время в формате ISO 8601
    created_at = datetime.now().isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO user_history (user_id, created_at, currency, amount, rate, result_rub)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, created_at, currency, amount, rate, result_rub)
        )
        await db.commit()


async def get_history(
    user_id: int,
    page: int = 1,
    page_size: int = 5
) -> List[Dict[str, str]]:
    """
    Получает историю расчётов пользователя с пагинацией.

    Args:
        user_id: Telegram ID пользователя
        page: Номер страницы (начинается с 1)
        page_size: Количество записей на странице (по умолчанию 5)

    Returns:
        List[Dict]: Список словарей с записями истории от новых к старым
        Пустой список, если записей нет или номер страницы некорректен

    Структура каждой записи:
    {
        'id': int,
        'created_at': str,
        'currency': str,
        'amount': str,
        'rate': str,
        'result_rub': str
    }
    """
    # Проверяем корректность номера страницы
    if page < 1:
        return []

    # Вычисляем offset
    offset = (page - 1) * page_size

    async with aiosqlite.connect(DB_PATH) as db:
        # Включаем возврат строк как словарей
        db.row_factory = aiosqlite.Row

        async with db.execute(
            """
            SELECT id, created_at, currency, amount, rate, result_rub
            FROM user_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
            """,
            (user_id, page_size, offset)
        ) as cursor:
            rows = await cursor.fetchall()

            # Преобразуем Row объекты в словари
            return [dict(row) for row in rows]


async def get_history_count(user_id: int) -> int:
    """
    Получает общее количество записей в истории пользователя.

    Args:
        user_id: Telegram ID пользователя

    Returns:
        int: Количество записей в истории
    """
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            """
            SELECT COUNT(*) as count
            FROM user_history
            WHERE user_id = ?
            """,
            (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0


async def get_total_pages(user_id: int, page_size: int = 5) -> int:
    """
    Вычисляет общее количество страниц для истории пользователя.

    Args:
        user_id: Telegram ID пользователя
        page_size: Количество записей на странице

    Returns:
        int: Количество страниц (минимум 1, если есть хотя бы одна запись)
    """
    count = await get_history_count(user_id)

    if count == 0:
        return 0

    # Вычисляем количество страниц с округлением вверх
    return (count + page_size - 1) // page_size
