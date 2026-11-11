"""
Модуль обработчиков Telegram-бота для проверки курсов валют.
Содержит логику работы с API ЦБ РФ и CoinGecko, парсинг пользовательского ввода.
"""

import re
import ssl
import logging
import os
import io
import asyncio
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta
from typing import Optional, Tuple, List
import xml.etree.ElementTree as ET

import certifi
import aiohttp
import matplotlib
matplotlib.use('Agg')  # Используем backend без GUI
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, BufferedInputFile
from aiogram.filters import CommandStart
from aiogram.exceptions import TelegramBadRequest

from keyboards import (
    get_main_menu,
    get_back_button,
    get_rates_menu,
    get_history_pagination,
    get_forecast_menu
)
from database import init_db, add_history, get_history, get_total_pages

# Настройка логирования
logger = logging.getLogger(__name__)

# Создаём роутер для регистрации обработчиков
router = Router()

# Тайм-аут для HTTP-запросов (10 секунд)
TIMEOUT = aiohttp.ClientTimeout(total=10)

# SSL-контекст с правильными сертификатами
SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())


# ==================== Вспомогательные функции валидации ====================

def validate_amount(amount: Decimal) -> Tuple[bool, Optional[str]]:
    """
    Валидирует сумму для конвертации.

    Args:
        amount: Сумма для проверки

    Returns:
        Tuple[bool, Optional[str]]: (валидна, сообщение об ошибке)
    """
    if amount < 0:
        return False, "❌ Сумма не может быть отрицательной."
    if amount == 0:
        return False, "❌ Сумма должна быть больше нуля."

    MAX_AMOUNT = Decimal('1000000000')  # Защита от DoS
    if amount > MAX_AMOUNT:
        return False, f"❌ Сумма слишком велика (максимум {MAX_AMOUNT:,.0f}).".replace(',', ' ')

    if abs(amount.as_tuple().exponent) > 8:
        return False, "❌ Слишком много знаков после запятой (максимум 8)."

    return True, None


def validate_rate(rate: Decimal, currency: str) -> Tuple[bool, Optional[str]]:
    """
    Валидирует курс валюты.

    Args:
        rate: Курс валюты
        currency: Код валюты

    Returns:
        Tuple[bool, Optional[str]]: (валиден, сообщение об ошибке)
    """
    if rate <= 0:
        return False, f"❌ Получен некорректный курс для {currency}."

    MAX_RATE = Decimal('100000000')
    if rate > MAX_RATE:
        return False, f"❌ Курс {currency} превышает допустимые значения."

    return True, None


async def retry_api_call(coro_func, max_retries: int = 3):
    """
    Повторяет API запрос при ошибках с exponential backoff.

    Args:
        coro_func: Корутина для выполнения (без аргументов)
        max_retries: Максимальное количество попыток

    Returns:
        Результат корутины или None при неудаче
    """
    for attempt in range(max_retries):
        try:
            return await coro_func()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            if attempt < max_retries - 1:
                delay = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(f"Попытка {attempt + 1}/{max_retries} не удалась: {e}. Повтор через {delay}с...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Все {max_retries} попытки исчерпаны: {e}")
                return None
    return None


# ==================== API: Центральный банк России ====================

async def get_cbr_rates() -> Optional[dict]:
    """
    Получает актуальные курсы валют от ЦБ РФ в формате XML.

    Returns:
        dict: Словарь {код_валюты: {'rate': Decimal, 'nominal': int, 'name': str}}
        None: Если произошла ошибка при запросе

    Пример структуры:
    {
        'USD': {'rate': Decimal('95.5'), 'nominal': 1, 'name': 'Доллар США'},
        'EUR': {'rate': Decimal('101.2'), 'nominal': 1, 'name': 'Евро'}
    }
    """
    url = "https://www.cbr.ru/scripts/XML_daily.asp"

    try:
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(timeout=TIMEOUT, connector=connector) as session:
            async with session.get(url) as response:
                # Проверяем статус ответа
                if response.status == 429:
                    logger.error("ЦБ РФ API: превышен лимит запросов (429)")
                    return None

                if response.status != 200:
                    logger.error(f"ЦБ РФ API вернул статус {response.status}")
                    return None

                # Читаем XML
                xml_text = await response.text(encoding='windows-1251')

                # Проверяем, что ответ не пустой
                if not xml_text or len(xml_text) < 10:
                    logger.error("ЦБ РФ API вернул пустой или слишком короткий ответ")
                    return None

                root = ET.fromstring(xml_text)

                rates = {}

                # Парсим каждую валюту с проверкой каждого элемента
                for valute in root.findall('Valute'):
                    try:
                        # Получаем все необходимые элементы
                        char_code_elem = valute.find('CharCode')
                        name_elem = valute.find('Name')
                        nominal_elem = valute.find('Nominal')
                        value_elem = valute.find('Value')

                        # Проверяем, что все элементы существуют
                        if not all([char_code_elem, name_elem, nominal_elem, value_elem]):
                            logger.warning("Пропущена валюта с неполными данными")
                            continue

                        # Проверяем, что у элементов есть текстовое содержимое
                        char_code = char_code_elem.text
                        name = name_elem.text
                        nominal_text = nominal_elem.text
                        value_text = value_elem.text

                        if not all([char_code, name, nominal_text, value_text]):
                            logger.warning(f"Пропущена валюта {char_code} с пустыми значениями")
                            continue

                        # Парсим числовые значения
                        nominal = int(nominal_text)
                        # Курс может быть с запятой как десятичный разделитель
                        value_text_normalized = value_text.replace(',', '.')
                        rate = Decimal(value_text_normalized)

                        # Валидация курса
                        is_valid, error_msg = validate_rate(rate, char_code)
                        if not is_valid:
                            logger.warning(f"Некорректный курс для {char_code}: {error_msg}")
                            continue

                        rates[char_code] = {
                            'rate': rate,
                            'nominal': nominal,
                            'name': name
                        }

                    except (ValueError, InvalidOperation) as e:
                        logger.warning(f"Ошибка парсинга числовых значений валюты: {e}")
                        continue
                    except Exception as e:
                        logger.warning(f"Ошибка обработки валюты: {e}")
                        continue

                # Проверяем, что удалось получить хотя бы одну валюту
                if not rates:
                    logger.error("Не удалось распарсить ни одной валюты из ответа ЦБ РФ")
                    return None

                # Получаем дату из атрибута корневого элемента
                date_str = root.attrib.get('Date', '')

                return {
                    'rates': rates,
                    'date': date_str
                }

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка сети при запросе к ЦБ РФ: {e}")
        return None
    except ET.ParseError as e:
        logger.error(f"Ошибка парсинга XML от ЦБ РФ: {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при запросе к ЦБ РФ: {e}")
        return None


