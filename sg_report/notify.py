"""Envoi du rapport en notification Home Assistant, via son API REST."""

from __future__ import annotations

import json

from .config import Config
from .errors import NotifyError
from .net import Transport, urllib_transport

NOTIFICATION_ID = "sg_report"


def send_home_assistant(
    config: Config,
    *,
    title: str,
    message: str,
    summary: str,
    transport: Transport | None = None,
) -> list[str]:
    """Rapport complet vers HA_NOTIFY_SERVICE, résumé court vers HA_PUSH_SERVICE (optionnel)."""
    if not config.ha_token:
        raise NotifyError(
            "Aucun jeton Home Assistant : renseignez HA_TOKEN dans .env, ou l'option "
            "homeassistant_token de l'add-on OpenClaw."
        )
    transport = transport or urllib_transport
    targets = [(config.ha_notify_service, message)]
    if config.ha_push_service:
        targets.append((config.ha_push_service, summary))
    sent = []
    for service, body in targets:
        domain, _, name = service.strip().partition(".")
        if not domain or not name:
            raise NotifyError(
                f"Service Home Assistant invalide : « {service} » (format attendu : domaine.service)."
            )
        payload = {"title": title, "message": body}
        if domain == "persistent_notification":
            payload["notification_id"] = NOTIFICATION_ID
        url = f"{config.ha_url.rstrip('/')}/api/services/{domain}/{name}"
        headers = {"Authorization": f"Bearer {config.ha_token}", "Content-Type": "application/json"}
        try:
            response = transport("POST", url, headers, json.dumps(payload).encode("utf-8"), 15.0)
        except OSError as exc:
            reason = getattr(exc, "reason", exc)
            raise NotifyError(f"Home Assistant injoignable ({config.ha_url}) : {reason}") from exc
        if response.status >= 400:
            raise NotifyError(
                f"Home Assistant a refusé la notification {service} "
                f"(HTTP {response.status}) : {response.text(200)}"
            )
        sent.append(service)
    return sent
