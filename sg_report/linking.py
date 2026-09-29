"""Consentement bancaire : lien d'autorisation à ouvrir, puis finalisation avec le code de retour."""

from __future__ import annotations

import difflib
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .clients.enable_banking import EnableBankingClient, normalize_account
from .config import Config
from .errors import BankAPIError, ConfigError, ConsentError
from .models import Account, Session, parse_datetime
from .storage import Store
from .text import normalize_text

# Marge sous le maximum annoncé par la banque, pour absorber un décalage d'horloge.
CONSENT_MARGIN = timedelta(hours=1)


def find_aspsp(aspsps: list[dict[str, Any]], wanted: str) -> dict[str, Any]:
    target = normalize_text(wanted)
    by_name = {normalize_text(a.get("name") or ""): a for a in aspsps if a.get("name")}
    if target in by_name:
        return by_name[target]
    close = difflib.get_close_matches(target, list(by_name), n=5, cutoff=0.5)
    close += [name for name in by_name if target and target in name and name not in close]
    suggestions = ", ".join(f"« {by_name[name]['name']} »" for name in close)
    hint = f" Noms proches : {suggestions}." if suggestions else " Consultez la commande aspsps."
    raise ConfigError(
        f"Banque « {wanted} » introuvable chez Enable Banking (ENABLE_BANKING_ASPSP_NAME).{hint}"
    )


def start_link(client: EnableBankingClient, store: Store, config: Config, *, now: datetime) -> str:
    """Prépare l'autorisation et renvoie le lien à ouvrir pour valider dans l'Appli SG."""
    aspsp = find_aspsp(
        client.list_aspsps(config.eb_aspsp_country, config.eb_psu_type), config.eb_aspsp_name
    )
    validity = timedelta(days=config.eb_consent_days)
    maximum = aspsp.get("maximum_consent_validity")
    if isinstance(maximum, int) and maximum > 0:
        validity = min(validity, timedelta(seconds=maximum) - CONSENT_MARGIN)
    state = secrets.token_urlsafe(16)
    auth = client.start_authorization(
        aspsp_name=aspsp["name"],
        country=config.eb_aspsp_country,
        psu_type=config.eb_psu_type,
        redirect_url=config.eb_redirect_url,
        state=state,
        valid_until=now + validity,
    )
    store.save_pending(
        {
            "state": state,
            "created_at": now.isoformat(),
            "aspsp_name": aspsp["name"],
            "aspsp_country": config.eb_aspsp_country,
            "psu_type": config.eb_psu_type,
            "authorization_id": auth.get("authorization_id"),
        }
    )
    return auth["url"]


@dataclass(frozen=True)
class CallbackParams:
    code: str | None
    state: str | None
    error: str | None


def parse_callback(callback_url: str) -> CallbackParams:
    parsed = urllib.parse.urlparse(callback_url.strip())
    query = urllib.parse.parse_qs(parsed.query or parsed.fragment)

    def first(name: str) -> str | None:
        values = query.get(name)
        return values[0] if values else None

    error = first("error")
    if error:
        error = first("error_description") or error
    return CallbackParams(code=first("code"), state=first("state"), error=error)


def complete_link(
    client: EnableBankingClient,
    store: Store,
    *,
    now: datetime,
    callback_url: str | None = None,
    code: str | None = None,
) -> Session:
    pending = store.load_pending() or {}
    if callback_url:
        params = parse_callback(callback_url)
        if params.error:
            raise ConsentError(
                f"Autorisation refusée ou annulée côté banque : {params.error}. Relancez link."
            )
        if not params.code:
            raise ConfigError(
                "Aucun paramètre « code= » dans cette adresse : copiez l'adresse complète "
                "de la page affichée après la validation."
            )
        if pending.get("state") and params.state != pending["state"]:
            raise ConsentError(
                "Cette adresse ne correspond pas à la dernière demande d'accès. Relancez link."
            )
        code = params.code
    if not code:
        raise ConfigError("Indiquez --callback-url (adresse de retour) ou --code.")

    data = client.create_session(code)
    session_id = data.get("session_id")
    if not session_id:
        raise BankAPIError("Enable Banking n'a pas renvoyé d'identifiant de session.")
    accounts: list[Account] = [
        account
        for account in (normalize_account(raw) for raw in data.get("accounts") or [])
        if account is not None
    ]
    if not accounts:
        raise ConfigError(
            "Autorisation acceptée, mais aucun compte renvoyé. En mode restreint, reliez d'abord "
            "vos comptes à l'application (« Activate by linking accounts » dans le Control Panel)."
        )
    aspsp = data.get("aspsp") or {}
    session = Session(
        session_id=str(session_id),
        accounts=tuple(accounts),
        valid_until=parse_datetime((data.get("access") or {}).get("valid_until")),
        aspsp_name=aspsp.get("name") or pending.get("aspsp_name", ""),
        aspsp_country=aspsp.get("country") or pending.get("aspsp_country", ""),
        psu_type=data.get("psu_type") or pending.get("psu_type", ""),
        created_at=now,
    )
    store.save_session(session)
    store.clear_pending()
    return session