# ==================== API: CoinGecko ====================

async def get_btc_rate() -> Optional[dict]:
    """
    Получает текущий курс Bitcoin в рублях через CoinGecko API.

    Returns:
        dict: {'rate': Decimal, 'timestamp': int}
        None: Если произошла ошибка
    """
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=rub"

    try:
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(timeout=TIMEOUT, connector=connector) as session:
            async with session.get(url) as response:
                # Проверяем статус ответа
                if response.status == 429:
                    logger.error("CoinGecko API: превышен лимит запросов (429)")
                    return None

                if response.status != 200:
                    logger.error(f"CoinGecko API вернул статус {response.status}")
                    return None

                # Получаем текст ответа для проверки
                response_text = await response.text()

                # Проверяем, что ответ не пустой
                if not response_text or len(response_text) < 10:
                    logger.error("CoinGecko API вернул пустой или слишком короткий ответ")
                    return None

                # Парсим JSON
                data = await response.json(content_type=None)

                # Проверяем структуру ответа
                if 'bitcoin' not in data or 'rub' not in data['bitcoin']:
                    logger.error("Неожиданная структура ответа от CoinGecko")
                    return None

                # Проверяем, что значение не None и не пустое
                btc_rub_value = data['bitcoin']['rub']
                if btc_rub_value is None:
                    logger.error("CoinGecko вернул None для курса BTC")
                    return None

                btc_rub = Decimal(str(btc_rub_value))

                # Валидация курса BTC
                is_valid, error_msg = validate_rate(btc_rub, 'BTC')
                if not is_valid:
                    logger.error(f"Некорректный курс BTC: {error_msg}")
                    return None

                return {
                    'rate': btc_rub,
                    'timestamp': int(datetime.now().timestamp())
                }

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка сети при запросе к CoinGecko: {e}")
        return None
    except (KeyError, ValueError, InvalidOperation) as e:
        logger.error(f"Ошибка обработки данных от CoinGecko: {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при запросе к CoinGecko: {e}")
        return None


# ==================== API: Исторические данные ====================

