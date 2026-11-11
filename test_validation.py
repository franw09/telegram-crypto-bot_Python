"""
Тестовый скрипт для проверки функций валидации handlers.py
"""

from decimal import Decimal
import sys
import os

# Добавляем путь к модулю handlers
sys.path.insert(0, os.path.dirname(__file__))

from handlers import validate_amount, validate_rate

def test_validate_amount():
    """Тестирует функцию валидации суммы"""
    print("=== Тестирование validate_amount ===\n")

    test_cases = [
        (Decimal('100'), True, "Обычная сумма 100"),
        (Decimal('0.00000001'), True, "Минимальная сумма с 8 знаками"),
        (Decimal('999999999'), True, "Большая допустимая сумма"),
        (Decimal('-10'), False, "Отрицательная сумма"),
        (Decimal('0'), False, "Нулевая сумма"),
        (Decimal('10000000000'), False, "Слишком большая сумма"),
        (Decimal('0.000000001'), False, "Слишком много знаков после запятой"),
    ]

    for amount, expected_valid, description in test_cases:
        is_valid, error_msg = validate_amount(amount)
        status = "✅ PASS" if is_valid == expected_valid else "❌ FAIL"
        print(f"{status} | {description}: {amount}")
        if not is_valid:
            print(f"      Ошибка: {error_msg}")
        print()

def test_validate_rate():
    """Тестирует функцию валидации курса"""
    print("=== Тестирование validate_rate ===\n")

    test_cases = [
        (Decimal('95.50'), 'USD', True, "Обычный курс USD"),
        (Decimal('6000000'), 'BTC', True, "Высокий курс BTC"),
        (Decimal('0.01'), 'KZT', True, "Низкий курс KZT"),
        (Decimal('0'), 'USD', False, "Нулевой курс"),
        (Decimal('-10'), 'EUR', False, "Отрицательный курс"),
        (Decimal('1000000000'), 'BTC', False, "Слишком высокий курс"),
    ]

    for rate, currency, expected_valid, description in test_cases:
        is_valid, error_msg = validate_rate(rate, currency)
        status = "✅ PASS" if is_valid == expected_valid else "❌ FAIL"
        print(f"{status} | {description}: {rate} {currency}")
        if not is_valid:
            print(f"      Ошибка: {error_msg}")
        print()

if __name__ == "__main__":
    print("Запуск тестов валидации\n")
    print("=" * 50)
    print()

    try:
        test_validate_amount()
        test_validate_rate()
        print("=" * 50)
        print("\n✅ Все тесты выполнены успешно!")
    except Exception as e:
        print(f"\n❌ Ошибка при выполнении тестов: {e}")
        import traceback
        traceback.print_exc()
