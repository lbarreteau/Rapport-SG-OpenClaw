"""Formats français : montants, pourcentages et dates."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

NBSP = "\u00a0"
MONTHS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip
MONTHS_SHORT = (
    "janv.", "févr.", "mars", "avr.", "mai", "juin",
    "juil.", "août", "sept.", "oct.", "nov.", "déc.",
)  # fmt: skip
WEEKDAYS_SHORT = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}


def money(
    amount: Decimal | int, currency: str = "EUR", *, signed: bool = False, decimals: int = 2
) -> str:
    """Decimal("-1234.5") -> « -1 234,50 € » (espaces insécables)."""
    exponent = Decimal(1).scaleb(-decimals)
    value = Decimal(amount).quantize(exponent, rounding=ROUND_HALF_UP)
    sign = "-" if value < 0 else ("+" if signed and value > 0 else "")
    units, _, cents = f"{abs(value):.{decimals}f}".partition(".")
    groups = []
    while len(units) > 3:
        groups.insert(0, units[-3:])
        units = units[:-3]
    groups.insert(0, units)
    number = NBSP.join(groups) + (f",{cents}" if cents else "")
    return f"{sign}{number}{NBSP}{CURRENCY_SYMBOLS.get(currency, currency)}"


def percent(ratio: Decimal | float, *, signed: bool = True) -> str:
    value = int((Decimal(str(ratio)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value}{NBSP}%"


def _day(d: date) -> str:
    return "1er" if d.day == 1 else str(d.day)


def long_date(d: date) -> str:
    """« 1er octobre 2026 »."""
    return f"{_day(d)} {MONTHS[d.month - 1]} {d.year}"


def short_date(d: date) -> str:
    """« mar. 22 sept. »."""
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS_SHORT[d.month - 1]}"


def numeric_date(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def period(start: date, end: date, *, short: bool = False) -> str:
    """« du 21 au 27 septembre 2026 », « du 28 sept. au 4 oct. » (short)."""
    months = MONTHS_SHORT if short else MONTHS
    end_text = f"{_day(end)} {months[end.month - 1]}" + ("" if short else f" {end.year}")
    if start.year != end.year:
        start_text = f"{_day(start)} {months[start.month - 1]} {start.year}"
        if short:
            end_text += f" {end.year}"
    elif start.month != end.month:
        start_text = f"{_day(start)} {months[start.month - 1]}"
    else:
        start_text = _day(start)
    return f"du {start_text} au {end_text}"
