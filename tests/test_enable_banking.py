import base64
import json
import subprocess
import urllib.parse
from datetime import date
from decimal import Decimal

import pytest
from helpers import CURRENT

from sg_report.clients.enable_banking import (
    EnableBankingClient,
    normalize_account,
    normalize_transaction,
    normalize_transactions,
)
from sg_report.clients.jwt import make_jwt, sign_rs256
from sg_report.errors import BankAPIError, ConfigError, ConsentError
from sg_report.net import HttpResponse


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "body": json.loads(body) if body else None,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def ok(payload):
    return HttpResponse(200, json.dumps(payload).encode())


def error(status, code="", message="oops"):
    return HttpResponse(
        status, json.dumps({"error": code, "message": message, "code": status}).encode()
    )


def _decode(segment):
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_jwt_is_valid_rs256_signed_with_openssl(rsa_key, tmp_path):
    token = make_jwt("app-123", rsa_key, issued_at=1_800_000_000, backend="openssl")
    header, payload, signature = token.split(".")
    assert _decode(header) == {"typ": "JWT", "alg": "RS256", "kid": "app-123"}
    assert _decode(payload) == {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": 1_800_000_000,
        "exp": 1_800_003_600,
    }
    public = tmp_path / "public.pem"
    subprocess.run(
        ["openssl", "pkey", "-in", str(rsa_key), "-pubout", "-out", str(public)], check=True
    )
    (tmp_path / "data").write_bytes(f"{header}.{payload}".encode())
    (tmp_path / "sig").write_bytes(
        base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))
    )
    verify = subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-verify",
            str(public),
            "-signature",
            str(tmp_path / "sig"),
            str(tmp_path / "data"),
        ],
        capture_output=True,
    )
    assert verify.returncode == 0, verify.stderr


def test_cryptography_and_openssl_backends_agree(rsa_key):
    pytest.importorskip("cryptography")
    message = b"header.payload"
    assert sign_rs256(message, rsa_key, backend="cryptography") == sign_rs256(
        message, rsa_key, backend="openssl"
    )


def test_unreadable_key_is_a_config_error(tmp_path):
    key = tmp_path / "broken.pem"
    key.write_text("pas une clé")
    with pytest.raises(ConfigError):
        sign_rs256(b"x", key, backend="openssl")


def make_client(rsa_key, *responses, sleeps=None):
    transport = FakeTransport(*responses)
    client = EnableBankingClient(
        "app-123",
        rsa_key,
        transport=transport,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        signing_backend="openssl",
    )
    return client, transport


def test_transactions_are_paginated_with_continuation_key(rsa_key):
    page1 = {
        "transactions": [
            {
                "entry_reference": "A1",
                "transaction_amount": {"amount": "12.30", "currency": "EUR"},
                "credit_debit_indicator": "DBIT",
                "status": "BOOK",
                "booking_date": "2026-09-20",
                "remittance_information": ["CARTE X4821 20/09", "LIDL"],
            },
        ],
        "continuation_key": "next-page",
    }
    page2 = {
        "transactions": [
            {
                "entry_reference": "A2",
                "transaction_amount": {"amount": "2850.00", "currency": "EUR"},
                "credit_debit_indicator": "CRDT",
                "status": "BOOK",
                "booking_date": "2026-09-26",
                "debtor": {"name": "ACME SAS"},
                "remittance_information": ["SALAIRE"],
            },
        ],
        "continuation_key": None,
    }
    client, transport = make_client(rsa_key, ok(page1), ok(page2))
    transactions = client.get_transactions(CURRENT, date(2026, 9, 14), date(2026, 9, 27))

    assert [(t.id, t.amount, t.label) for t in transactions] == [
        ("A1", Decimal("-12.30"), "CARTE X4821 20/09 LIDL"),
        ("A2", Decimal("2850.00"), "SALAIRE"),
    ]
    assert transactions[1].counterparty == "ACME SAS"
    assert all(t.account_key == CURRENT.key for t in transactions)
    first, second = (urllib.parse.urlparse(call["url"]) for call in transport.calls)
    assert first.path == "/accounts/uid-courant/transactions"
    assert urllib.parse.parse_qs(first.query) == {
        "date_from": ["2026-09-14"],
        "date_to": ["2026-09-27"],
    }
    assert urllib.parse.parse_qs(second.query)["continuation_key"] == ["next-page"]
    assert transport.calls[0]["headers"]["Authorization"].startswith("Bearer ey")


