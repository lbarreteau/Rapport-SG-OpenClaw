from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from helpers import CURRENT, SAVINGS, tx

from sg_report.categorize import Categorizer
from sg_report.models import Balance, Session
from sg_report.report import build_report, render_markdown, render_summary, report_to_dict
from sg_report.storage import Ledger

PARIS = ZoneInfo("Europe/Paris")
END = date(2026, 9, 27)  # dimanche
NOW = datetime(2026, 9, 27, 18, 0, tzinfo=PARIS)
PREVIOUS_WEEK = date(2026, 9, 16)
THIS_WEEK = date(2026, 9, 23)


def make_session(valid_until: datetime | None = None, *, demo: bool = False) -> Session:
    return Session(
        session_id="s1",
        accounts=(CURRENT, SAVINGS),
        valid_until=valid_until or NOW + timedelta(days=150),
        demo=demo,
    )


def make_ledger(transactions, *, coverage_from=date(2026, 9, 14), last_sync=NOW, balance="1523.40"):
    ledger = Ledger(last_sync=last_sync)
    for account in (CURRENT, SAVINGS):
        entry = ledger.account(account.key)
        entry.replace_transactions(
            coverage_from, END, [t for t in transactions if t.account_key == account.key]
        )
    ledger.account(CURRENT.key).balances = [
        Balance(Decimal(balance), "EUR", "CLBD"),
        Balance(Decimal(balance) - Decimal("12.5"), "EUR", "XPCD"),
    ]
    ledger.account(SAVINGS.key).balances = [Balance(Decimal("8200"), "EUR", "CLBD")]
    return ledger


TRANSACTIONS = [
    # Semaine précédente
    tx("CARTE X4821 16/09 LIDL", -40, PREVIOUS_WEEK),
    tx("CARTE X4821 16/09 LE PETIT BISTROT", -20, PREVIOUS_WEEK),
    # Semaine du rapport
    tx("CARTE X4821 23/09 LIDL", -60, THIS_WEEK),
    tx("CARTE X4821 23/09 CARREFOUR CITY PARIS 11", -25, THIS_WEEK),
    tx("CARTE X4821 24/09 LE PETIT BISTROT", -45, date(2026, 9, 24)),
    tx("CARTE X4821 25/09 BIG MAMMA", -38, date(2026, 9, 25)),
    tx("CARTE X4821 26/09 FNAC PARIS", -149.99, date(2026, 9, 26)),
    tx(
        "VIR INST RECU DE: JULIE BERNARD MOTIF: RESTO",
        19,
        date(2026, 9, 26),
        counterparty="JULIE BERNARD",
    ),
    tx("PRLV SEPA NETFLIX.COM", -13.49, date(2026, 9, 25)),
    tx(
        "VIR RECU DE: ACME SAS MOTIF: SALAIRE SEPTEMBRE",
        2850,
        date(2026, 9, 26),
        counterparty="ACME SAS",
    ),
    tx("VIR PERM POUR: M TEST LIVRET A", -200, date(2026, 9, 24), counterparty_iban=SAVINGS.iban),
    tx(
        "VIR RECU DE: M TEST",
        200,
        date(2026, 9, 24),
        account=SAVINGS,
        counterparty_iban=CURRENT.iban,
    ),
    tx("CARTE X4821 27/09 STARBUCKS", -6.5, END, status="PDNG"),
]


@pytest.fixture
def report():
    categorizer = Categorizer(own_ibans=[CURRENT.iban, SAVINGS.iban])
    return build_report(
        session=make_session(),
        ledger=make_ledger(TRANSACTIONS),
        categorizer=categorizer,
        end=END,
        days=7,
        now=NOW,
        low_balance=Decimal(200),
    )


def test_totals_split_expenses_income_and_savings(report):
    cur = report.current
    assert (report.start, report.end) == (date(2026, 9, 21), END)
    # 60 + 25 + 45 + 38 + 149.99 + 13.49 + 6.5 - 19 (remboursement resto)
    assert cur.expenses == Decimal("318.98")
    assert cur.income == Decimal("2850")
    assert cur.savings == Decimal("200")
    assert cur.operations == 11
    assert cur.pending == 1
    assert cur.categories["Restaurants & sorties"] == Decimal("70.5")
    assert cur.refunds["Restaurants & sorties"] == Decimal("19")
    assert report.previous is not None
    assert report.previous.expenses == Decimal("60")


def test_merchants_and_largest_expenses(report):
    assert [m.name for m in report.current.merchants[:2]] == ["Fnac Paris", "Lidl"]
    assert report.current.largest[0].merchant == "Fnac Paris"
    # Les abonnements ne sont pas des dépenses « pilotables » : absents du top.
    assert all(m.name != "Netflix.com" for m in report.current.merchants)