async def get_cbr_historical(currency_code: str, days: int = 365) -> Optional[list]:
    """
    Получает исторические курсы валюты от ЦБ РФ за указанное количество дней.

    Args:
        currency_code: Код валюты (USD, EUR, CNY и т.д.)
        days: Количество дней истории (по умолчанию 365)

    Returns:
        list: Список словарей [{'date': 'DD.MM.YYYY', 'rate': Decimal, 'nominal': int}, ...]
        None: Если произошла ошибка

    Примечание:
        API ЦБ РФ: https://www.cbr.ru/scripts/XML_dynamic.asp
        Параметры: date_req1, date_req2, VAL_NM_RQ (код валюты)
    """
    from datetime import timedelta

    # Коды валют ЦБ РФ для исторических данных
    CBR_CURRENCY_CODES = {
        'USD': 'R01235',
        'EUR': 'R01239',
        'CNY': 'R01375',
        'GBP': 'R01035',
        'KZT': 'R01335',
        'TRY': 'R01700'
    }

    if currency_code not in CBR_CURRENCY_CODES:
        logger.error(f"Валюта {currency_code} не поддерживается для исторических данных ЦБ РФ")
        return None

    val_code = CBR_CURRENCY_CODES[currency_code]

    # Вычисляем даты для запроса
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)

    date_req1 = start_date.strftime('%d/%m/%Y')
    date_req2 = end_date.strftime('%d/%m/%Y')

    url = f"https://www.cbr.ru/scripts/XML_dynamic.asp?date_req1={date_req1}&date_req2={date_req2}&VAL_NM_RQ={val_code}"

    try:
        # Увеличенный тайм-аут для исторических данных (30 секунд)
        historical_timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(timeout=historical_timeout, connector=connector) as session:
            async with session.get(url) as response:
                # Проверяем статус ответа
                if response.status == 429:
                    logger.error("ЦБ РФ исторический API: превышен лимит запросов (429)")
                    return None

                if response.status != 200:
                    logger.error(f"ЦБ РФ исторический API вернул статус {response.status}")
                    return None

                # Читаем XML
                xml_text = await response.text(encoding='windows-1251')

                # Проверяем, что ответ не пустой
                if not xml_text or len(xml_text) < 10:
                    logger.error("ЦБ РФ исторический API вернул пустой или слишком короткий ответ")
                    return None

                root = ET.fromstring(xml_text)

                historical_data = []

                # Парсим каждую запись с проверкой всех элементов
                for record in root.findall('Record'):
                    try:
                        # Получаем дату из атрибута
                        date_str = record.attrib.get('Date', '')
                        if not date_str:
                            logger.warning("Пропущена запись без даты")
                            continue

                        # Получаем элементы
                        nominal_elem = record.find('Nominal')
                        value_elem = record.find('Value')

                        # Проверяем, что элементы существуют и содержат текст
                        if nominal_elem is None or value_elem is None:
                            logger.warning(f"Пропущена запись {date_str} с отсутствующими элементами")
                            continue

                        nominal_text = nominal_elem.text
                        value_text = value_elem.text

                        if not nominal_text or not value_text:
                            logger.warning(f"Пропущена запись {date_str} с пустыми значениями")
                            continue

                        # Парсим числовые значения
                        nominal = int(nominal_text)
                        value_text_normalized = value_text.replace(',', '.')
                        rate = Decimal(value_text_normalized)

                        # Валидация курса
                        is_valid, error_msg = validate_rate(rate, currency_code)
                        if not is_valid:
                            logger.warning(f"Некорректный курс для {currency_code} на {date_str}: {error_msg}")
                            continue

                        historical_data.append({
                            'date': date_str,
                            'rate': rate,
                            'nominal': nominal
                        })

                    except (ValueError, InvalidOperation) as e:
                        logger.warning(f"Ошибка парсинга числовых значений исторической записи: {e}")
                        continue
                    except Exception as e:
                        logger.warning(f"Ошибка обработки исторической записи: {e}")
                        continue

                return historical_data if historical_data else None

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка сети при запросе исторических данных ЦБ РФ: {e}")
        return None
    except ET.ParseError as e:
        logger.error(f"Ошибка парсинга XML исторических данных ЦБ РФ: {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при запросе исторических данных ЦБ РФ: {e}")
        return None


async def get_btc_historical(days: int = 365) -> Optional[list]:
    """
    Получает исторические курсы Bitcoin в рублях через CoinGecko API.

    Args:
        days: Количество дней истории (по умолчанию 365)

    Returns:
        list: Список словарей [{'date': 'YYYY-MM-DD', 'rate': Decimal}, ...]
        None: Если произошла ошибка

    Примечание:
        API CoinGecko: https://api.coingecko.com/api/v3/coins/bitcoin/market_chart
        Параметр days может быть: 1, 7, 14, 30, 90, 180, 365, max
    """
    url = f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=rub&days={days}"

    try:
        # Увеличенный тайм-аут для исторических данных (30 секунд)
        historical_timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(ssl=SSL_CONTEXT)
        async with aiohttp.ClientSession(timeout=historical_timeout, connector=connector) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logger.error(f"CoinGecko исторический API вернул статус {response.status}")
                    return None

                data = await response.json()

                # Проверяем структуру ответа
                if 'prices' not in data:
                    logger.error("Неожиданная структура ответа от CoinGecko (исторические данные)")
                    return None

                historical_data = []

                # Парсим данные: prices - это массив [timestamp_ms, price]
                for timestamp_ms, price in data['prices']:
                    # Конвертируем timestamp в дату
                    date_obj = datetime.fromtimestamp(timestamp_ms / 1000)
                    date_str = date_obj.strftime('%Y-%m-%d')

                    historical_data.append({
                        'date': date_str,
                        'rate': Decimal(str(price))
                    })

                return historical_data if historical_data else None

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка сети при запросе исторических данных BTC: {e}")
        return None
    except (KeyError, ValueError) as e:
        logger.error(f"Ошибка обработки исторических данных BTC: {e}")
        return None
    except Exception as e:
        logger.error(f"Неожиданная ошибка при запросе исторических данных BTC: {e}")
        return None


# ==================== Графики и прогноз ====================

