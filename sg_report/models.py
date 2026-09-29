"""Modèles : comptes, soldes, opérations et session bancaire."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from .text import normalize_text, pretty_name

SAVINGS_HINTS = ("LIVRET", "LDD", "LDDS", "LEP", "PEL", "CEL", "EPARGNE", "ASSURANCE VIE", "PERP")
BALANCE_PRIORITY = ("CLBD", "ITBD", "CLAV", "ITAV", "XPCD", "OPBD", "PRCD", "INFO", "OTHR")


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Account:
    uid: str
    name: str = ""
    iban: str = ""
    currency: str = "EUR"
    product: str = ""
    cash_account_type: str = ""

    @property
    def key(self) -> str:
        """Identifiant stable : l'IBAN survit au renouvellement du consentement, pas l'uid."""
        return self.iban or self.uid

    @property
    def label(self) -> str:
        base = (self.product or self.name or "Compte").strip()
        if base.isupper():
            base = pretty_name(base)
        return f"{base} (…{self.iban[-4:]})" if len(self.iban) >= 4 else base

    @property
    def is_savings(self) -> bool:
        if self.cash_account_type.upper() == "SVGS":
            return True
        words = f" {normalize_text(f'{self.product} {self.name}')} "
        return any(f" {hint} " in words for hint in SAVINGS_HINTS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "uid": self.uid,
            "name": self.name,
            "iban": self.iban,
            "currency": self.currency,
            "product": self.product,
            "cash_account_type": self.cash_account_type,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        return cls(
            uid=data["uid"],
            name=data.get("name") or "",
            iban=data.get("iban") or "",
            currency=data.get("currency") or "EUR",
            product=data.get("product") or "",
            cash_account_type=data.get("cash_account_type") or "",
        )


@dataclass(frozen=True)
class Balance:
    amount: Decimal
    currency: str
    balance_type: str
    reference_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "amount": str(self.amount),
            "currency": self.currency,
            "type": self.balance_type,
            "reference_date": self.reference_date,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Balance:
        return cls(
            amount=Decimal(data["amount"]),
            currency=data.get("currency") or "EUR",
            balance_type=data.get("type") or "",
            reference_date=data.get("reference_date") or "",
        )


def main_balance(balances: list[Balance]) -> Balance | None:
    for balance_type in BALANCE_PRIORITY:
        for balance in balances:
            if balance.balance_type.upper() == balance_type:
                return balance
    return balances[0] if balances else None


def expected_balance(balances: list[Balance]) -> Balance | None:
    """Solde « à venir » (opérations en attente comprises), quand la banque le fournit."""
    return next((b for b in balances if b.balance_type.upper() == "XPCD"), None)


@dataclass(frozen=True)
class Transaction:
    id: str
    account_key: str
    date: date
    amount: Decimal
    currency: str
    label: str
    counterparty: str = ""
    counterparty_iban: str = ""
    status: str = "BOOK"
    mcc: str = ""

    @property
    def is_debit(self) -> bool:
        return self.amount < 0

    @property
    def is_pending(self) -> bool:
        return self.status.upper() == "PDNG"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account": self.account_key,
            "date": self.date.isoformat(),
            "amount": str(self.amount),
            "currency": self.currency,
            "label": self.label,
            "counterparty": self.counterparty,
            "counterparty_iban": self.counterparty_iban,
            "status": self.status,
            "mcc": self.mcc,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transaction:
        return cls(
            id=data["id"],
            account_key=data["account"],
            date=date.fromisoformat(data["date"]),
            amount=Decimal(data["amount"]),
            currency=data.get("currency") or "EUR",
            label=data.get("label") or "",
            counterparty=data.get("counterparty") or "",
            counterparty_iban=data.get("counterparty_iban") or "",
            status=data.get("status") or "BOOK",
            mcc=data.get("mcc") or "",
        )


@dataclass(frozen=True)
class Session:
    session_id: str
    accounts: tuple[Account, ...]
    valid_until: datetime | None = None
    aspsp_name: str = ""
    aspsp_country: str = ""
    psu_type: str = ""
    created_at: datetime | None = None
    demo: bool = False

    def is_expired(self, now: datetime) -> bool:
        return self.valid_until is not None and self.valid_until <= now

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "valid_until": self.valid_until.isoformat() if self.valid_until else None,
            "aspsp": {"name": self.aspsp_name, "country": self.aspsp_country},
            "psu_type": self.psu_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "demo": self.demo,
            "accounts": [account.to_dict() for account in self.accounts],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Session:
        aspsp = data.get("aspsp") or {}
        return cls(
            session_id=data["session_id"],
            accounts=tuple(Account.from_dict(a) for a in data.get("accounts") or []),
            valid_until=parse_datetime(data.get("valid_until")),
            aspsp_name=aspsp.get("name") or "",
            aspsp_country=aspsp.get("country") or "",
            psu_type=data.get("psu_type") or "",
            created_at=parse_datetime(data.get("created_at")),
            demo=bool(data.get("demo")),
        )
