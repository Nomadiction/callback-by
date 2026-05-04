from __future__ import annotations

import re

VALID_OPERATOR_CODES = {"25", "29", "33", "44"}


class PhoneError(ValueError):
    pass


def _digits_only(raw: str) -> str:
    return re.sub(r"\D", "", raw or "")


def normalize(raw: str) -> str:
    """Return canonical 12-char form: +375XXXXXXXXX. Raises PhoneError on bad input."""
    digits = _digits_only(raw)
    if digits.startswith("80"):
        digits = "375" + digits[2:]
    elif digits.startswith("375"):
        pass
    elif len(digits) == 9:
        digits = "375" + digits
    else:
        raise PhoneError(f"Не удалось распознать номер: {raw!r}")

    if len(digits) != 12 or not digits.startswith("375"):
        raise PhoneError(f"Длина номера некорректна: {digits}")

    operator = digits[3:5]
    if operator not in VALID_OPERATOR_CODES:
        raise PhoneError(f"Неизвестный код оператора: {operator}")

    return "+" + digits


def variants(raw: str) -> list[str]:
    """Возвращает разные форматы одного номера для подстройки под input-маски сайтов."""
    canonical = normalize(raw)
    digits = canonical[1:]              # 375XXXXXXXXX
    op = digits[3:5]
    p1, p2, p3 = digits[5:8], digits[8:10], digits[10:12]
    return [
        canonical,                              # +375XXXXXXXXX
        f"+375 {op} {p1}-{p2}-{p3}",            # +375 29 123-45-67
        f"+375 ({op}) {p1}-{p2}-{p3}",          # +375 (29) 123-45-67
        f"80 {op} {p1} {p2} {p3}",              # 80 29 123 45 67
        f"{op}{p1}{p2}{p3}",                    # 291234567 (для масок без кода)
    ]