async def generate_currency_chart(currency_code: str, days: int = 365) -> Optional[io.BytesIO]:
    """
    Генерирует график курса валюты за указанный период с прогнозом на год.

    Args:
        currency_code: Код валюты (USD, EUR, CNY, GBP, KZT, TRY, BTC)
        days: Количество дней истории (по умолчанию 365)

    Returns:
        io.BytesIO: Буфер с изображением графика в формате PNG
        None: Если произошла ошибка
    """
    try:
        # Получаем исторические данные
        if currency_code == 'BTC':
            historical_data = await get_btc_historical(days=days)
        else:
            historical_data = await get_cbr_historical(currency_code, days=days)

        if not historical_data:
            logger.error(f"Нет исторических данных для {currency_code}")
            return None

        # Подготавливаем данные для графика
        dates = []
        rates = []

        for record in historical_data:
            # Парсим дату
            if currency_code == 'BTC':
                date_obj = datetime.strptime(record['date'], '%Y-%m-%d')
            else:
                date_obj = datetime.strptime(record['date'], '%d.%m.%Y')

            dates.append(date_obj)

            # Получаем курс с учётом номинала
            if 'nominal' in record:
                rate = float(record['rate']) / record['nominal']
            else:
                rate = float(record['rate'])

            rates.append(rate)

        # Для BTC агрегируем по дням (берём последнее значение дня)
        if currency_code == 'BTC' and len(dates) > 100:
            daily_data = {}
            for date, rate in zip(dates, rates):
                date_key = date.date()
                daily_data[date_key] = rate

            dates = [datetime.combine(d, datetime.min.time()) for d in sorted(daily_data.keys())]
            rates = [daily_data[d.date()] for d in dates]

        # Расчёт прогноза
        forecast_dates, forecast_rates = calculate_forecast(dates, rates, forecast_days=365)

        # Построение графика
        plt.figure(figsize=(12, 6))
        plt.style.use('seaborn-v0_8-darkgrid')

        # График исторических данных
        plt.plot(dates, rates, label='Исторические данные', color='#2E86AB', linewidth=2)

        # График прогноза
        if forecast_dates and forecast_rates:
            plt.plot(forecast_dates, forecast_rates, label='Прогноз (линейная регрессия)',
                    color='#A23B72', linestyle='--', linewidth=2, alpha=0.7)

        # Настройка осей и подписей
        currency_names = {
            'USD': 'Доллар США',
            'EUR': 'Евро',
            'CNY': 'Китайский юань',
            'GBP': 'Фунт стерлингов',
            'KZT': 'Казахстанский тенге',
            'TRY': 'Турецкая лира',
            'BTC': 'Bitcoin'
        }

        currency_name = currency_names.get(currency_code, currency_code)
        plt.title(f'Курс {currency_name} ({currency_code}) к рублю', fontsize=14, fontweight='bold')
        plt.xlabel('Дата', fontsize=12)
        plt.ylabel('Курс (₽)', fontsize=12)
        plt.legend(loc='best', fontsize=10)
        plt.grid(True, alpha=0.3)

        # Форматирование дат на оси X
        ax = plt.gca()
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m.%Y'))
        ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        plt.xticks(rotation=45, ha='right')

        plt.tight_layout()

        # Сохраняем график в буфер
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, bbox_inches='tight')
        buf.seek(0)
        plt.close()

        return buf

    except Exception as e:
        logger.error(f"Ошибка при генерации графика для {currency_code}: {e}")
        import traceback
        traceback.print_exc()
        return None


def calculate_forecast(dates: List[datetime], rates: List[float], forecast_days: int = 365) -> Tuple[List[datetime], List[float]]:
    """
    Рассчитывает прогноз курса на основе линейной регрессии.

    Args:
        dates: Список дат исторических данных
        rates: Список курсов
        forecast_days: Количество дней для прогноза

    Returns:
        Tuple[List[datetime], List[float]]: Даты и курсы прогноза
    """
    try:
        if len(dates) < 10:
            logger.warning("Недостаточно данных для прогноза")
            return [], []

        # Преобразуем даты в числовой формат (дни от первой даты)
        base_date = dates[0]
        x = np.array([(d - base_date).days for d in dates])
        y = np.array(rates)

        # Линейная регрессия: y = a*x + b
        coefficients = np.polyfit(x, y, 1)
        a, b = coefficients

        # Генерируем прогноз
        last_date = dates[-1]
        forecast_dates = [last_date + timedelta(days=i) for i in range(1, forecast_days + 1)]
        forecast_x = np.array([(d - base_date).days for d in forecast_dates])
        forecast_rates = a * forecast_x + b

        # Ограничиваем прогноз разумными пределами (не допускаем отрицательных значений)
        forecast_rates = np.maximum(forecast_rates, 0.01)

        return forecast_dates, forecast_rates.tolist()

    except Exception as e:
        logger.error(f"Ошибка при расчёте прогноза: {e}")
        return [], []


# ==================== Вспомогательные функции ====================

def format_rate(rate: Decimal, nominal: int = 1) -> str:
    """
    Форматирует курс валюты для отображения.

    Args:
        rate: Курс валюты
        nominal: Номинал (за сколько единиц указан курс)

    Returns:
        str: Отформатированная строка курса

    Примеры:
        format_rate(Decimal('95.5'), 1) -> '95.50 ₽'
        format_rate(Decimal('550'), 10) -> '55.00 ₽'
    """
    # Если курс указан не за 1 единицу, пересчитываем
    if nominal != 1:
        rate = rate / nominal

    return f"{rate:.2f} ₽"


# ==================== Обработчики команд ====================

@router.message(CommandStart())
async def cmd_start(message: Message):
    """
    Обработчик команды /start.
    Показывает приветствие и главное меню с инлайн-кнопками.
    """
    await message.answer(
        "👋 Привет! Я бот для проверки курсов валют.\n\n"
        "Выберите действие:",
        reply_markup=get_main_menu()
    )


# ==================== Обработчики callback-кнопок ====================

