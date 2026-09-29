from datetime import date
from decimal import Decimal

from sg_report import formatting as fmt

NBSP = "\u00a0"


def test_money_groups_thousands_with_french_separators():
    assert fmt.money(Decimal("1234.5")) == f"1{NBSP}234,50{NBSP}€"
    assert fmt.money(Decimal("-1234567.891")) == f"-1{NBSP}234{NBSP}567,89{NBSP}€"
    assert fmt.money(Decimal("12"), signed=True) == f"+12,00{NBSP}€"
    assert fmt.money(Decimal("-0.001"), signed=True) == f"0,00{NBSP}€"
    assert fmt.money(Decimal("611.6"), decimals=0) == f"612{NBSP}€"
    assert fmt.money(Decimal("5"), "USD") == f"5,00{NBSP}$"


def test_percent_rounds_half_up_and_signs():
    assert fmt.percent(Decimal("0.185")) == f"+19{NBSP}%"
    assert fmt.percent(Decimal("-0.084")) == f"-8{NBSP}%"
    assert fmt.percent(Decimal("0.31"), signed=False) == f"31{NBSP}%"


def test_dates_in_french():
    assert fmt.long_date(date(2026, 10, 1)) == "1er octobre 2026"
    assert fmt.short_date(date(2026, 9, 22)) == "mar. 22 sept."
    assert fmt.numeric_date(date(2026, 9, 2)) == "02/09/2026"


def test_period_wording():
    assert fmt.period(date(2026, 9, 21), date(2026, 9, 27)) == "du 21 au 27 septembre 2026"
    assert fmt.period(date(2026, 9, 28), date(2026, 10, 4)) == "du 28 septembre au 4 octobre 2026"
    assert (
        fmt.period(date(2025, 12, 29), date(2026, 1, 4)) == "du 29 décembre 2025 au 4 janvier 2026"
    )
    assert fmt.period(date(2026, 9, 28), date(2026, 10, 4), short=True) == "du 28 sept. au 4 oct."
    assert fmt.period(date(2026, 10, 1), date(2026, 10, 7), short=True) == "du 1er au 7 oct."
