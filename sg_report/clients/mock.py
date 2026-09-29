"""Banque de démonstration : opérations fictives au format Enable Banking.

Les opérations d'un jour dépendent uniquement de sa date : deux synchronisations
successives renvoient les mêmes données, comme une vraie banque.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..models import Account, Balance, Session, Transaction
from .enable_banking import normalize_transactions

CURRENT = Account(
    uid="demo-courant",
    name="M ALEX MARTIN",
    iban="FR7630003012340005012345821",
    product="Compte courant",
    cash_account_type="CACC",
)
SAVINGS = Account(
    uid="demo-livret",
    name="M ALEX MARTIN",
    iban="FR7630003012340005099887734",
    product="Livret A",
    cash_account_type="SVGS",
)
DEMO_ACCOUNTS = (CURRENT, SAVINGS)
OPENING_BALANCES = {CURRENT.uid: Decimal("1650.00"), SAVINGS.uid: Decimal("7400.00")}
HISTORY_DAYS = 120
CARD = "X4821"
MONTHS = (
    "JANVIER", "FEVRIER", "MARS", "AVRIL", "MAI", "JUIN",
    "JUILLET", "AOUT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DECEMBRE",
)  # fmt: skip

GROCERIES = (
    ("CARREFOUR CITY PARIS 11", 8, 45),
    ("MONOPRIX PARIS", 15, 70),
    ("LIDL", 20, 85),
    ("PICARD", 12, 40),
    ("BIOCOOP BASTILLE", 10, 55),
    ("FRANPRIX", 5, 25),
)
RESTAURANTS = (
    ("LE PETIT BISTROT", 18, 45),
    ("UBER EATS", 15, 35),
    ("DELIVEROO", 14, 32),
    ("BIG MAMMA", 25, 60),
    ("STARBUCKS", 4, 9),
    ("O TACOS", 8, 14),
)
TRANSPORT = (
    ("UBER TRIP", 9, 28),
    ("SNCF INTERNET", 35, 120),
    ("TOTALENERGIES STATION", 40, 75),
)
SHOPPING = (
    ("AMAZON PAYMENTS", 12, 90),
    ("FNAC PARIS", 15, 150),
    ("DECATHLON", 20, 80),
    ("ZARA", 25, 90),
)
# (jour du mois, montant, libellé, contrepartie, paiement par carte)
MONTHLY_DEBITS = (
    (
        3,
        "950.00",
        "VIR PERM POUR: SCI DES TILLEULS MOTIF: LOYER {month}",
        "SCI DES TILLEULS",
        False,
    ),
    (5, "68.40", "PRLV SEPA EDF CLIENTS PARTICULIERS", "EDF", False),
    (6, "88.80", "PRLV SEPA COMUTITRES NAVIGO", "", False),
    (8, "19.99", "PRLV SEPA FREE MOBILE", "FREE MOBILE", False),
    (10, "13.49", "CARTE {card} {date} NETFLIX.COM", "", True),
    (12, "11.12", "CARTE {card} {date} SPOTIFY", "", True),
    (14, "38.72", "PRLV SEPA AXA FRANCE IARD", "AXA FRANCE IARD", False),
    (15, "8.90", "COTISATION JAZZ", "", False),
    (18, "29.99", "PRLV SEPA BASIC FIT", "BASIC FIT", False),
    (20, "52.30", "PRLV SEPA HARMONIE MUTUELLE", "HARMONIE MUTUELLE", False),
)
SALARY = Decimal("2850.00")
MONTHLY_SAVINGS = Decimal("200.00")


@dataclass(frozen=True)
class _Operation:
    account: Account
    amount: Decimal
    label: str
    counterparty: str = ""
    counterparty_iban: str = ""
    card: bool = False


def _amount(rng: random.Random, low: float, high: float) -> Decimal:
    return Decimal(f"{rng.uniform(low, high):.2f}")


def _operations_for_day(day: date) -> list[_Operation]:
    rng = random.Random(f"sg-report-demo:{day.isoformat()}")
    month = MONTHS[day.month - 1]
    ops: list[_Operation] = []

    def card(merchant: str, low: float, high: float) -> None:
        label = f"CARTE {CARD} {day:%d/%m} {merchant}"
        ops.append(_Operation(CURRENT, -_amount(rng, low, high), label, card=True))

    def credit(amount: Decimal, label: str, counterparty: str) -> None:
        ops.append(_Operation(CURRENT, amount, label, counterparty))

    for day_of_month, amount, template, counterparty, is_card in MONTHLY_DEBITS:
        if day.day == day_of_month:
            label = template.format(month=month, card=CARD, date=f"{day:%d/%m}")
            ops.append(_Operation(CURRENT, -Decimal(amount), label, counterparty, card=is_card))
    if day.day == 2:
        owner = CURRENT.name
        ops.append(
            _Operation(
                CURRENT, -MONTHLY_SAVINGS, f"VIR PERM POUR: {owner} LIVRET A", owner, SAVINGS.iban
            )
        )
        ops.append(
            _Operation(SAVINGS, MONTHLY_SAVINGS, f"VIR RECU DE: {owner}", owner, CURRENT.iban)
        )
    if day.day == 28:
        credit(
            SALARY,
            f"VIR RECU 7284519302 DE: ACME CONSEIL SAS MOTIF: SALAIRE {month}",
            "ACME CONSEIL SAS",
        )

    weekend = day.weekday() >= 5
    if rng.random() < 0.45:
        card("BOULANGERIE LA FOURNEE", 1.2, 8.9)
    if rng.random() < 0.35:
        card(*rng.choice(GROCERIES))
    if rng.random() < (0.55 if weekend else 0.3):
        card(*rng.choice(RESTAURANTS))
    if rng.random() < 0.12:
        card(*rng.choice(TRANSPORT))
    if rng.random() < 0.1:
        card(*rng.choice(SHOPPING))
    if rng.random() < 0.05:
        card("UGC CINE CITE LES HALLES", 11.5, 24)
    if rng.random() < 0.05:
        card("PHARMACIE DU MARCHE", 6, 35)
    if rng.random() < 0.04:
        amount = Decimal(rng.choice(("40.00", "60.00", "80.00")))
        time = f"{rng.randint(8, 21):02d}H{rng.randint(0, 59):02d}"
        ops.append(_Operation(CURRENT, -amount, f"RETRAIT DAB {day:%d/%m} {time} PARIS 11"))
    if rng.random() < 0.03:
        credit(_amount(rng, 12, 30), "VIR RECU DE: CPAM PARIS MOTIF: REMBT SOINS", "CPAM PARIS")
    if rng.random() < 0.04:
        credit(
            _amount(rng, 12, 30), "VIR INST RECU DE: JULIE BERNARD MOTIF: RESTO", "JULIE BERNARD"
        )
    return ops


def _to_raw(op: _Operation, day: date, index: int, today: date) -> dict[str, Any]:
    pending = op.card and day >= today - timedelta(days=1)
    is_debit = op.amount < 0
    raw: dict[str, Any] = {
        "entry_reference": None
        if pending
        else f"{op.account.uid[-4:].upper()}{day:%Y%m%d}{index:03d}",
        "transaction_id": f"demo-{op.account.uid}-{day.isoformat()}-{index}",
        "transaction_amount": {"currency": "EUR", "amount": f"{abs(op.amount):.2f}"},
        "credit_debit_indicator": "DBIT" if is_debit else "CRDT",
        "status": "PDNG" if pending else "BOOK",
        "booking_date": None if pending else day.isoformat(),
        "value_date": day.isoformat(),
        "transaction_date": day.isoformat(),
        "remittance_information": [op.label],
    }
    party, party_account = (
        ("creditor", "creditor_account") if is_debit else ("debtor", "debtor_account")
    )
    if op.counterparty:
        raw[party] = {"name": op.counterparty}
    if op.counterparty_iban:
        raw[party_account] = {"iban": op.counterparty_iban}
    return raw


def _signed(raw: dict[str, Any]) -> Decimal:
    amount = Decimal(raw["transaction_amount"]["amount"])
    return -amount if raw["credit_debit_indicator"] == "DBIT" else amount


class MockBankClient:
    def __init__(self, *, today: date, history_days: int = HISTORY_DAYS):
        self.today = today
        self._rows: dict[str, list[tuple[date, dict[str, Any]]]] = {
            a.uid: [] for a in DEMO_ACCOUNTS
        }
        day = today - timedelta(days=history_days)
        while day <= today:
            for index, op in enumerate(_operations_for_day(day)):
                self._rows[op.account.uid].append((day, _to_raw(op, day, index, today)))
            day += timedelta(days=1)

    def demo_session(self, now: datetime) -> Session:
        return Session(
            session_id="demo",
            accounts=DEMO_ACCOUNTS,
            valid_until=now + timedelta(days=180),
            aspsp_name="Société Générale",
            aspsp_country="FR",
            psu_type="personal",
            created_at=now,
            demo=True,
        )

    def get_balances(self, account: Account) -> list[Balance]:
        rows = self._rows.get(account.uid, [])
        booked = OPENING_BALANCES.get(account.uid, Decimal(0)) + sum(
            (_signed(raw) for _, raw in rows if raw["status"] == "BOOK"), Decimal(0)
        )
        pending = sum((_signed(raw) for _, raw in rows if raw["status"] == "PDNG"), Decimal(0))
        reference = self.today.isoformat()
        return [
            Balance(booked, "EUR", "CLBD", reference),
            Balance(booked + pending, "EUR", "XPCD", reference),
        ]

    def get_transactions(
        self, account: Account, date_from: date, date_to: date
    ) -> list[Transaction]:
        raws = [raw for day, raw in self._rows.get(account.uid, []) if date_from <= day <= date_to]
        return normalize_transactions(raws, account.key)
