"""Interface commune aux sources de données bancaires (Enable Banking, démo)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from ..models import Account, Balance, Transaction


class BankClient(Protocol):
    def get_balances(self, account: Account) -> list[Balance]: ...

    def get_transactions(
        self, account: Account, date_from: date, date_to: date
    ) -> list[Transaction]: ...
