"""Проверка ИИН Республики Казахстан (чистая логика, без Django).

ИИН — 12 цифр: ГГММДД + цифра века/пола + 4 цифры + контрольная цифра.
Контрольная цифра считается по модулю 11 с двумя наборами весов.
"""
from django.utils.translation import gettext_lazy as _
from dataclasses import dataclass
from datetime import date

IIN_LENGTH = 12
WEIGHTS_FIRST = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
WEIGHTS_SECOND = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)
CENTURY_BY_DIGIT = {1: 1800, 2: 1800, 3: 1900, 4: 1900, 5: 2000, 6: 2000}


class IINError(ValueError):
    """ИИН не прошёл проверку."""


@dataclass(frozen=True)
class IINInfo:
    birth_date: date
    is_male: bool


def checksum(digits: list[int]) -> int | None:
    """Контрольная цифра по первым 11 цифрам; None — такой ИИН не выдаётся."""
    total = sum(d * w for d, w in zip(digits, WEIGHTS_FIRST)) % 11
    if total == 10:
        total = sum(d * w for d, w in zip(digits, WEIGHTS_SECOND)) % 11
    return None if total == 10 else total


def parse_iin(raw: str) -> IINInfo:
    """Проверяет ИИН и возвращает дату рождения и пол. Бросает IINError."""
    value = (raw or "").strip()
    if len(value) != IIN_LENGTH or not value.isdigit():
        raise IINError(_("ИИН состоит ровно из 12 цифр"))
    digits = [int(c) for c in value]
    if checksum(digits[:11]) != digits[11]:
        raise IINError(_("Контрольная цифра ИИН не совпадает — проверьте номер"))
    century_digit = digits[6]
    if century_digit not in CENTURY_BY_DIGIT:
        raise IINError(_("Седьмая цифра ИИН должна быть от 1 до 6"))
    year = CENTURY_BY_DIGIT[century_digit] + int(value[0:2])
    try:
        born = date(year, int(value[2:4]), int(value[4:6]))
    except ValueError as exc:
        raise IINError(_("Первые 6 цифр ИИН — некорректная дата рождения")) from exc
    return IINInfo(birth_date=born, is_male=century_digit % 2 == 1)


def make_valid_iin(prefix11: str) -> str:
    """Дописывает контрольную цифру (для тестов и демо-данных)."""
    digits = [int(c) for c in prefix11]
    check = checksum(digits)
    if check is None:
        raise IINError(_("Для этого префикса ИИН не существует"))
    return prefix11 + str(check)


def mask_iin(value: str) -> str:
    """Маскирует ИИН для показа на экране: 0401••••••37."""
    return value[:4] + "•" * 6 + value[-2:] if len(value) == IIN_LENGTH else value
