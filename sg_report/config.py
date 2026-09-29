"""Configuration : variables d'environnement, complétées par le fichier .env du dépôt."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_HA_TOKEN_FILE = Path("/config/secrets/homeassistant.token")
DEFAULT_EB_API_URL = "https://api.enablebanking.com"
REPORT_CHANNELS = ("telegram", "ha")
PSU_TYPES = ("personal", "business")
MAX_CONSENT_DAYS = 180
_TRUE = {"1", "true", "yes", "oui", "on"}


def parse_env_file(path: Path) -> dict[str, str]:
    """Lit un .env simple : CLE=valeur, lignes # ignorées, guillemets optionnels."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def resolve_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _read_token_file(value: str) -> str:
    path = resolve_path(value) if value else DEFAULT_HA_TOKEN_FILE
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


@dataclass(frozen=True)
class Config:
    mock: bool
    data_dir: Path
    timezone: ZoneInfo
    low_balance: Decimal
    rules_file: Path | None
    report_channel: str
    eb_api_url: str
    eb_app_id: str
    eb_private_key: Path | None
    eb_redirect_url: str
    eb_aspsp_name: str
    eb_aspsp_country: str
    eb_psu_type: str
    eb_consent_days: int
    ha_url: str
    ha_token: str
    ha_notify_service: str
    ha_push_service: str

    @classmethod
    def load(
        cls,
        *,
        env_file: Path | None = None,
        environ: Mapping[str, str] | None = None,
        mock: bool | None = None,
    ) -> Config:
        environ = os.environ if environ is None else environ
        if env_file is None:
            env_file = resolve_path(environ.get("SG_REPORT_ENV_FILE") or ".env")
        values = {**parse_env_file(env_file), **environ}

        def get(key: str, default: str = "") -> str:
            return (values.get(key) or "").strip() or default

        is_mock = get("SG_REPORT_MOCK").lower() in _TRUE if mock is None else mock
        data_dir = resolve_path(get("SG_REPORT_DATA_DIR", "data"))
        if is_mock:
            data_dir = data_dir / "demo"

        tz_name = get("SG_REPORT_TIMEZONE", "Europe/Paris")
        try:
            timezone = ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ConfigError(f"Fuseau horaire inconnu : SG_REPORT_TIMEZONE={tz_name}") from exc

        try:
            low_balance = Decimal(get("SG_REPORT_LOW_BALANCE", "200").replace(",", "."))
        except InvalidOperation as exc:
            raise ConfigError(
                "SG_REPORT_LOW_BALANCE doit être un montant, par exemple 200"
            ) from exc

        rules = get("SG_REPORT_RULES_FILE")
        default_rules = PROJECT_ROOT / "categories.json"
        if rules:
            rules_file: Path | None = resolve_path(rules)
        else:
            rules_file = default_rules if default_rules.is_file() else None

        channel = get("REPORT_CHANNEL", "telegram").lower()
        if channel not in REPORT_CHANNELS:
            raise ConfigError(
                f"REPORT_CHANNEL doit valoir telegram ou ha (valeur actuelle : {channel})"
            )

        psu_type = get("ENABLE_BANKING_PSU_TYPE", "personal").lower()
        if psu_type not in PSU_TYPES:
            raise ConfigError(
                f"ENABLE_BANKING_PSU_TYPE doit valoir personal ou business (valeur actuelle : {psu_type})"
            )

        try:
            consent_days = int(get("ENABLE_BANKING_CONSENT_DAYS", str(MAX_CONSENT_DAYS)))
        except ValueError as exc:
            raise ConfigError("ENABLE_BANKING_CONSENT_DAYS doit être un nombre de jours") from exc
        if not 1 <= consent_days <= MAX_CONSENT_DAYS:
            raise ConfigError(
                f"ENABLE_BANKING_CONSENT_DAYS doit être compris entre 1 et {MAX_CONSENT_DAYS}"
            )

        private_key = get("ENABLE_BANKING_PRIVATE_KEY")
        return cls(
            mock=is_mock,
            data_dir=data_dir,
            timezone=timezone,
            low_balance=low_balance,
            rules_file=rules_file,
            report_channel=channel,
            eb_api_url=get("ENABLE_BANKING_API_URL", DEFAULT_EB_API_URL).rstrip("/"),
            eb_app_id=get("ENABLE_BANKING_APP_ID"),
            eb_private_key=resolve_path(private_key) if private_key else None,
            eb_redirect_url=get("ENABLE_BANKING_REDIRECT_URL"),
            eb_aspsp_name=get("ENABLE_BANKING_ASPSP_NAME", "Société Générale"),
            eb_aspsp_country=get("ENABLE_BANKING_ASPSP_COUNTRY", "FR").upper(),
            eb_psu_type=psu_type,
            eb_consent_days=consent_days,
            ha_url=get("HA_URL", "http://127.0.0.1:8123"),
            ha_token=get("HA_TOKEN") or _read_token_file(get("HA_TOKEN_FILE")),
            ha_notify_service=get("HA_NOTIFY_SERVICE", "persistent_notification.create"),
            ha_push_service=get("HA_PUSH_SERVICE"),
        )

    def require_enable_banking(self) -> None:
        missing = [
            name
            for name, value in (
                ("ENABLE_BANKING_APP_ID", self.eb_app_id),
                ("ENABLE_BANKING_PRIVATE_KEY", self.eb_private_key),
                ("ENABLE_BANKING_REDIRECT_URL", self.eb_redirect_url),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "Configuration Enable Banking incomplète dans .env : "
                + ", ".join(missing)
                + ". Pour essayer sans banque, ajoutez --mock."
            )
        if self.eb_private_key is not None and not self.eb_private_key.is_file():
            raise ConfigError(f"Clé privée Enable Banking introuvable : {self.eb_private_key}")
