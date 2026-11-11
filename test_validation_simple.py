"""
Упрощенный тест для проверки логики валидации
"""

from decimal import Decimal
from typing import Optional, Tuple


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

    passed = 0
    failed = 0

    for amount, expected_valid, description in test_cases:
        is_valid, error_msg = validate_amount(amount)
        if is_valid == expected_valid:
            status = "[PASS]"
            passed += 1
        else:
            status = "[FAIL]"
            failed += 1

        result_text = "Valid" if is_valid else "Invalid"
        print(f"{status} | {description}: {amount} -> {result_text}")
        print()

    return passed, failed


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

    passed = 0
    failed = 0

    for rate, currency, expected_valid, description in test_cases:
        is_valid, error_msg = validate_rate(rate, currency)
        if is_valid == expected_valid:
            status = "[PASS]"
            passed += 1
        else:
            status = "[FAIL]"
            failed += 1

        result_text = "Valid" if is_valid else "Invalid"
        print(f"{status} | {description}: {rate} {currency} -> {result_text}")
        print()

    return passed, failed


if __name__ == "__main__":
    print("Запуск тестов валидации\n")
    print("=" * 50)
    print()

    try:
        passed_amount, failed_amount = test_validate_amount()
        passed_rate, failed_rate = test_validate_rate()

        total_passed = passed_amount + passed_rate
        total_failed = failed_amount + failed_rate

        print("=" * 50)
        print(f"\nResults:")
        print(f"  [OK] Passed: {total_passed}")
        print(f"  [!!] Failed: {total_failed}")

        if total_failed == 0:
            print("\n[SUCCESS] All tests passed!")
        else:
            print(f"\n[WARNING] Found problems: {total_failed}")

    except Exception as e:
        print(f"\n[ERROR] Test execution failed: {e}")
        import traceback
        traceback.print_exc()
