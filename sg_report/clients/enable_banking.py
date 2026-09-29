"""Client de l'API Enable Banking (information sur les comptes, lecture seule)."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..config import DEFAULT_EB_API_URL, Config
from ..errors import BankAPIError, ConfigError, ConsentError
from ..models import Account, Balance, Transaction
from ..net import HttpResponse, Transport, urllib_transport
from .jwt import JWT_TTL_SECONDS, make_jwt

# Une seule relance, et seulement sur des erreurs d'infrastructure : la Société Générale
# limite le nombre d'accès quotidiens sans l'utilisateur, chaque appel compte.
RETRY_STATUSES = frozenset({502, 503, 504})
RETRY_DELAY_SECONDS = 3.0
MAX_TRANSACTION_PAGES = 50

_RENEW = "Renouvelez l'accès avec la commande link."
ERROR_HINTS = {
    "EXPIRED_SESSION": f"L'accès à vos comptes a expiré. {_RENEW}",
    "REVOKED_SESSION": f"L'accès à vos comptes a été révoqué. {_RENEW}",
    "CLOSED_SESSION": f"L'accès à vos comptes a été fermé. {_RENEW}",
    "SESSION_DOES_NOT_EXIST": f"Session bancaire inconnue d'Enable Banking. {_RENEW}",
    "WRONG_SESSION_STATUS": f"La session bancaire n'est plus active. {_RENEW}",
    "ASPSP_PSU_ACTION_REQUIRED": f"La banque demande une nouvelle validation. {_RENEW}",
    "EXPIRED_AUTHORIZATION_CODE": "Le code de validation a expiré : relancez link et collez "
    "l'adresse de retour sans attendre.",
    "WRONG_AUTHORIZATION_CODE": "Code de validation invalide : relancez link.",
    "ALREADY_AUTHORIZED": "Ce code de validation a déjà servi : relancez link.",
    "NO_ACCOUNTS_ADDED": "Aucun compte n'est rattaché à l'application Enable Banking : dans le "
    "Control Panel, utilisez « Activate by linking accounts » avec vos comptes Société Générale.",
    "REDIRECT_URI_NOT_ALLOWED": "L'URL ENABLE_BANKING_REDIRECT_URL n'est pas déclarée dans "
    "l'application Enable Banking.",
    "WRONG_ASPSP_PROVIDED": "Banque inconnue d'Enable Banking : vérifiez "
    "ENABLE_BANKING_ASPSP_NAME (commande aspsps).",
    "UNAUTHORIZED_ACCESS": "Authentification Enable Banking refusée : vérifiez "
    "ENABLE_BANKING_APP_ID et la clé privée.",
    "AUTHORIZATION_NOT_PROVIDED": "Authentification Enable Banking manquante : vérifiez "
    "ENABLE_BANKING_APP_ID et la clé privée.",
    "ASPSP_RATE_LIMIT_EXCEEDED": "La Société Générale limite le nombre d'accès quotidiens : "
    "réessayez plus tard.",
    "ASPSP_TIMEOUT": "La Société Générale ne répond pas : réessayez plus tard.",
    "ASPSP_ERROR": "Erreur côté Société Générale : réessayez plus tard.",
}
CONSENT_CODES = frozenset(
    {
        "EXPIRED_SESSION", "REVOKED_SESSION", "CLOSED_SESSION", "SESSION_DOES_NOT_EXIST",
        "WRONG_SESSION_STATUS", "ASPSP_PSU_ACTION_REQUIRED", "EXPIRED_AUTHORIZATION_CODE",
        "WRONG_AUTHORIZATION_CODE", "ALREADY_AUTHORIZED",
    }
)  # fmt: skip
CONFIG_CODES = frozenset(
    {
        "NO_ACCOUNTS_ADDED", "REDIRECT_URI_NOT_ALLOWED", "WRONG_ASPSP_PROVIDED",
        "UNAUTHORIZED_ACCESS", "AUTHORIZATION_NOT_PROVIDED",
    }
)  # fmt: skip


class EnableBankingClient:
    def __init__(
        self,
        app_id: str,
        private_key: Path,
        *,
        api_url: str = DEFAULT_EB_API_URL,
        transport: Transport | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = 60.0,
        signing_backend: str = "auto",
    ):
        self.app_id = app_id
        self.private_key = private_key
        self.api_url = api_url.rstrip("/")
        self._transport = transport or urllib_transport
        self._clock = clock
        self._sleep = sleep
        self._timeout = timeout
        self._signing_backend = signing_backend
        self._token = ""
        self._token_expires_at = 0

    @classmethod
    def from_config(cls, config: Config) -> EnableBankingClient:
        config.require_enable_banking()
        assert config.eb_private_key is not None
        return cls(config.eb_app_id, config.eb_private_key, api_url=config.eb_api_url)

    # --- Consentement ---------------------------------------------------------

    def list_aspsps(self, country: str, psu_type: str | None = None) -> list[dict[str, Any]]:
        data = self._request(
            "GET", "/aspsps", params={"country": country, "psu_type": psu_type, "service": "AIS"}
        )
        return list(data.get("aspsps") or [])

    def start_authorization(
        self,
        *,
        aspsp_name: str,
        country: str,
        psu_type: str,
        redirect_url: str,
        state: str,
        valid_until: datetime,
        language: str = "fr",
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            "/auth",
            payload={
                "access": {"valid_until": valid_until.astimezone(UTC).isoformat()},
                "aspsp": {"name": aspsp_name, "country": country},
                "state": state,
                "redirect_url": redirect_url,
                "psu_type": psu_type,
                "language": language,
            },
        )
        if not data.get("url"):
            raise BankAPIError("Enable Banking n'a pas renvoyé de lien d'autorisation.")
        return data

    def create_session(self, code: str) -> dict[str, Any]:
        return self._request("POST", "/sessions", payload={"code": code})

    # --- Données --------------------------------------------------------------

    def get_balances(self, account: Account) -> list[Balance]:
        data = self._request("GET", f"/accounts/{urllib.parse.quote(account.uid)}/balances")
        balances = (parse_balance(raw) for raw in data.get("balances") or [])
        return [balance for balance in balances if balance is not None]

    def get_transactions(
        self, account: Account, date_from: date, date_to: date
    ) -> list[Transaction]:
        path = f"/accounts/{urllib.parse.quote(account.uid)}/transactions"
        params = {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}
        raws: list[dict[str, Any]] = []
        for _ in range(MAX_TRANSACTION_PAGES):
            data = self._request("GET", path, params=params)
            raws.extend(data.get("transactions") or [])
            continuation_key = data.get("continuation_key")
            if not continuation_key:
                return normalize_transactions(raws, account.key)
            params = {**params, "continuation_key": continuation_key}
        raise BankAPIError(
            "Enable Banking renvoie trop de pages d'opérations, synchronisation interrompue."
        )

    # --- HTTP -----------------------------------------------------------------

    def _authorization(self) -> str:
        now = int(self._clock())
        if not self._token or now >= self._token_expires_at - 60:
            self._token = make_jwt(
                self.app_id, self.private_key, issued_at=now, backend=self._signing_backend
            )
            self._token_expires_at = now + JWT_TTL_SECONDS
        return f"Bearer {self._token}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | None] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            url = f"{url}?{query}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "Authorization": self._authorization()}
        if body is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(2):
            try:
                response = self._transport(method, url, headers, body, self._timeout)
            except OSError as exc:
                if attempt == 0:
                    self._sleep(RETRY_DELAY_SECONDS)
                    continue
                reason = getattr(exc, "reason", exc)
                raise BankAPIError(f"Enable Banking injoignable : {reason}") from exc
            if response.status in RETRY_STATUSES and attempt == 0:
                self._sleep(RETRY_DELAY_SECONDS)
                continue
            if response.status >= 400:
                raise _api_error(response)
            try:
                data = response.json()
            except ValueError as exc:
                raise BankAPIError(
                    "Réponse Enable Banking illisible.", status=response.status
                ) from exc
            return data if isinstance(data, dict) else {}
        raise AssertionError("unreachable")


def _api_error(response: HttpResponse) -> Exception:
    code = message = detail = ""
    try:
        data = response.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        code = str(data.get("error") or "")
        message = str(data.get("message") or "")
        raw_detail = data.get("detail")
        if raw_detail:
            detail = raw_detail if isinstance(raw_detail, str) else json.dumps(raw_detail)
    origin = " — ".join(part for part in (code, message, detail) if part) or response.text()
    hint = ERROR_HINTS.get(code)
    text = (
        f"{hint} (Enable Banking, HTTP {response.status} : {origin})"
        if hint
        else f"Erreur Enable Banking HTTP {response.status} : {origin}"
    )
    if code in CONSENT_CODES:
        return ConsentError(text)
    if code in CONFIG_CODES or (response.status == 401 and not code):
        return ConfigError(text)
    return BankAPIError(text, status=response.status, code=code or None)


# --- Normalisation des réponses -------------------------------------------------


def _parse_amount(info: dict[str, Any] | None) -> Decimal | None:
    try:
        return Decimal(str((info or {}).get("amount")))
    except InvalidOperation:
        return None


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def parse_balance(raw: dict[str, Any]) -> Balance | None:
    amount = _parse_amount(raw.get("balance_amount"))
    if amount is None:
        return None
    return Balance(
        amount=amount,
        currency=(raw.get("balance_amount") or {}).get("currency") or "EUR",
        balance_type=str(raw.get("balance_type") or "").upper(),
        reference_date=str(raw.get("reference_date") or ""),
    )


def normalize_account(raw: dict[str, Any]) -> Account | None:
    uid = raw.get("uid")
    if not uid:
        return None
    iban = (raw.get("account_id") or {}).get("iban") or ""
    if not iban:
        for other in raw.get("all_account_ids") or []:
            if str(other.get("scheme_name") or "").upper() == "IBAN":
                iban = other.get("identification") or ""
                break
    return Account(
        uid=str(uid),
        name=str(raw.get("name") or "").strip(),
        iban=str(iban).replace(" ", "").upper(),
        currency=raw.get("currency") or "EUR",
        product=str(raw.get("product") or raw.get("details") or "").strip(),
        cash_account_type=str(raw.get("cash_account_type") or "").upper(),
    )


def normalize_transaction(raw: dict[str, Any], account_key: str) -> Transaction | None:
    """Opération Enable Banking -> Transaction signée (montant négatif = débit)."""
    amount = _parse_amount(raw.get("transaction_amount"))
    tx_date = (
        _parse_date(raw.get("booking_date"))
        or _parse_date(raw.get("transaction_date"))
        or _parse_date(raw.get("value_date"))
    )
    if amount is None or tx_date is None:
        return None
    is_debit = str(raw.get("credit_debit_indicator") or "").upper() == "DBIT"
    amount = -abs(amount) if is_debit else abs(amount)

    party_key, party_account_key = (
        ("creditor", "creditor_account") if is_debit else ("debtor", "debtor_account")
    )
    counterparty = str((raw.get(party_key) or {}).get("name") or "").strip()
    counterparty_iban = str((raw.get(party_account_key) or {}).get("iban") or "")
    remittance = " ".join(str(line) for line in raw.get("remittance_information") or [] if line)
    label = " ".join(remittance.split()) or counterparty
    if not label:
        label = str((raw.get("bank_transaction_code") or {}).get("description") or "")
    label = label or str(raw.get("note") or "") or "Opération"

    status = str(raw.get("status") or "BOOK").upper()
    tx_id = raw.get("entry_reference") or raw.get("transaction_id")
    if not tx_id:
        fingerprint = f"{account_key}|{tx_date}|{amount}|{label}|{status}"
        tx_id = "h" + hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:16]
    return Transaction(
        id=str(tx_id),
        account_key=account_key,
        date=tx_date,
        amount=amount,
        currency=(raw.get("transaction_amount") or {}).get("currency") or "EUR",
        label=label,
        counterparty=counterparty,
        counterparty_iban=counterparty_iban.replace(" ", "").upper(),
        status=status,
        mcc=str(raw.get("merchant_category_code") or ""),
    )


def normalize_transactions(raws: Iterable[dict[str, Any]], account_key: str) -> list[Transaction]:
    seen: dict[str, int] = {}
    result = []
    for raw in raws:
        tx = normalize_transaction(raw, account_key)
        if tx is None:
            continue
        occurrences = seen.get(tx.id, 0)
        seen[tx.id] = occurrences + 1
        result.append(replace(tx, id=f"{tx.id}#{occurrences}") if occurrences else tx)
    return result
