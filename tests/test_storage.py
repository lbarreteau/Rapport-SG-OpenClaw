import stat
from datetime import UTC, date, datetime
from decimal import Decimal

from helpers import CURRENT, tx

from sg_report.models import Balance, Session
from sg_report.storage import AccountLedger, Ledger, Store, merge_intervals


def test_merge_intervals_joins_overlapping_and_adjacent_ranges():
    merged = merge_intervals(
        [
            (date(2026, 9, 10), date(2026, 9, 20)),
            (date(2026, 9, 1), date(2026, 9, 5)),
            (date(2026, 9, 21), date(2026, 9, 29)),
            (date(2026, 9, 3), date(2026, 9, 8)),
        ]
    )
    assert merged == [
        (date(2026, 9, 1), date(2026, 9, 8)),
        (date(2026, 9, 10), date(2026, 9, 29)),
    ]


def test_replace_transactions_lets_the_bank_win_inside_the_window():
    entry = AccountLedger()
    old = tx("CARTE X4821 01/09 LIDL", -10, date(2026, 9, 1))
    pending = tx("CARTE X4821 20/09 LIDL", -30, date(2026, 9, 20), status="PDNG")
    entry.replace_transactions(date(2026, 9, 1), date(2026, 9, 20), [old, pending])

    booked = tx("CARTE X4821 20/09 LIDL", -30, date(2026, 9, 20))
    entry.replace_transactions(date(2026, 9, 14), date(2026, 9, 27), [booked])

    assert entry.transactions == [old, booked]
    assert entry.coverage == [(date(2026, 9, 1), date(2026, 9, 27))]
    assert entry.covers(date(2026, 9, 7), date(2026, 9, 13))
    assert not entry.covers(date(2026, 8, 31), date(2026, 9, 6))


def test_store_roundtrip_with_private_permissions(tmp_path):
    store = Store(tmp_path / "data")
    now = datetime(2026, 9, 27, 16, 0, tzinfo=UTC)
    session = Session(
        session_id="abc", accounts=(CURRENT,), valid_until=now, aspsp_name="Société Générale"
    )
    store.save_session(session)

    ledger = Ledger(last_sync=now)
    entry = ledger.account(CURRENT.key)
    entry.balances = [Balance(Decimal("12.34"), "EUR", "CLBD", "2026-09-27")]
    entry.replace_transactions(
        date(2026, 9, 14), date(2026, 9, 27), [tx("LIDL", -5, date(2026, 9, 20))]
    )
    store.save_ledger(ledger)

    assert store.load_session() == session
    loaded = store.load_ledger()
    assert loaded.last_sync == now
    assert loaded.accounts[CURRENT.key].transactions == entry.transactions
    assert loaded.accounts[CURRENT.key].balances == entry.balances
    assert loaded.accounts[CURRENT.key].coverage == entry.coverage

    assert stat.S_IMODE((tmp_path / "data").stat().st_mode) == 0o700
    for name in ("session.json", "ledger.json"):
        assert stat.S_IMODE((tmp_path / "data" / name).stat().st_mode) == 0o600


def test_pending_auth_lifecycle(tmp_path):
    store = Store(tmp_path)
    assert store.load_pending() is None
    store.save_pending({"state": "xyz"})
    assert store.load_pending() == {"state": "xyz"}
    store.clear_pending()
    store.clear_pending()
    assert store.load_pending() is None