@router.callback_query(F.data == "back")
async def back_to_menu(callback: CallbackQuery):
    """
    Обработчик кнопки «Назад» - возврат в главное меню.
    """
    try:
        await callback.message.edit_text(
            "Выберите действие:",
            reply_markup=get_main_menu()
        )
    except TelegramBadRequest:
        # Если не удалось отредактировать, отправляем новое сообщение
        await callback.message.answer(
            "Выберите действие:",
            reply_markup=get_main_menu()
        )

    await callback.answer()


@router.callback_query(F.data == "rates")
async def show_rates(callback: CallbackQuery):
    """
    Обработчик кнопки «📊 Курсы валют».
    Показывает актуальные курсы USD, EUR, CNY, GBP, KZT, TRY и BTC.
    """
    # Сначала показываем индикатор загрузки
    await callback.answer("⏳ Загружаю курсы...")

    # Получаем данные параллельно
    cbr_data = await get_cbr_rates()
    btc_data = await get_btc_rate()

    # Формируем ответ
    response_lines = ["📊 <b>Актуальные курсы валют</b>\n"]

    # Обрабатываем фиатные валюты
    if cbr_data and 'rates' in cbr_data:
        rates = cbr_data['rates']
        date_str = cbr_data.get('date', 'неизвестно')

        # USD
        if 'USD' in rates:
            usd = rates['USD']
            response_lines.append(
                f"💵 1 USD = {format_rate(usd['rate'], usd['nominal'])}"
            )

        # EUR
        if 'EUR' in rates:
            eur = rates['EUR']
            response_lines.append(
                f"💶 1 EUR = {format_rate(eur['rate'], eur['nominal'])}"
            )

        # CNY
        if 'CNY' in rates:
            cny = rates['CNY']
            response_lines.append(
                f"🇨🇳 1 CNY = {format_rate(cny['rate'], cny['nominal'])}"
            )

        # GBP
        if 'GBP' in rates:
            gbp = rates['GBP']
            response_lines.append(
                f"💷 1 GBP = {format_rate(gbp['rate'], gbp['nominal'])}"
            )

        # KZT
        if 'KZT' in rates:
            kzt = rates['KZT']
            response_lines.append(
                f"🇰🇿 1 KZT = {format_rate(kzt['rate'], kzt['nominal'])}"
            )

        # TRY
        if 'TRY' in rates:
            try_curr = rates['TRY']
            response_lines.append(
                f"🇹🇷 1 TRY = {format_rate(try_curr['rate'], try_curr['nominal'])}"
            )

        response_lines.append(f"\n📅 Данные ЦБ РФ от {date_str}")
    else:
        response_lines.append("❌ Не удалось получить курсы фиатных валют от ЦБ РФ.")

    # Обрабатываем Bitcoin
    response_lines.append("")  # Пустая строка для разделения

    if btc_data:
        btc_rate = btc_data['rate']
        response_lines.append(
            f"₿ 1 BTC = {btc_rate:,.2f} ₽".replace(',', ' ')
        )

        # Время получения данных
        timestamp = btc_data['timestamp']
        time_str = datetime.fromtimestamp(timestamp).strftime('%H:%M:%S')
        response_lines.append(f"🕐 Данные CoinGecko от {time_str}")
    else:
        response_lines.append("❌ Не удалось получить курс Bitcoin от CoinGecko.")

    try:
        await callback.message.edit_text(
            "\n".join(response_lines),
            parse_mode="HTML",
            reply_markup=get_rates_menu()
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "\n".join(response_lines),
            parse_mode="HTML",
            reply_markup=get_rates_menu()
        )


@router.callback_query(F.data == "calculate")
async def start_calculation(callback: CallbackQuery):
    """
    Обработчик кнопки «🔎 Точный расчёт».
    Предлагает пользователю ввести валюту и сумму.
    """
    try:
        await callback.message.edit_text(
            "🔎 <b>Точный расчёт</b>\n\n"
            "Введите валюту и сумму в любом формате:\n\n"
            "Примеры:\n"
            "• <code>100$</code> или <code>$100</code>\n"
            "• <code>50 EUR</code> или <code>50 евро</code>\n"
            "• <code>100 юаней</code> или <code>100 CNY</code>\n"
            "• <code>0.5 BTC</code>\n"
            "• <code>доллар</code> (курс за 1 единицу)\n\n"
            "Поддерживаемые валюты: USD, EUR, CNY, GBP, KZT, TRY, BTC, RUB",
            parse_mode="HTML",
            reply_markup=get_back_button()
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "🔎 <b>Точный расчёт</b>\n\n"
            "Введите валюту и сумму в любом формате:\n\n"
            "Примеры:\n"
            "• <code>100$</code> или <code>$100</code>\n"
            "• <code>50 EUR</code> или <code>50 евро</code>\n"
            "• <code>100 юаней</code> или <code>100 CNY</code>\n"
            "• <code>0.5 BTC</code>\n"
            "• <code>доллар</code> (курс за 1 единицу)\n\n"
            "Поддерживаемые валюты: USD, EUR, CNY, GBP, KZT, TRY, BTC, RUB",
            parse_mode="HTML",
            reply_markup=get_back_button()
        )

    await callback.answer()


@router.callback_query(F.data == "history")
async def show_history(callback: CallbackQuery):
    """
    Обработчик кнопки «📜 История» - показывает первую страницу истории.
    """
    await show_history_page(callback, page=1)


