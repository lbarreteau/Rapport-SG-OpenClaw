"""Erreurs métier : leur message est affiché tel quel à l'utilisateur."""

from __future__ import annotations


class SgReportError(Exception):
    exit_code = 1


class ConfigError(SgReportError):
    """Configuration absente ou invalide (.env, clé privée, banque...)."""

    exit_code = 2


class ConsentError(SgReportError):
    """Accès bancaire absent, expiré ou révoqué : il faut relancer `link`."""

    exit_code = 2


class BankAPIError(SgReportError):
    """Échec côté Enable Banking ou banque, souvent temporaire."""

    exit_code = 3

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class NotifyError(SgReportError):
    exit_code = 4
