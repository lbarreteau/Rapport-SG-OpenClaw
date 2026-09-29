"""Calcul du rapport de dépenses et rendus Markdown, résumé court et JSON."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from . import formatting as fmt
from .categorize import CASH, OTHER_EXPENSES, Categorized, Categorizer
from .models import Account, Balance, Session, expected_balance, main_balance
from .storage import AccountLedger, Ledger

ZERO = Decimal(0)
# Dépenses « pilotables » : surveillées pour les hausses et les top commerçants.
# Loyer, énergie, abonnements ou impôts tombent à date fixe et fausseraient ces comparaisons.
DISCRETIONARY = frozenset(
    {
        "Courses",
        "Restaurants & sorties",
        "Transport",
        "Shopping",
        "Loisirs & voyages",
        "Santé",
        CASH,
        OTHER_EXPENSES,
    }
)
SPIKE_MIN_INCREASE = Decimal(30)
SPIKE_MIN_RATIO = Decimal("1.3")
CONSENT_WARNING = timedelta(days=14)
STALE_AFTER = timedelta(hours=36)
MAX_CATEGORIES = 8
MAX_MERCHANTS = 5
MAX_LARGEST = 3
MAX_SPIKES = 3


@dataclass(frozen=True)
class MerchantTotal:
    name: str
    total: Decimal
    count: int


@dataclass
class PeriodStats:
    start: date
    end: date
    items: list[Categorized]
    expenses: Decimal = ZERO
    income: Decimal = ZERO
    savings: Decimal = ZERO
    categories: dict[str, Decimal] = field(default_factory=dict)
    refunds: dict[str, Decimal] = field(default_factory=dict)
    merchants: list[MerchantTotal] = field(default_factory=list)
    largest: list[Categorized] = field(default_factory=list)

    @property
    def operations(self) -> int:
        return len(self.items)

    @property
    def pending(self) -> int:
        return sum(1 for item in self.items if item.tx.is_pending)

    @property
    def net(self) -> Decimal:
        return self.income - self.expenses


@dataclass(frozen=True)
class AccountSummary:
    account: Account
    balance: Balance | None
    expected: Balance | None
    change: Decimal


@dataclass
class Report:
    start: date
    end: date
    days: int
    generated_at: datetime
    accounts: list[AccountSummary]
    current: PeriodStats
    previous: PeriodStats | None
    alerts: list[str]
    warnings: list[str]
    demo: bool = False

    @property
    def unit(self) -> str:
        return "semaine" if self.days == 7 else "période"


def compute_stats(
    items: list[Categorized], start: date, end: date, savings_keys: set[str]
) -> PeriodStats:
    period_items = sorted(
        (item for item in items if start <= item.tx.date <= end),
        key=lambda item: (item.tx.date, item.tx.id),
    )
    stats = PeriodStats(start=start, end=end, items=period_items)
    merchants: dict[str, list[Any]] = {}
    for item in period_items:
        amount = item.tx.amount
        if item.kind == "income":
            stats.income += amount
        elif item.kind == "transfer":
            # Vu depuis les comptes courants : un virement sortant vers l'épargne est « mis de côté ».
            if item.tx.account_key not in savings_keys:
                stats.savings -= amount
        else:
            spent = -amount
            stats.expenses += spent
            stats.categories[item.category] = stats.categories.get(item.category, ZERO) + spent
            if not item.tx.is_debit:
                stats.refunds[item.category] = stats.refunds.get(item.category, ZERO) + amount
            elif item.category in DISCRETIONARY and item.category != CASH:
                entry = merchants.setdefault(item.merchant_key, [item.merchant, ZERO, 0])
                entry[1] += spent
                entry[2] += 1
    stats.categories = {name: total for name, total in stats.categories.items() if total != 0}
    stats.merchants = sorted(
        (MerchantTotal(name, total, count) for name, total, count in merchants.values()),
        key=lambda merchant: merchant.total,
        reverse=True,
    )
    stats.largest = sorted(
        (
            item
            for item in period_items
            if item.kind == "expense"
            and item.tx.is_debit
            and item.category in DISCRETIONARY
            and item.category != CASH
        ),
        key=lambda item: item.tx.amount,
    )[:MAX_LARGEST]
    return stats


def build_report(
    *,
    session: Session,
    ledger: Ledger,
    categorizer: Categorizer,
    end: date,
    days: int,
    now: datetime,
    low_balance: Decimal,
    sync_warnings: list[str] | tuple[str, ...] = (),
    demo: bool = False,
) -> Report:
    start = end - timedelta(days=days - 1)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    entries = {a.key: ledger.accounts.get(a.key) for a in session.accounts}

    items = [
        categorizer.categorize(tx)
        for entry in entries.values()
        if entry is not None
        for tx in entry.transactions
        if previous_start <= tx.date <= end
    ]
    savings_keys = {account.key for account in session.accounts if account.is_savings}
    current = compute_stats(items, start, end, savings_keys)
    known = [entry for entry in entries.values() if entry is not None and entry.coverage]
    has_previous = bool(known) and all(e.covers(previous_start, previous_end) for e in known)
    previous = (
        compute_stats(items, previous_start, previous_end, savings_keys) if has_previous else None
    )
    warnings = _warnings(session, ledger, now, sync_warnings)
    gap = _coverage_gap(known, start, end)
    if gap is not None:
        warnings.append(
            f"Historique local incomplet : opérations connues à partir du {fmt.numeric_date(gap)} "
            "seulement (synchronisez une période plus longue pour compléter)."
        )

    summaries = []
    for account in session.accounts:
        entry = entries[account.key]
        balances = entry.balances if entry else []
        change = sum(
            (item.tx.amount for item in current.items if item.tx.account_key == account.key), ZERO
        )
        summaries.append(
            AccountSummary(account, main_balance(balances), expected_balance(balances), change)
        )

    unit = "semaine" if days == 7 else "période"
    return Report(
        start=start,
        end=end,
        days=days,
        generated_at=now,
        accounts=summaries,
        current=current,
        previous=previous,
        alerts=_alerts(current, previous, summaries, low_balance, unit),
        warnings=warnings,
        demo=demo,
    )


def _coverage_gap(known: list[AccountLedger], start: date, end: date) -> date | None:
    """Premier jour réellement synchronisé quand l'historique ne remonte pas jusqu'à `start`."""
    starts = [
        low
        for entry in known
        if not entry.covers(start, end)
        for low, high in entry.coverage
        if low <= end <= high
    ]
    return max(starts) if starts else None


def _alerts(
    current: PeriodStats,
    previous: PeriodStats | None,
    summaries: list[AccountSummary],
    low_balance: Decimal,
    unit: str,
) -> list[str]:
    alerts = []
    if previous is not None:
        spikes = []
        for category in DISCRETIONARY:
            now_total = current.categories.get(category, ZERO)
            before = previous.categories.get(category, ZERO)
            increase = now_total - before
            if increase >= SPIKE_MIN_INCREASE and (
                before <= 0 or now_total >= before * SPIKE_MIN_RATIO
            ):
                spikes.append((increase, category, now_total, before))
        for increase, category, now_total, before in sorted(spikes, reverse=True)[:MAX_SPIKES]:
            alerts.append(
                f"{category} en hausse : {fmt.money(now_total)} contre {fmt.money(before)} "
                f"la {unit} précédente ({fmt.money(increase, signed=True)})."
            )
    for summary in summaries:
        balance = summary.balance
        if balance is not None and not summary.account.is_savings and balance.amount < low_balance:
            alerts.append(
                f"Solde bas sur {summary.account.label} : {fmt.money(balance.amount, balance.currency)}"
                f" (seuil : {fmt.money(low_balance)})."
            )
    return alerts


def _warnings(
    session: Session, ledger: Ledger, now: datetime, sync_warnings: list[str] | tuple[str, ...]
) -> list[str]:
    warnings = list(sync_warnings)
    if session.valid_until is not None and not session.demo:
        expiry = fmt.long_date(session.valid_until.astimezone(now.tzinfo).date())
        if session.valid_until <= now:
            warnings.append(
                f"L'accès à vos comptes a expiré le {expiry} : renouvelez-le pour recevoir "
                "des données à jour."
            )
        elif session.valid_until - now <= CONSENT_WARNING:
            warnings.append(
                f"L'accès à vos comptes expire le {expiry} : pensez à le renouveler "
                "(demandez à OpenClaw de « renouveler l'accès SG »)."
            )
    if ledger.last_sync is None:
        warnings.append("Aucune synchronisation n'a encore eu lieu.")
    elif now - ledger.last_sync > STALE_AFTER:
        last = ledger.last_sync.astimezone(now.tzinfo)
        warnings.append(
            f"Données non actualisées depuis le {fmt.long_date(last.date())} à {last:%H:%M}."
        )
    return warnings


# --- Rendus ------------------------------------------------------------------------


def _delta(current: Decimal, previous: Decimal, unit: str) -> str:
    if previous <= 0:
        return f"rien la {unit} précédente" if current > 0 else ""
    return f"{fmt.percent((current - previous) / previous)} vs {unit} préc."


def render_markdown(report: Report) -> str:
    cur, prev, unit = report.current, report.previous, report.unit
    title = (
        "Rapport hebdo Société Générale"
        if report.days == 7
        else (f"Rapport Société Générale sur {report.days} jours")
    )
    if report.demo:
        title += " (démo)"
    lines = [f"**{title}**", fmt.period(report.start, report.end).capitalize(), "", "**Soldes**"]
    for summary in report.accounts:
        if summary.balance is None:
            lines.append(f"- {summary.account.label} : solde indisponible")
            continue
        currency = summary.balance.currency
        extras = []
        if summary.expected is not None and summary.expected.amount != summary.balance.amount:
            extras.append(f"à venir {fmt.money(summary.expected.amount, currency)}")
        extras.append(f"{unit} : {fmt.money(summary.change, currency, signed=True)}")
        lines.append(
            f"- {summary.account.label} : {fmt.money(summary.balance.amount, currency)} "
            f"({' · '.join(extras)})"
        )

    lines += ["", f"**Bilan de la {unit}**"]
    if not cur.operations:
        lines.append("- Aucune opération sur la période.")
    else:
        expenses = f"- Dépenses : {fmt.money(cur.expenses)}"
        delta = _delta(cur.expenses, prev.expenses, unit) if prev is not None else ""
        lines.append(f"{expenses} ({delta})" if delta else expenses)
        lines.append(f"- Revenus : {fmt.money(cur.income)}")
        if cur.savings > 0:
            lines.append(f"- Mis de côté : {fmt.money(cur.savings)}")
        elif cur.savings < 0:
            lines.append(f"- Puisé dans l'épargne : {fmt.money(-cur.savings)}")
        lines.append(f"- Revenus − dépenses : {fmt.money(cur.net, signed=True)}")

    categories = sorted(cur.categories.items(), key=lambda kv: kv[1], reverse=True)
    if categories:
        lines += ["", "**Dépenses par catégorie**"]
        for name, total in categories[:MAX_CATEGORIES]:
            if total < 0:
                lines.append(f"- {name} : {fmt.money(total)} (remboursements)")
                continue
            details = [fmt.percent(total / cur.expenses, signed=False)] if cur.expenses > 0 else []
            if prev is not None:
                before = prev.categories.get(name, ZERO)
                details.append(
                    _delta(total, before, unit)
                    if before > 0
                    else f"{fmt.money(ZERO)} la {unit} préc."
                )
            refunded = cur.refunds.get(name, ZERO)
            if refunded > 0:
                details.append(f"net de {fmt.money(refunded)} remboursés")
            suffix = f" ({', '.join(details)})" if details else ""
            lines.append(f"- {name} : {fmt.money(total)}{suffix}")

    if cur.merchants:
        lines += ["", "**Top commerçants**"]
        for rank, merchant in enumerate(cur.merchants[:MAX_MERCHANTS], start=1):
            count = f"{merchant.count} achat{'s' if merchant.count > 1 else ''}"
            lines.append(f"{rank}. {merchant.name} : {fmt.money(merchant.total)} ({count})")

    if cur.largest:
        lines += ["", "**Plus grosses dépenses**"]
        for item in cur.largest:
            pending = " (en attente)" if item.tx.is_pending else ""
            lines.append(
                f"- {fmt.short_date(item.tx.date)} · {item.merchant} : "
                f"{fmt.money(-item.tx.amount, item.tx.currency)}{pending}"
            )

    if report.alerts:
        lines += ["", "**Points d'attention**", *(f"- {alert}" for alert in report.alerts)]
    if report.warnings:
        lines += ["", "**À savoir**", *(f"- {warning}" for warning in report.warnings)]

    plural = "s" if cur.operations > 1 else ""
    footer = f"{cur.operations} opération{plural} analysée{plural}"
    if cur.pending:
        footer += f", dont {cur.pending} en attente"
    if prev is None and cur.operations and report.days == 7:
        footer += ". Comparaison avec la semaine précédente disponible au prochain rapport"
    lines += ["", f"_{footer}._"]
    return "\n".join(lines)


def render_summary(report: Report) -> str:
    """Quelques lignes pour une notification push."""
    cur, prev = report.current, report.previous
    spent = f"{fmt.money(cur.expenses, decimals=0)} dépensés"
    if prev is not None and prev.expenses > 0:
        spent += f" ({fmt.percent((cur.expenses - prev.expenses) / prev.expenses)})"
    parts = [f"{fmt.period(report.start, report.end, short=True).capitalize()} : {spent}"]
    top = [
        (n, t)
        for n, t in sorted(cur.categories.items(), key=lambda kv: kv[1], reverse=True)
        if t > 0
    ]
    if top:
        parts.append("Top : " + ", ".join(f"{n} {fmt.money(t, decimals=0)}" for n, t in top[:2]))
    current_accounts = [
        s for s in report.accounts if s.balance is not None and not s.account.is_savings
    ]
    if current_accounts:
        main = current_accounts[0]
        assert main.balance is not None
        parts.append(f"Solde {main.account.label} : {fmt.money(main.balance.amount, decimals=0)}")
    if report.alerts:
        count = len(report.alerts)
        parts.append(f"{count} point{'s' if count > 1 else ''} d'attention")
    return ". ".join(parts) + "."


def _number(value: Decimal) -> float:
    return float(round(value, 2))


def _stats_dict(stats: PeriodStats) -> dict[str, Any]:
    return {
        "start": stats.start.isoformat(),
        "end": stats.end.isoformat(),
        "expenses": _number(stats.expenses),
        "income": _number(stats.income),
        "savings": _number(stats.savings),
        "net": _number(stats.net),
        "operations": stats.operations,
        "pending": stats.pending,
        "categories": [
            {"name": name, "amount": _number(total)}
            for name, total in sorted(stats.categories.items(), key=lambda kv: kv[1], reverse=True)
        ],
        "top_merchants": [
            {"name": m.name, "amount": _number(m.total), "count": m.count}
            for m in stats.merchants[:MAX_MERCHANTS]
        ],
    }


def report_to_dict(report: Report) -> dict[str, Any]:
    labels = {summary.account.key: summary.account.label for summary in report.accounts}
    return {
        "period": {
            "start": report.start.isoformat(),
            "end": report.end.isoformat(),
            "days": report.days,
        },
        "generated_at": report.generated_at.isoformat(),
        "demo": report.demo,
        "accounts": [
            {
                "name": s.account.label,
                "savings": s.account.is_savings,
                "balance": _number(s.balance.amount) if s.balance else None,
                "expected_balance": _number(s.expected.amount) if s.expected else None,
                "currency": s.balance.currency if s.balance else s.account.currency,
                "change": _number(s.change),
            }
            for s in report.accounts
        ],
        "current": _stats_dict(report.current),
        "previous": _stats_dict(report.previous) if report.previous else None,
        "alerts": report.alerts,
        "warnings": report.warnings,
        "transactions": [
            {
                "date": item.tx.date.isoformat(),
                "account": labels.get(item.tx.account_key, ""),
                "amount": _number(item.tx.amount),
                "label": item.tx.label,
                "merchant": item.merchant,
                "category": item.category,
                "kind": item.kind,
                "pending": item.tx.is_pending,
            }
            for item in report.current.items
        ],
    }
