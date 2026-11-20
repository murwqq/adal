from datetime import date

import pytest

from accounts.iin import IINError, checksum, make_valid_iin, mask_iin, parse_iin


def valid(prefix7: str) -> str:
    """Действительный ИИН с заданными первыми 7 цифрами."""
    for serial in range(10000):
        candidate = f"{prefix7}{serial:04d}"
        if checksum([int(c) for c in candidate]) is not None:
            return make_valid_iin(candidate)
    raise AssertionError


def test_valid_iin_gives_birth_date_and_gender():
    info = parse_iin(valid("0403055"))
    assert info.birth_date == date(2004, 3, 5)
    assert info.is_male is True


def test_female_from_last_century():
    info = parse_iin(valid("6512154"))
    assert info.birth_date == date(1965, 12, 15)
    assert info.is_male is False


@pytest.mark.parametrize("raw", ["", "123", "12345678901a", "1234567890123"])
def test_wrong_format_rejected(raw):
    with pytest.raises(IINError, match="12 цифр"):
        parse_iin(raw)


def test_wrong_checksum_rejected():
    iin = valid("0403055")
    broken = iin[:11] + str((int(iin[11]) + 1) % 10)
    with pytest.raises(IINError, match="Контрольная"):
        parse_iin(broken)


def test_impossible_date_rejected():
    with pytest.raises(IINError, match="дату рождения"):
        parse_iin(valid("0413315"))


def test_bad_century_digit_rejected():
    with pytest.raises(IINError, match="Седьмая"):
        parse_iin(valid("0403059"))


def test_make_valid_iin_rejects_impossible_prefix():
    bad = next(f"0403055{s:04d}" for s in range(10000)
               if checksum([int(c) for c in f"0403055{s:04d}"]) is None)
    with pytest.raises(IINError):
        make_valid_iin(bad)


def test_mask():
    assert mask_iin("040305500001") == "0403••••••01"
    assert mask_iin("short") == "short"