@router.callback_query(F.data.startswith("history_page:"))
async def history_page_handler(callback: CallbackQuery):
    """
    Обработчик пагинации истории.
    """
    # Извлекаем номер страницы из callback_data
    page = int(callback.data.split(":")[1])
    await show_history_page(callback, page)


async def show_history_page(callback: CallbackQuery, page: int):
    """
    Отображает страницу истории расчётов пользователя.

    Args:
        callback: CallbackQuery объект
        page: Номер страницы
    """
    user_id = callback.from_user.id

    # Получаем историю
    history = await get_history(user_id, page=page, page_size=5)
    total_pages = await get_total_pages(user_id, page_size=5)

    if not history:
        text = "📜 <b>История расчётов</b>\n\n"
        if page == 1:
            text += "История пуста. Выполните расчёт через раздел «🔎 Точный расчёт»."
        else:
            text += "На этой странице нет записей."

        try:
            await callback.message.edit_text(
                text,
                parse_mode="HTML",
                reply_markup=get_back_button()
            )
        except TelegramBadRequest:
            await callback.message.answer(
                text,
                parse_mode="HTML",
                reply_markup=get_back_button()
            )
        await callback.answer()
        return

    # Формируем текст истории
    lines = [f"📜 <b>История расчётов</b> (стр. {page}/{total_pages})\n"]

    # Получаем актуальные курсы для сравнения
    cbr_data = await get_cbr_rates()
    btc_data = await get_btc_rate()

    for i, record in enumerate(history, 1):
        currency = record['currency']
        amount = record['amount']
        old_rate = record['rate']
        old_result = record['result_rub']
        created_at = datetime.fromisoformat(record['created_at']).strftime('%d.%m.%Y %H:%M')

        lines.append(f"{i}. <b>{amount} {currency}</b>")
        lines.append(f"   📅 {created_at}")
        lines.append(f"   Тогда: {old_result} ₽")

        # Рассчитываем текущую стоимость
        current_result = None

        if currency == 'RUB':
            current_result = amount
        elif currency == 'BTC' and btc_data:
            current_rate = btc_data['rate']
            current_result = Decimal(amount) * current_rate
        elif cbr_data and 'rates' in cbr_data:
            rates = cbr_data['rates']
            if currency in rates:
                curr_info = rates[currency]
                rate_per_unit = curr_info['rate'] / curr_info['nominal']
                current_result = Decimal(amount) * rate_per_unit

        if current_result is not None:
            lines.append(f"   Сейчас: {current_result:.2f} ₽")

        lines.append("")  # Пустая строка между записями

    # Кнопки пагинации
    has_prev = page > 1
    has_next = page < total_pages

    try:
        await callback.message.edit_text(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=get_history_pagination(page, total_pages, has_prev, has_next)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "\n".join(lines),
            parse_mode="HTML",
            reply_markup=get_history_pagination(page, total_pages, has_prev, has_next)
        )

    await callback.answer()


@router.callback_query(F.data == "current_page")
async def current_page_handler(callback: CallbackQuery):
    """
    Обработчик неактивной кнопки с номером текущей страницы.
    """
    await callback.answer("Вы уже на этой странице")


@router.callback_query(F.data == "forecast")
async def show_forecast_menu(callback: CallbackQuery):
    """
    Обработчик кнопки «📈 График и прогноз».
    Показывает меню выбора валюты для прогноза.
    """
    try:
        await callback.message.edit_text(
            "📈 <b>График и прогноз на год</b>\n\n"
            "Выберите валюту для просмотра графика:\n\n"
            "График покажет историю курса за последние 12 месяцев "
            "и прогноз на следующие 12 месяцев.",
            parse_mode="HTML",
            reply_markup=get_forecast_menu()
        )
    except TelegramBadRequest:
        await callback.message.answer(
            "📈 <b>График и прогноз на год</b>\n\n"
            "Выберите валюту для просмотра графика:\n\n"
            "График покажет историю курса за последние 12 месяцев "
            "и прогноз на следующие 12 месяцев.",
            parse_mode="HTML",
            reply_markup=get_forecast_menu()
        )

    await callback.answer()


