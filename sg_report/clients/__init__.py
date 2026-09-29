"""Sources de données bancaires."""

from __future__ import annotations

from datetime import date

from ..config import Config
from .base import BankClient
from .enable_banking import EnableBankingClient
from .mock import MockBankClient


def make_client(config: Config, *, today: date) -> BankClient:
    if config.mock:
        return MockBankClient(today=today)
    return EnableBankingClient.from_config(config)


__all__ = ["BankClient", "EnableBankingClient", "MockBankClient", "make_client"]
