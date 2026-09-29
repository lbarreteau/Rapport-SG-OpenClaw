from __future__ import annotations

import itertools
from datetime import date
from decimal import Decimal

from sg_report.models import Account, Transaction

CURRENT = Account(
    uid="uid-courant",
    name="M TEST",
    iban="FR7630003000000000000001111",
    product="Compte courant",
    cash_account_type="CACC",
)
SAVINGS = Account(
    uid="uid-livret",
    name="M TEST",
    iban="FR7630003000000000000002222",
    product="Livret A",
    cash_account_type="SVGS",
)

_ids = itertools.count()


def tx(
    label: str,
    amount: str | float,
    day: date,
    *,
    account: Account = CURRENT,
    counterparty: str = "",
    counterparty_iban: str = "",
    status: str = "BOOK",
    mcc: str = "",
) -> Transaction:
    return Transaction(
        id=f"t{next(_ids)}",
        account_key=account.key,
        date=day,
        amount=Decimal(str(amount)),
        currency="EUR",
        label=label,
        counterparty=counterparty,
        counterparty_iban=counterparty_iban,
        status=status,
        mcc=mcc,
    )