@router.callback_query(F.data.startswith("forecast:"))
async def generate_forecast_chart(callback: CallbackQuery):
    """
    Обработчик выбора валюты для построения графика с прогнозом.
    """
    # Извлекаем код валюты из callback_data
    currency_code = callback.data.split(":")[1]

    await callback.answer("⏳ Генерирую график...")

    # Отправляем сообщение о загрузке
    loading_msg = await callback.message.answer(
        f"⏳ Загружаю данные и строю график для {currency_code}...\n"
        "Это может занять несколько секунд."
    )

    try:
        # Генерируем график
        chart_buffer = await generate_currency_chart(currency_code, days=365)

        if chart_buffer:
            # Формируем описание
            currency_names = {
                'USD': 'Доллара США',
                'EUR': 'Евро',
                'CNY': 'Китайского юаня',
                'GBP': 'Фунта стерлингов',
                'KZT': 'Казахстанского тенге',
                'TRY': 'Турецкой лиры',
                'BTC': 'Bitcoin'
            }

            currency_name = currency_names.get(currency_code, currency_code)

            caption = (
                f"📈 <b>График курса {currency_name}</b>\n\n"
                f"🔵 Синяя линия — история за последний год\n"
                f"🟣 Пунктирная линия — прогноз на следующий год\n\n"
                f"⚠️ <b>Важно:</b> Прогноз приблизительный, построен на линейной "
                f"регрессии по историческим данным и не гарантирует будущий курс. "
                f"Реальный курс зависит от множества экономических и политических факторов."
            )

            # Удаляем сообщение о загрузке
            try:
                await loading_msg.delete()
            except TelegramBadRequest:
                # Если не удалось удалить, игнорируем
                pass

            # Создаём BufferedInputFile из BytesIO для отправки
            photo = BufferedInputFile(
                chart_buffer.getvalue(),
                filename=f"chart_{currency_code}.png"
            )

            # Отправляем график
            await callback.message.answer_photo(
                photo=photo,
                caption=caption,
                parse_mode="HTML"
            )

            # Закрываем буфер
            chart_buffer.close()

        else:
            # Если график не сгенерирован, обновляем сообщение о загрузке
            try:
                await loading_msg.edit_text(
                    f"❌ Не удалось построить график для {currency_code}.\n\n"
                    "Возможные причины:\n"
                    "• API временно недоступен\n"
                    "• Недостаточно исторических данных\n\n"
                    "Попробуйте позже или выберите другую валюту.",
                    reply_markup=get_forecast_menu()
                )
            except TelegramBadRequest:
                # Если сообщение было удалено, отправляем новое
                await callback.message.answer(
                    f"❌ Не удалось построить график для {currency_code}.\n\n"
                    "Возможные причины:\n"
                    "• API временно недоступен\n"
                    "• Недостаточно исторических данных\n\n"
                    "Попробуйте позже или выберите другую валюту.",
                    reply_markup=get_forecast_menu()
                )

    except Exception as e:
        logger.error(f"Ошибка при генерации графика: {e}")
        try:
            await loading_msg.edit_text(
                "❌ Произошла ошибка при построении графика.\n\n"
                "Попробуйте позже или выберите другую валюту.",
                reply_markup=get_forecast_menu()
            )
        except TelegramBadRequest:
            # Если сообщение было удалено, отправляем новое
            await callback.message.answer(
                "❌ Произошла ошибка при построении графика.\n\n"
                "Попробуйте позже или выберите другую валюту.",
                reply_markup=get_forecast_menu()
            )


# ==================== Парсинг пользовательского ввода ====================

# Словарь для нормализации валют
CURRENCY_MAP = {
    # USD
    'usd': 'USD',
    'доллар': 'USD',
    'доллары': 'USD',
    'долларов': 'USD',
    'бакс': 'USD',
    'баксов': 'USD',
    '$': 'USD',

    # EUR
    'eur': 'EUR',
    'euro': 'EUR',
    'евро': 'EUR',
    '€': 'EUR',

    # CNY
    'cny': 'CNY',
    'юань': 'CNY',
    'юаней': 'CNY',
    '¥': 'CNY',

    # GBP
    'gbp': 'GBP',
    'фунт': 'GBP',
    'фунтов': 'GBP',
    'фунт стерлингов': 'GBP',
    '£': 'GBP',

    # KZT
    'kzt': 'KZT',
    'тенге': 'KZT',

    # TRY
    'try': 'TRY',
    'лира': 'TRY',
    'лир': 'TRY',
    'турецкая лира': 'TRY',
    '₺': 'TRY',

    # BTC
    'btc': 'BTC',
    'bitcoin': 'BTC',
    'биткоин': 'BTC',
    '₿': 'BTC',

    # RUB
    'rub': 'RUB',
    'rur': 'RUB',
    'руб': 'RUB',
    'рубль': 'RUB',
    'рубли': 'RUB',
    'рублей': 'RUB',
    '₽': 'RUB',
}


def parse_currency_input(text: str) -> Optional[Tuple[Optional[Decimal], str]]:
    """
    Парсит пользовательский ввод и извлекает сумму и валюту.

    Args:
        text: Текст от пользователя

    Returns:
        Tuple[Optional[Decimal], str]: (сумма, код_валюты) или None если не удалось распарсить
        - сумма может быть None если указана только валюта
        - код валюты всегда в верхнем регистре (USD, EUR и т.д.)

    Примеры:
        parse_currency_input("100$") -> (Decimal('100'), 'USD')
        parse_currency_input("доллар") -> (None, 'USD')
        parse_currency_input("50 евро") -> (Decimal('50'), 'EUR')
    """
    text = text.strip().lower()

    # Заменяем запятую на точку для десятичных чисел
    text = text.replace(',', '.')

    # Паттерн для извлечения числа и валюты
    # Поддерживает: "100$", "$100", "100 USD", "USD 100", "100 долларов"
    patterns = [
        r'^([\d.]+)\s*([^\d\s]+)$',  # "100$" или "100 usd"
        r'^([^\d\s]+)\s*([\d.]+)$',  # "$100" или "usd 100"
        r'^([\d.]+)\s+([а-яё]+)$',   # "100 долларов"
        r'^([а-яё]+)\s+([\d.]+)$',   # "долларов 100"
        r'^([^\d\s]+)$',             # Только валюта: "$" или "доллар"
    ]

    amount = None
    currency_str = None

    for pattern in patterns:
        match = re.match(pattern, text)
        if match:
            groups = match.groups()

            # Определяем, что является числом, а что валютой
            for group in groups:
                # Пытаемся распознать число
                try:
                    amount = Decimal(group)
                except (InvalidOperation, ValueError):
                    # Это не число, значит валюта
                    if currency_str is None:
                        currency_str = group

            if currency_str:
                break

    # Если не нашли валюту, возвращаем None
    if not currency_str:
        return None

    # Нормализуем валюту
    currency_str = currency_str.strip()
    currency_code = CURRENCY_MAP.get(currency_str)

    if not currency_code:
        return None

    return (amount, currency_code)