def test_alerts_flag_spikes_on_discretionary_categories(report):
    assert any(alert.startswith("Shopping en hausse") for alert in report.alerts)
    assert any(alert.startswith("Restaurants & sorties en hausse") for alert in report.alerts)
    assert not any("Abonnements" in alert for alert in report.alerts)


def test_account_summaries(report):
    current, savings = report.accounts
    assert current.balance.amount == Decimal("1523.40")
    # 2850 - 318.98 - 200 (virement vers le livret)
    assert current.change == Decimal("2331.02")
    assert savings.change == Decimal("200")


def test_markdown_rendering(report):
    text = render_markdown(report)
    assert text.startswith("**Rapport hebdo Société Générale**\nDu 21 au 27 septembre 2026")
    assert "- Compte courant (…1111) : 1\u00a0523,40\u00a0€ (à venir 1\u00a0510,90\u00a0€" in text
    assert "- Dépenses : 318,98\u00a0€ (+432\u00a0% vs semaine préc.)" in text
    assert "- Mis de côté : 200,00\u00a0€" in text
    assert "net de 19,00\u00a0€ remboursés" in text
    assert "1. Fnac Paris : 149,99\u00a0€ (1 achat)" in text
    assert "**Points d'attention**" in text
    assert text.endswith("_11 opérations analysées, dont 1 en attente._")


def test_low_balance_alert():
    report = build_report(
        session=make_session(),
        ledger=make_ledger(TRANSACTIONS, balance="150"),
        categorizer=Categorizer(),
        end=END,
        days=7,
        now=NOW,
        low_balance=Decimal(200),
    )
    assert any(alert.startswith("Solde bas sur Compte courant") for alert in report.alerts)


def test_first_report_without_previous_week():
    ledger = make_ledger(TRANSACTIONS, coverage_from=date(2026, 9, 21))
    report = build_report(
        session=make_session(),
        ledger=ledger,
        categorizer=Categorizer(),
        end=END,
        days=7,
        now=NOW,
        low_balance=Decimal(0),
    )
    assert report.previous is None
    assert not any("en hausse" in alert for alert in report.alerts)
    assert (
        "Comparaison avec la semaine précédente disponible au prochain rapport"
        in render_markdown(report)
    )


def test_empty_period():
    report = build_report(
        session=make_session(),
        ledger=make_ledger([]),
        categorizer=Categorizer(),
        end=END,
        days=7,
        now=NOW,
        low_balance=Decimal(0),
    )
    text = render_markdown(report)
    assert "- Aucune opération sur la période." in text
    assert "**Dépenses par catégorie**" not in text


def test_operational_warnings():
    soon = NOW + timedelta(days=5)
    stale_sync = NOW - timedelta(days=3)
    report = build_report(
        session=make_session(valid_until=soon),
        ledger=make_ledger(TRANSACTIONS, last_sync=stale_sync),
        categorizer=Categorizer(),
        end=END,
        days=30,
        now=NOW,
        low_balance=Decimal(0),
        sync_warnings=["Synchronisation incomplète : Livret A : erreur"],
    )
    warnings = " | ".join(report.warnings)
    assert "Synchronisation incomplète" in warnings
    assert "expire le 2 octobre 2026" in warnings
    assert "Données non actualisées depuis le 24 septembre 2026 à 18:00" in warnings
    assert "Historique local incomplet : opérations connues à partir du 14/09/2026" in warnings

    expired = build_report(
        session=make_session(valid_until=NOW - timedelta(days=1)),
        ledger=make_ledger([]),
        categorizer=Categorizer(),
        end=END,
        days=7,
        now=NOW,
        low_balance=Decimal(0),
    )
    assert any("a expiré le 26 septembre 2026" in w for w in expired.warnings)


def test_summary_and_json(report):
    summary = render_summary(report)
    assert summary.startswith("Du 21 au 27 sept. : 319\u00a0€ dépensés (+432\u00a0%)")
    assert "Top : Shopping 150\u00a0€, Courses 85\u00a0€" in summary

    data = report_to_dict(report)
    assert data["period"] == {"start": "2026-09-21", "end": "2026-09-27", "days": 7}
    assert data["current"]["expenses"] == 318.98
    assert data["previous"]["expenses"] == 60.0
    assert len(data["transactions"]) == 11
    assert {t["account"] for t in data["transactions"]} == {
        "Compte courant (…1111)",
        "Livret A (…2222)",
    }
