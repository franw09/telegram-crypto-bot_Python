"""
Модуль для создания клавиатур Telegram-бота.
Содержит функции для генерации InlineKeyboardMarkup с callback-кнопками.
"""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


def get_main_menu() -> InlineKeyboardMarkup:
    """
    Создаёт главное меню с четырьмя кнопками:
    - 📊 Курсы валют
    - 🔎 Точный расчёт
    - 📜 История
    - 📈 График и прогноз на год

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура главного меню
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📊 Курсы валют", callback_data="rates")],
            [InlineKeyboardButton(text="🔎 Точный расчёт", callback_data="calculate")],
            [InlineKeyboardButton(text="📜 История", callback_data="history")],
            [InlineKeyboardButton(text="📈 График и прогноз", callback_data="forecast")]
        ]
    )
    return keyboard


def get_back_button() -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру с одной кнопкой «⬅️ Назад» для возврата в главное меню.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопкой возврата
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
        ]
    )
    return keyboard


def get_history_pagination(page: int, total_pages: int, has_prev: bool, has_next: bool) -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для пагинации истории запросов.

    Args:
        page: Текущая страница (начинается с 1)
        total_pages: Общее количество страниц
        has_prev: Есть ли предыдущая страница
        has_next: Есть ли следующая страница

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопками навигации
    """
    buttons = []

    # Кнопки навигации по страницам
    nav_buttons = []

    if has_prev:
        nav_buttons.append(
            InlineKeyboardButton(text="⬅️ Назад", callback_data=f"history_page:{page - 1}")
        )

    # Показываем текущую страницу
    nav_buttons.append(
        InlineKeyboardButton(text=f"• {page}/{total_pages} •", callback_data="current_page")
    )

    if has_next:
        nav_buttons.append(
            InlineKeyboardButton(text="Вперёд ➡️", callback_data=f"history_page:{page + 1}")
        )

    if nav_buttons:
        buttons.append(nav_buttons)

    # Кнопка возврата в главное меню
    buttons.append([InlineKeyboardButton(text="🏠 Главное меню", callback_data="back")])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return keyboard


def get_rates_menu() -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для раздела «Курсы валют» с кнопкой возврата.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопкой назад
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить курсы", callback_data="rates")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
        ]
    )
    return keyboard


def get_forecast_menu() -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру для раздела «График и прогноз» с выбором валюты.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с выбором валюты
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💵 USD", callback_data="forecast:USD"),
                InlineKeyboardButton(text="💶 EUR", callback_data="forecast:EUR")
            ],
            [
                InlineKeyboardButton(text="🇨🇳 CNY", callback_data="forecast:CNY"),
                InlineKeyboardButton(text="₿ BTC", callback_data="forecast:BTC")
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back")]
        ]
    )
    return keyboard


def get_forecast_back_button() -> InlineKeyboardMarkup:
    """
    Создаёт клавиатуру с кнопками возврата для просмотра графика.

    Returns:
        InlineKeyboardMarkup: Инлайн-клавиатура с кнопками навигации
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📈 Выбрать другую валюту", callback_data="forecast")],
            [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back")]
        ]
    )
    return keyboard
