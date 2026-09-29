"""Stockage local en JSON (fichiers 0600, dossier 0700) : session, consentement en cours, opérations."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Balance, Session, Transaction, parse_datetime

SESSION_FILE = "session.json"
PENDING_FILE = "pending_auth.json"
LEDGER_FILE = "ledger.json"


def merge_intervals(intervals: list[tuple[date, date]]) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


@dataclass
class AccountLedger:
    transactions: list[Transaction] = field(default_factory=list)
    balances: list[Balance] = field(default_factory=list)
    balances_at: datetime | None = None
    coverage: list[tuple[date, date]] = field(default_factory=list)
    last_error: str = ""

    def replace_transactions(
        self, date_from: date, date_to: date, fetched: list[Transaction]
    ) -> None:
        """La banque fait foi sur la fenêtre synchronisée : une opération en attente
        devenue comptabilisée remplace son ancienne version au lieu de s'y ajouter."""
        by_id = {tx.id: tx for tx in self.transactions if not date_from <= tx.date <= date_to}
        by_id.update((tx.id, tx) for tx in fetched)
        self.transactions = sorted(by_id.values(), key=lambda tx: (tx.date, tx.id))
        self.coverage = merge_intervals([*self.coverage, (date_from, date_to)])

    def covers(self, start: date, end: date) -> bool:
        return any(low <= start and end <= high for low, high in self.coverage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "balances": [b.to_dict() for b in self.balances],
            "balances_at": self.balances_at.isoformat() if self.balances_at else None,
            "coverage": [[low.isoformat(), high.isoformat()] for low, high in self.coverage],
            "last_error": self.last_error,
            "transactions": [tx.to_dict() for tx in self.transactions],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AccountLedger:
        return cls(
            transactions=[Transaction.from_dict(t) for t in data.get("transactions") or []],
            balances=[Balance.from_dict(b) for b in data.get("balances") or []],
            balances_at=parse_datetime(data.get("balances_at")),
            coverage=[
                (date.fromisoformat(low), date.fromisoformat(high))
                for low, high in data.get("coverage") or []
            ],
            last_error=data.get("last_error") or "",
        )


@dataclass
class Ledger:
    """Opérations et soldes par compte, indexés par Account.key (IBAN)."""

    accounts: dict[str, AccountLedger] = field(default_factory=dict)
    last_sync: datetime | None = None

    def account(self, key: str) -> AccountLedger:
        return self.accounts.setdefault(key, AccountLedger())

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "accounts": {key: entry.to_dict() for key, entry in self.accounts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Ledger:
        return cls(
            accounts={
                key: AccountLedger.from_dict(entry)
                for key, entry in (data.get("accounts") or {}).items()
            },
            last_sync=parse_datetime(data.get("last_sync")),
        )


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def _read(self, name: str) -> dict[str, Any] | None:
        try:
            return json.loads((self.data_dir / name).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def _write(self, name: str, payload: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.data_dir, 0o700)
        fd, tmp_path = tempfile.mkstemp(dir=self.data_dir, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(tmp_path, self.data_dir / name)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)
            raise

    def _delete(self, name: str) -> None:
        with contextlib.suppress(FileNotFoundError):
            (self.data_dir / name).unlink()

    def load_session(self) -> Session | None:
        data = self._read(SESSION_FILE)
        return Session.from_dict(data) if data else None

    def save_session(self, session: Session) -> None:
        self._write(SESSION_FILE, session.to_dict())

    def load_pending(self) -> dict[str, Any] | None:
        return self._read(PENDING_FILE)

    def save_pending(self, pending: dict[str, Any]) -> None:
        self._write(PENDING_FILE, pending)

    def clear_pending(self) -> None:
        self._delete(PENDING_FILE)

    def load_ledger(self) -> Ledger:
        data = self._read(LEDGER_FILE)
        return Ledger.from_dict(data) if data else Ledger()

    def save_ledger(self, ledger: Ledger) -> None:
        self._write(LEDGER_FILE, ledger.to_dict())
