"""
Комплексный набор автотестов для CryptoBot.
Тестирует валидацию, работу с БД, парсинг и API-интеграции.
"""

import pytest
import asyncio
import aiosqlite
import os
import sys
from decimal import Decimal
from datetime import datetime
from unittest.mock import AsyncMock, patch, MagicMock
import xml.etree.ElementTree as ET

# Добавляем путь к корню проекта
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from handlers import (
    validate_amount,
    validate_rate,
    parse_currency_input,
    get_cbr_rates,
    get_btc_rate,
    get_cbr_historical,
    get_btc_historical,
    format_rate
)
from database import (
    init_db,
    add_history,
    get_history,
    get_history_count,
    get_total_pages,
    DB_PATH
)


# ==================== Фикстуры ====================

@pytest.fixture
def temp_db_path(tmp_path):
    """Создает временный путь к БД для тестов."""
    return str(tmp_path / "test_currency_bot.db")


@pytest.fixture
async def test_db(temp_db_path, monkeypatch):
    """Создает временную тестовую БД."""
    # Подменяем путь к БД
    monkeypatch.setattr('database.DB_PATH', temp_db_path)

    # Инициализируем БД
    async with aiosqlite.connect(temp_db_path) as db:
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
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_user_id
            ON user_history(user_id)
        """)
        await db.commit()

    yield temp_db_path

    # Очистка после теста
    if os.path.exists(temp_db_path):
        os.remove(temp_db_path)


# ==================== Тесты валидации ====================

class TestValidation:
    """Тесты функций валидации."""

    def test_validate_amount_positive_normal(self):
        """Тест валидации обычной положительной суммы."""
        is_valid, error = validate_amount(Decimal('100'))
        assert is_valid is True
        assert error is None

    def test_validate_amount_minimum(self):
        """Тест валидации минимальной суммы с 8 знаками."""
        is_valid, error = validate_amount(Decimal('0.00000001'))
        assert is_valid is True
        assert error is None

    def test_validate_amount_large_valid(self):
        """Тест валидации большой допустимой суммы."""
        is_valid, error = validate_amount(Decimal('999999999'))
        assert is_valid is True
        assert error is None

    def test_validate_amount_negative(self):
        """Тест валидации отрицательной суммы."""
        is_valid, error = validate_amount(Decimal('-10'))
        assert is_valid is False
        assert "отрицательной" in error.lower()

    def test_validate_amount_zero(self):
        """Тест валидации нулевой суммы."""
        is_valid, error = validate_amount(Decimal('0'))
        assert is_valid is False
        assert "больше нуля" in error.lower()

    def test_validate_amount_too_large(self):
        """Тест валидации слишком большой суммы."""
        is_valid, error = validate_amount(Decimal('10000000000'))
        assert is_valid is False
        assert "слишком велика" in error.lower()

    def test_validate_amount_too_many_decimals(self):
        """Тест валидации суммы с слишком большим количеством знаков."""
        is_valid, error = validate_amount(Decimal('0.000000001'))
        assert is_valid is False
        assert "знаков после запятой" in error.lower()

    def test_validate_rate_positive_usd(self):
        """Тест валидации обычного курса USD."""
        is_valid, error = validate_rate(Decimal('95.50'), 'USD')
        assert is_valid is True
        assert error is None

    def test_validate_rate_high_btc(self):
        """Тест валидации высокого курса BTC."""
        is_valid, error = validate_rate(Decimal('6000000'), 'BTC')
        assert is_valid is True
        assert error is None

    def test_validate_rate_low_kzt(self):
        """Тест валидации низкого курса KZT."""
        is_valid, error = validate_rate(Decimal('0.01'), 'KZT')
        assert is_valid is True
        assert error is None

    def test_validate_rate_zero(self):
        """Тест валидации нулевого курса."""
        is_valid, error = validate_rate(Decimal('0'), 'USD')
        assert is_valid is False
        assert "некорректный курс" in error.lower()

    def test_validate_rate_negative(self):
        """Тест валидации отрицательного курса."""
        is_valid, error = validate_rate(Decimal('-10'), 'EUR')
        assert is_valid is False
        assert "некорректный курс" in error.lower()

    def test_validate_rate_too_high(self):
        """Тест валидации слишком высокого курса."""
        is_valid, error = validate_rate(Decimal('1000000000'), 'BTC')
        assert is_valid is False
        assert "превышает допустимые значения" in error.lower()


# ==================== Тесты работы с БД ====================

class TestDatabase:
    """Тесты работы с базой данных."""

    @pytest.mark.asyncio
    async def test_init_db(self, temp_db_path, monkeypatch):
        """Тест инициализации базы данных."""
        monkeypatch.setattr('database.DB_PATH', temp_db_path)

        await init_db()

        # Проверяем, что БД создана
        assert os.path.exists(temp_db_path)

        # Проверяем, что таблица существует
        async with aiosqlite.connect(temp_db_path) as db:
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='user_history'"
            ) as cursor:
                result = await cursor.fetchone()
                assert result is not None

    @pytest.mark.asyncio
    async def test_add_history(self, test_db, monkeypatch):
        """Тест добавления записи в историю."""
        monkeypatch.setattr('database.DB_PATH', test_db)

        user_id = 123456
        currency = 'USD'
        amount = '100'
        rate = '95.50'
        result_rub = '9550.00'

        await add_history(user_id, currency, amount, rate, result_rub)

        # Проверяем, что запись добавлена
        async with aiosqlite.connect(test_db) as db:
            async with db.execute(
                "SELECT * FROM user_history WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row[1] == user_id  # user_id
                assert row[3] == currency  # currency
                assert row[4] == amount  # amount

    @pytest.mark.asyncio
    async def test_get_history_empty(self, test_db, monkeypatch):
        """Тест получения пустой истории."""
        monkeypatch.setattr('database.DB_PATH', test_db)

        history = await get_history(user_id=999999, page=1)
        assert history == []

    @pytest.mark.asyncio
    async def test_get_history_with_data(self, test_db, monkeypatch):
        """Тест получения истории с данными."""
        monkeypatch.setattr('database.DB_PATH', test_db)

        user_id = 123456

        # Добавляем несколько записей
        for i in range(3):
            await add_history(
                user_id, 'USD', f'{100 + i}', '95.50', f'{9550 + i * 95.50}'
            )

        history = await get_history(user_id, page=1, page_size=5)

        assert len(history) == 3
        assert history[0]['currency'] == 'USD'

    @pytest.mark.asyncio
    async def test_get_history_pagination(self, test_db, monkeypatch):
        """Тест пагинации истории."""
        monkeypatch.setattr('database.DB_PATH', test_db)

        user_id = 123456

        # Добавляем 10 записей
        for i in range(10):
            await add_history(
                user_id, 'USD', f'{100 + i}', '95.50', f'{9550 + i * 95.50}'
            )

        # Первая страница (5 записей)
        page1 = await get_history(user_id, page=1, page_size=5)
        assert len(page1) == 5

        # Вторая страница (5 записей)
        page2 = await get_history(user_id, page=2, page_size=5)
        assert len(page2) == 5

        # Третья страница (пустая)
        page3 = await get_history(user_id, page=3, page_size=5)
        assert len(page3) == 0

    @pytest.mark.asyncio
    async def test_get_history_count(self, test_db, monkeypatch):
        """Тест подсчета записей в истории."""
        monkeypatch.setattr('database.DB_PATH', test_db)

        user_id = 123456

        # Изначально 0 записей
        count = await get_history_count(user_id)
        assert count == 0

        # Добавляем 5 записей
        for i in range(5):
            await add_history(
                user_id, 'USD', f'{100 + i}', '95.50', f'{9550 + i * 95.50}'
            )

        count = await get_history_count(user_id)
        assert count == 5

    @pytest.mark.asyncio
    async def test_get_total_pages(self, test_db, monkeypatch):
        """Тест расчета общего количества страниц."""
        monkeypatch.setattr('database.DB_PATH', test_db)

        user_id = 123456

        # Пустая история - 0 страниц
        total = await get_total_pages(user_id, page_size=5)
        assert total == 0

        # 3 записи - 1 страница
        for i in range(3):
            await add_history(
                user_id, 'USD', f'{100 + i}', '95.50', f'{9550 + i * 95.50}'
            )
        total = await get_total_pages(user_id, page_size=5)
        assert total == 1

        # 7 записей - 2 страницы
        for i in range(4):
            await add_history(
                user_id, 'EUR', f'{50 + i}', '101.20', f'{5060 + i * 101.20}'
            )
        total = await get_total_pages(user_id, page_size=5)
        assert total == 2


# ==================== Тесты парсинга ====================

class TestParsing:
    """Тесты парсинга пользовательского ввода."""

    def test_parse_currency_dollar_symbol(self):
        """Тест парсинга с символом доллара."""
        result = parse_currency_input("100$")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('100')
        assert currency == 'USD'

    def test_parse_currency_symbol_before_amount(self):
        """Тест парсинга с символом перед суммой."""
        result = parse_currency_input("$50")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('50')
        assert currency == 'USD'

    def test_parse_currency_with_code(self):
        """Тест парсинга с кодом валюты."""
        result = parse_currency_input("75 EUR")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('75')
        assert currency == 'EUR'

    def test_parse_currency_russian_word(self):
        """Тест парсинга с русским названием валюты."""
        result = parse_currency_input("100 долларов")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('100')
        assert currency == 'USD'

    def test_parse_currency_euro_word(self):
        """Тест парсинга евро."""
        result = parse_currency_input("50 евро")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('50')
        assert currency == 'EUR'

    def test_parse_currency_yuan(self):
        """Тест парсинга юаня."""
        result = parse_currency_input("200 юаней")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('200')
        assert currency == 'CNY'

    def test_parse_currency_btc(self):
        """Тест парсинга Bitcoin."""
        result = parse_currency_input("0.5 BTC")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('0.5')
        assert currency == 'BTC'

    def test_parse_currency_only_currency_name(self):
        """Тест парсинга только названия валюты без суммы."""
        result = parse_currency_input("доллар")
        assert result is not None
        amount, currency = result
        assert amount is None
        assert currency == 'USD'

    def test_parse_currency_only_symbol(self):
        """Тест парсинга только символа валюты."""
        result = parse_currency_input("€")
        assert result is not None
        amount, currency = result
        assert amount is None
        assert currency == 'EUR'

    def test_parse_currency_decimal_comma(self):
        """Тест парсинга с запятой в десятичной дроби."""
        result = parse_currency_input("10,5 USD")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('10.5')
        assert currency == 'USD'

    def test_parse_currency_rub(self):
        """Тест парсинга рублей."""
        result = parse_currency_input("1000 руб")
        assert result is not None
        amount, currency = result
        assert amount == Decimal('1000')
        assert currency == 'RUB'

    def test_parse_currency_invalid(self):
        """Тест парсинга некорректного ввода."""
        result = parse_currency_input("hello world")
        assert result is None

    def test_parse_currency_no_currency(self):
        """Тест парсинга только числа без валюты."""
        result = parse_currency_input("100")
        assert result is None


# ==================== Тесты API (с моками) ====================

class TestAPIIntegration:
    """Интеграционные тесты API с моками."""

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Сложный тест с моками aiohttp - требует доработки")
    async def test_get_cbr_rates_success(self):
        """
        Тест успешного получения курсов ЦБ РФ.

        Примечание: Этот тест пропущен, т.к. требует сложной настройки моков
        для aiohttp с TCPConnector и SSL-контекстом. В реальном проекте
        рекомендуется использовать библиотеку aioresponses для мокирования HTTP-запросов.
        """
        pass

    @pytest.mark.asyncio
    async def test_get_cbr_rates_network_error(self):
        """Тест обработки ошибки сети при запросе к ЦБ РФ."""
        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = await get_cbr_rates()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cbr_rates_invalid_xml(self):
        """Тест обработки невалидного XML от ЦБ РФ."""
        invalid_xml = "This is not XML"

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=invalid_xml)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_cbr_rates()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cbr_rates_empty_response(self):
        """Тест обработки пустого ответа от ЦБ РФ."""
        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value="")
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_cbr_rates()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cbr_rates_status_429(self):
        """Тест обработки превышения лимита запросов (429)."""
        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 429
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_cbr_rates()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_btc_rate_success(self):
        """Тест успешного получения курса Bitcoin."""
        mock_json = {
            "bitcoin": {
                "rub": 6000000.0
            }
        }

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value='{"bitcoin":{"rub":6000000.0}}')
            mock_response.json = AsyncMock(return_value=mock_json)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_btc_rate()

            assert result is not None
            assert 'rate' in result
            assert result['rate'] == Decimal('6000000.0')

    @pytest.mark.asyncio
    async def test_get_btc_rate_network_error(self):
        """Тест обработки ошибки сети при запросе к CoinGecko."""
        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = await get_btc_rate()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_btc_rate_invalid_structure(self):
        """Тест обработки невалидной структуры данных от CoinGecko."""
        mock_json = {"invalid": "data"}

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value='{"invalid":"data"}')
            mock_response.json = AsyncMock(return_value=mock_json)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_btc_rate()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_btc_rate_null_value(self):
        """Тест обработки null значения курса от CoinGecko."""
        mock_json = {"bitcoin": {"rub": None}}

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value='{"bitcoin":{"rub":null}}')
            mock_response.json = AsyncMock(return_value=mock_json)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_btc_rate()

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cbr_historical_success(self):
        """Тест успешного получения исторических данных ЦБ РФ."""
        mock_xml = """<?xml version="1.0" encoding="windows-1251"?>
        <ValCurs ID="R01235" DateRange1="11.10.2026" DateRange2="11.11.2026">
            <Record Date="11.10.2026" Id="R01235">
                <Nominal>1</Nominal>
                <Value>94,50</Value>
            </Record>
            <Record Date="12.10.2026" Id="R01235">
                <Nominal>1</Nominal>
                <Value>95,00</Value>
            </Record>
        </ValCurs>
        """

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=mock_xml)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_cbr_historical('USD', days=30)

            assert result is not None
            assert len(result) == 2
            assert result[0]['date'] == '11.10.2026'
            assert result[0]['rate'] == Decimal('94.50')

    @pytest.mark.asyncio
    async def test_get_cbr_historical_empty_response(self):
        """Тест обработки пустого ответа исторических данных ЦБ РФ."""
        mock_xml = """<?xml version="1.0" encoding="windows-1251"?>
        <ValCurs ID="R01235" DateRange1="11.10.2026" DateRange2="11.11.2026">
        </ValCurs>
        """

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.text = AsyncMock(return_value=mock_xml)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_cbr_historical('USD', days=30)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_cbr_historical_unsupported_currency(self):
        """Тест запроса неподдерживаемой валюты для исторических данных."""
        result = await get_cbr_historical('XXX', days=30)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_btc_historical_success(self):
        """Тест успешного получения исторических данных Bitcoin."""
        mock_json = {
            "prices": [
                [1697040000000, 5900000.0],
                [1697126400000, 6000000.0],
                [1697212800000, 6100000.0]
            ]
        }

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_json)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_btc_historical(days=30)

            assert result is not None
            assert len(result) == 3
            assert 'date' in result[0]
            assert 'rate' in result[0]

    @pytest.mark.asyncio
    async def test_get_btc_historical_invalid_structure(self):
        """Тест обработки невалидной структуры исторических данных BTC."""
        mock_json = {"invalid": "data"}

        with patch('aiohttp.ClientSession.get') as mock_get:
            mock_response = AsyncMock()
            mock_response.status = 200
            mock_response.json = AsyncMock(return_value=mock_json)
            mock_get.return_value.__aenter__.return_value = mock_response

            result = await get_btc_historical(days=30)

            assert result is None


# ==================== Тесты вспомогательных функций ====================

class TestHelpers:
    """Тесты вспомогательных функций."""

    def test_format_rate_simple(self):
        """Тест форматирования курса с номиналом 1."""
        result = format_rate(Decimal('95.50'), 1)
        assert result == "95.50 ₽"

    def test_format_rate_with_nominal(self):
        """Тест форматирования курса с номиналом 10."""
        result = format_rate(Decimal('550'), 10)
        assert result == "55.00 ₽"

    def test_format_rate_high_value(self):
        """Тест форматирования высокого курса."""
        result = format_rate(Decimal('6000000'), 1)
        assert result == "6000000.00 ₽"


# ==================== Настройка pytest ====================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