# ==================== Обработчик текстового ввода ====================

@router.message(F.text)
async def handle_text_input(message: Message):
    """
    Обработчик произвольного текстового ввода.
    Пытается распарсить валюту и сумму, затем выполняет расчёт.
    """
    text = message.text

    # Пытаемся распарсить ввод
    parsed = parse_currency_input(text)

    if not parsed:
        await message.answer(
            "❌ Не удалось распознать валюту.\n\n"
            "Попробуйте ещё раз. Примеры:\n"
            "• <code>100$</code>\n"
            "• <code>50 евро</code>\n"
            "• <code>доллар</code>",
            parse_mode="HTML"
        )
        return

    amount, currency_code = parsed

    # Если не указана сумма, используем 1
    if amount is None:
        amount = Decimal('1')

    # Валидация суммы
    is_valid, error_msg = validate_amount(amount)
    if not is_valid:
        await message.answer(error_msg)
        return

    # Отправляем сообщение о загрузке
    loading_msg = await message.answer("⏳ Рассчитываю...")

    # Получаем актуальные курсы
    result_text = ""
    rate_for_db = None
    result_for_db = None

    if currency_code == 'RUB':
        # Рубль не требует конвертации
        result_text = f"{amount} RUB = {amount:.2f} ₽"
        rate_for_db = "1"
        result_for_db = str(amount)
    elif currency_code == 'BTC':
        # Получаем курс Bitcoin
        btc_data = await get_btc_rate()

        if btc_data:
            btc_rate = btc_data['rate']

            # Дополнительная валидация курса (на случай обхода в get_btc_rate)
            is_valid, error_msg = validate_rate(btc_rate, 'BTC')
            if not is_valid:
                result_text = error_msg
            else:
                result_rub = amount * btc_rate

                result_text = (
                    f"₿ {amount} BTC = <b>{result_rub:,.2f} ₽</b>\n\n"
                    f"Курс: 1 BTC = {btc_rate:,.2f} ₽\n"
                    f"Источник: CoinGecko"
                ).replace(',', ' ')

                rate_for_db = str(btc_rate)
                result_for_db = str(result_rub)
        else:
            result_text = "❌ Не удалось получить курс Bitcoin."
    else:
        # Фиатные валюты от ЦБ РФ
        cbr_data = await get_cbr_rates()

        if cbr_data and 'rates' in cbr_data:
            rates = cbr_data['rates']

            if currency_code in rates:
                currency_info = rates[currency_code]
                rate = currency_info['rate']
                nominal = currency_info['nominal']
                name = currency_info['name']

                # Рассчитываем стоимость в рублях
                # rate указан за nominal единиц, поэтому:
                rate_per_unit = rate / nominal

                # Дополнительная валидация курса за единицу
                is_valid, error_msg = validate_rate(rate_per_unit, currency_code)
                if not is_valid:
                    result_text = error_msg
                else:
                    result_rub = amount * rate_per_unit

                    # Символы валют
                    currency_symbols = {
                        'USD': '💵',
                        'EUR': '💶',
                        'CNY': '🇨🇳',
                        'GBP': '💷',
                        'KZT': '🇰🇿',
                        'TRY': '🇹🇷'
                    }

                    symbol = currency_symbols.get(currency_code, '')

                    result_text = (
                        f"{symbol} {amount} {currency_code} = <b>{result_rub:.2f} ₽</b>\n\n"
                        f"Курс: 1 {currency_code} = {rate_per_unit:.2f} ₽\n"
                        f"Источник: ЦБ РФ от {cbr_data.get('date', 'неизвестно')}\n\n"
                        f"⚠️ Официальный курс ЦБ РФ может отличаться от курса "
                        f"покупки/продажи в банках и обменниках."
                    )

                    rate_for_db = str(rate_per_unit)
                    result_for_db = str(result_rub)
            else:
                result_text = f"❌ Валюта {currency_code} не найдена в данных ЦБ РФ."
        else:
            result_text = "❌ Не удалось получить курсы валют."

    # Сохраняем в историю только успешные расчёты
    if rate_for_db and result_for_db:
        try:
            await add_history(
                user_id=message.from_user.id,
                currency=currency_code,
                amount=str(amount),
                rate=rate_for_db,
                result_rub=result_for_db
            )
        except Exception as e:
            logger.error(f"Ошибка при сохранении истории: {e}")

    # Удаляем сообщение о загрузке
    await loading_msg.delete()

    # Отправляем результат
    await message.answer(result_text, parse_mode="HTML")

    # Пытаемся удалить сообщение пользователя
    try:
        await message.delete()
    except TelegramBadRequest:
        # Если нет прав на удаление, просто игнорируем
        pass
