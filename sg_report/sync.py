"""Synchronisation des soldes et opérations vers le stockage local."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .clients.base import BankClient
from .errors import BankAPIError
from .models import Session
from .storage import Store


@dataclass(frozen=True)
class SyncResult:
    date_from: date
    date_to: date
    accounts: int
    transactions: int
    errors: tuple[str, ...]


def sync_accounts(
    client: BankClient,
    store: Store,
    session: Session,
    *,
    today: date,
    now: datetime,
    days: int,
) -> SyncResult:
    """Synchronise chaque compte ; un compte en erreur n'empêche pas les autres.

    Les erreurs de consentement (ConsentError) remontent telles quelles : inutile
    d'insister auprès de la banque tant que l'accès n'est pas renouvelé.
    """
    if days < 1:
        raise ValueError("days doit être >= 1")
    date_from = today - timedelta(days=days - 1)
    ledger = store.load_ledger()
    errors: list[str] = []
    synced = fetched = 0
    for account in session.accounts:
        entry = ledger.account(account.key)
        try:
            balances = client.get_balances(account)
            transactions = client.get_transactions(account, date_from, today)
        except BankAPIError as exc:
            entry.last_error = str(exc)
            errors.append(f"{account.label} : {exc}")
            continue
        entry.balances = balances
        entry.balances_at = now
        entry.replace_transactions(date_from, today, transactions)
        entry.last_error = ""
        synced += 1
        fetched += len(transactions)
    if synced:
        ledger.last_sync = now
    store.save_ledger(ledger)
    if errors and not synced:
        raise BankAPIError("Aucun compte n'a pu être synchronisé. " + " | ".join(errors))
    return SyncResult(date_from, today, synced, fetched, tuple(errors))