def test_balances_are_parsed(rsa_key):
    client, _ = make_client(
        rsa_key,
        ok(
            {
                "balances": [
                    {
                        "balance_amount": {"amount": "-42.10", "currency": "EUR"},
                        "balance_type": "CLBD",
                        "reference_date": "2026-09-27",
                    },
                    {"balance_amount": {"amount": "n/a"}, "balance_type": "XPCD"},
                ]
            }
        ),
    )
    [balance] = client.get_balances(CURRENT)
    assert (balance.amount, balance.balance_type) == (Decimal("-42.10"), "CLBD")


@pytest.mark.parametrize(
    ("response", "expected", "fragment"),
    [
        (error(401, "EXPIRED_SESSION"), ConsentError, "a expiré"),
        (error(422, "NO_ACCOUNTS_ADDED"), ConfigError, "Activate by linking accounts"),
        (HttpResponse(401, b"Unauthorized"), ConfigError, "Unauthorized"),
        (error(500, "ASPSP_ERROR"), BankAPIError, "Erreur côté Société Générale"),
        (error(429, "ASPSP_RATE_LIMIT_EXCEEDED"), BankAPIError, "limite le nombre d'accès"),
    ],
)
def test_api_errors_are_mapped(rsa_key, response, expected, fragment):
    client, transport = make_client(rsa_key, response)
    with pytest.raises(expected, match=fragment):
        client.get_balances(CURRENT)
    assert len(transport.calls) == 1


def test_gateway_errors_and_network_failures_are_retried_once(rsa_key):
    sleeps = []
    client, transport = make_client(rsa_key, HttpResponse(503), ok({"balances": []}), sleeps=sleeps)
    assert client.get_balances(CURRENT) == []
    assert len(transport.calls) == 2 and sleeps == [3.0]

    client, transport = make_client(rsa_key, OSError("boom"), OSError("boom"))
    with pytest.raises(BankAPIError, match="injoignable"):
        client.get_balances(CURRENT)
    assert len(transport.calls) == 2


def test_jwt_is_reused_between_requests(rsa_key):
    client, transport = make_client(rsa_key, ok({"balances": []}), ok({"balances": []}))
    client.get_balances(CURRENT)
    client.get_balances(CURRENT)
    tokens = {call["headers"]["Authorization"] for call in transport.calls}
    assert len(tokens) == 1


def test_normalize_transaction_fallbacks():
    pending = normalize_transaction(
        {
            "transaction_amount": {"amount": "6.50"},
            "credit_debit_indicator": "DBIT",
            "status": "PDNG",
            "transaction_date": "2026-09-27",
            "creditor": {"name": "STARBUCKS"},
            "creditor_account": {"iban": "fr76 1234"},
        },
        "KEY",
    )
    assert pending.date == date(2026, 9, 27)
    assert pending.label == "STARBUCKS"
    assert pending.counterparty_iban == "FR761234"
    assert pending.is_pending and pending.id.startswith("h")
    assert normalize_transaction({"transaction_amount": {"amount": "1"}}, "KEY") is None

    duplicate = {
        "transaction_amount": {"amount": "2.50"},
        "credit_debit_indicator": "DBIT",
        "booking_date": "2026-09-21",
        "remittance_information": ["CAFE"],
    }
    first, second = normalize_transactions([duplicate, duplicate], "KEY")
    assert second.id == f"{first.id}#1"


def test_normalize_account_uses_iban_or_other_identifiers():
    account = normalize_account(
        {
            "uid": "u1",
            "name": "M TEST",
            "product": "COMPTE BANCAIRE",
            "cash_account_type": "cacc",
            "all_account_ids": [{"scheme_name": "IBAN", "identification": "FR76 3000 3000"}],
        }
    )
    assert account.iban == "FR7630003000"
    assert account.label == "Compte Bancaire (…3000)"
    assert normalize_account({"name": "sans uid"}) is None
