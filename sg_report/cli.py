"""Ligne de commande : python3 -m sg_report [--mock] <commande>."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from . import __version__
from . import formatting as fmt
from .categorize import Categorizer
from .clients import EnableBankingClient, MockBankClient, make_client
from .config import Config
from .errors import BankAPIError, ConfigError, ConsentError, SgReportError
from .linking import complete_link, start_link
from .models import Session, main_balance
from .notify import send_home_assistant
from .report import Report, build_report, render_markdown, render_summary, report_to_dict
from .storage import Store
from .sync import SyncResult, sync_accounts
from .text import normalize_text

DEFAULT_FETCH_DAYS = 14
# Au-delà de 90 jours d'historique, la DSP2 impose une nouvelle validation dans l'Appli SG.
MAX_FETCH_DAYS = 89
MAX_REPORT_DAYS = 44


def _command(arguments: str) -> str:
    """Commande à suggérer, sous la forme utilisée pour lancer l'outil (script ou module)."""
    return f"{os.environ.get('SG_REPORT_COMMAND') or 'python3 -m sg_report'} {arguments}"


@dataclass(frozen=True)
class Context:
    config: Config
    store: Store
    now: datetime

    @property
    def today(self) -> date:
        return self.now.date()


def _load_session(ctx: Context, *, require_valid: bool) -> Session:
    session = ctx.store.load_session()
    if session is None and ctx.config.mock:
        session = MockBankClient(today=ctx.today).demo_session(ctx.now)
        ctx.store.save_session(session)
    if session is None:
        raise ConfigError(
            f"Aucun accès bancaire configuré. Reliez d'abord vos comptes : {_command('link')}"
        )
    if require_valid and session.is_expired(ctx.now):
        assert session.valid_until is not None
        expiry = fmt.long_date(session.valid_until.astimezone(ctx.config.timezone).date())
        raise ConsentError(
            f"L'accès à vos comptes a expiré le {expiry}. Renouvelez-le : {_command('link')}"
        )
    return session


def _sync(ctx: Context, session: Session, days: int) -> SyncResult:
    client = make_client(ctx.config, today=ctx.today)
    return sync_accounts(client, ctx.store, session, today=ctx.today, now=ctx.now, days=days)


def _sync_for_report(ctx: Context, session: Session, days: int) -> list[str]:
    """Synchronise ce qu'il faut pour la période et la précédente ; en cas d'échec bancaire
    temporaire, le rapport est quand même produit à partir des dernières données connues."""
    try:
        result = _sync(ctx, session, min(max(2 * days, DEFAULT_FETCH_DAYS), MAX_FETCH_DAYS))
    except BankAPIError as exc:
        return [
            f"Synchronisation impossible, rapport établi avec les dernières données connues ({exc})."
        ]
    return [f"Synchronisation incomplète : {error}" for error in result.errors]


def _build(ctx: Context, session: Session, *, end: date, days: int, warnings: list[str]) -> Report:
    categorizer = Categorizer.load(
        ctx.config.rules_file, own_ibans=[account.iban for account in session.accounts]
    )
    return build_report(
        session=session,
        ledger=ctx.store.load_ledger(),
        categorizer=categorizer,
        end=end,
        days=days,
        now=ctx.now,
        low_balance=ctx.config.low_balance,
        sync_warnings=warnings,
        demo=ctx.config.mock or session.demo,
    )


# --- Commandes ------------------------------------------------------------------


def cmd_link(args: argparse.Namespace, ctx: Context) -> int:
    if ctx.config.mock:
        session = MockBankClient(today=ctx.today).demo_session(ctx.now)
        ctx.store.save_session(session)
        print("Mode démo : accès fictif créé (Compte courant et Livret A).")
        return 0
    client = EnableBankingClient.from_config(ctx.config)
    if args.callback_url or args.code:
        session = complete_link(
            client, ctx.store, now=ctx.now, callback_url=args.callback_url, code=args.code
        )
        validity = ""
        if session.valid_until is not None:
            until = session.valid_until.astimezone(ctx.config.timezone).date()
            validity = f", valable jusqu'au {fmt.long_date(until)}"
        print(f"Accès enregistré : {len(session.accounts)} compte(s){validity}.")
        for account in session.accounts:
            print(f"- {account.label}")
        print(f"Premier rapport : {_command('run')}")
        return 0
    url = start_link(client, ctx.store, ctx.config, now=ctx.now)
    print("1. Ouvrez ce lien et validez l'accès dans l'Appli SG :")
    print(url)
    print("2. Vous arrivez ensuite sur une page (souvent en erreur, c'est normal) dont l'adresse")
    print("   contient « code= ». Copiez cette adresse complète.")
    print(f'3. Terminez avec : {_command("link")} --callback-url "<adresse copiée>"')
    return 0


def cmd_aspsps(args: argparse.Namespace, ctx: Context) -> int:
    if ctx.config.mock:
        print("Mode démo : pas d'appel à Enable Banking, la liste des banques est indisponible.")
        return 0
    client = EnableBankingClient.from_config(ctx.config)
    country = (args.country or ctx.config.eb_aspsp_country).upper()
    needle = normalize_text(args.search)
    aspsps = sorted(
        client.list_aspsps(country, ctx.config.eb_psu_type), key=lambda a: a.get("name", "")
    )
    shown = 0
    for aspsp in aspsps:
        name = aspsp.get("name") or ""
        if needle and needle not in normalize_text(name):
            continue
        seconds = aspsp.get("maximum_consent_validity")
        detail = (
            f" (consentement jusqu'à {seconds // 86400} jours)" if isinstance(seconds, int) else ""
        )
        print(f"- {name}{detail}")
        shown += 1
    if not shown:
        print("Aucune banque ne correspond.")
    return 0


def cmd_status(args: argparse.Namespace, ctx: Context) -> int:
    config = ctx.config
    print(f"Mode : {'démo (données fictives)' if config.mock else 'réel (Enable Banking)'}")
    channel = (
        "Telegram via OpenClaw"
        if config.report_channel == "telegram"
        else "notification Home Assistant"
    )
    print(f"Livraison : {channel}")
    print(f"Données locales : {config.data_dir}")
    session = ctx.store.load_session()
    if session is None:
        print(f"Accès bancaire : aucun. Reliez vos comptes avec : {_command('link')}")
        return 0
    if session.valid_until is None:
        print("Accès bancaire : actif (durée inconnue)")
    else:
        until = session.valid_until.astimezone(config.timezone)
        if session.is_expired(ctx.now):
            print(
                f"Accès bancaire : EXPIRÉ depuis le {fmt.long_date(until.date())}. Relancez link."
            )
        else:
            remaining = (session.valid_until - ctx.now).days
            print(
                f"Accès bancaire : valable jusqu'au {fmt.long_date(until.date())} (encore {remaining} jours)"
            )
    ledger = ctx.store.load_ledger()
    print("Comptes :")
    for account in session.accounts:
        entry = ledger.accounts.get(account.key)
        line = f"- {account.label}"
        balance = main_balance(entry.balances) if entry else None
        if balance is not None:
            line += f" : {fmt.money(balance.amount, balance.currency)}"
        if entry is not None and entry.transactions:
            line += f" · {len(entry.transactions)} opérations en local"
        if entry is not None and entry.last_error:
            line += f" · dernière erreur : {entry.last_error}"
        print(line)
    if ledger.last_sync is None:
        print("Dernière synchronisation : jamais")
    else:
        last = ledger.last_sync.astimezone(config.timezone)
        print(f"Dernière synchronisation : {fmt.numeric_date(last.date())} à {last:%H:%M}")
    return 0


def cmd_fetch(args: argparse.Namespace, ctx: Context) -> int:
    session = _load_session(ctx, require_valid=True)
    result = _sync(ctx, session, args.days)
    print(
        f"Synchronisation terminée : {result.accounts} compte(s), {result.transactions} opération(s) "
        f"du {fmt.numeric_date(result.date_from)} au {fmt.numeric_date(result.date_to)}."
    )
    for error in result.errors:
        print(f"Attention : {error}", file=sys.stderr)
    return 0


def cmd_report(args: argparse.Namespace, ctx: Context) -> int:
    session = _load_session(ctx, require_valid=args.fetch)
    end = args.end or ctx.today
    if end > ctx.today:
        raise ConfigError("--end ne peut pas être dans le futur.")
    warnings: list[str] = []
    if args.fetch:
        span = (ctx.today - end).days + args.days
        warnings = _sync_for_report(ctx, session, span)
    report = _build(ctx, session, end=end, days=args.days, warnings=warnings)
    if args.format == "json":
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    elif args.format == "summary":
        print(render_summary(report))
    else:
        print(render_markdown(report))
    return 0


def cmd_run(args: argparse.Namespace, ctx: Context) -> int:
    session = _load_session(ctx, require_valid=not args.no_fetch)
    warnings = [] if args.no_fetch else _sync_for_report(ctx, session, args.days)
    report = _build(ctx, session, end=ctx.today, days=args.days, warnings=warnings)
    markdown = render_markdown(report)
    print(markdown)
    if ctx.config.report_channel == "ha" and not args.no_send:
        title = f"Rapport SG {fmt.period(report.start, report.end, short=True)}"
        sent = send_home_assistant(
            ctx.config, title=title, message=markdown, summary=render_summary(report)
        )
        print(f"Notification Home Assistant envoyée ({', '.join(sent)}).", file=sys.stderr)
    return 0


# --- Analyse des arguments -------------------------------------------------------


def _int_range(low: int, high: int) -> Callable[[str], int]:
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError(f"nombre entier attendu : {value}") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"valeur entre {low} et {high} attendue : {value}")
        return number

    return parse


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"date AAAA-MM-JJ attendue : {value}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sg-report",
        description="Rapport des comptes Société Générale via Open Banking (Enable Banking).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--mock", action="store_true", help="données fictives, aucun appel bancaire"
    )
    parser.add_argument(
        "--env-file", type=Path, help="fichier .env à utiliser (défaut : .env du dépôt)"
    )
    # Accepté aussi après la commande (« run --mock ») sans écraser la valeur globale.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--mock", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="commande")

    link = commands.add_parser(
        "link", parents=[common], help="relier ou renouveler l'accès aux comptes"
    )
    source = link.add_mutually_exclusive_group()
    source.add_argument(
        "--callback-url", help="adresse complète de la page atteinte après validation"
    )
    source.add_argument("--code", help="valeur du paramètre code= de cette adresse")
    link.set_defaults(handler=cmd_link)

    aspsps = commands.add_parser(
        "aspsps", parents=[common], help="lister les banques Enable Banking"
    )
    aspsps.add_argument("--country", help="code pays (défaut : ENABLE_BANKING_ASPSP_COUNTRY)")
    aspsps.add_argument("--search", default="", help="filtrer par nom, ex. generale")
    aspsps.set_defaults(handler=cmd_aspsps)

    status = commands.add_parser("status", parents=[common], help="état de l'accès et des données")
    status.set_defaults(handler=cmd_status)

    fetch = commands.add_parser("fetch", parents=[common], help="synchroniser soldes et opérations")
    fetch.add_argument(
        "--days",
        type=_int_range(1, MAX_FETCH_DAYS),
        default=DEFAULT_FETCH_DAYS,
        help=f"nombre de jours d'historique (défaut {DEFAULT_FETCH_DAYS})",
    )
    fetch.set_defaults(handler=cmd_fetch)

    report = commands.add_parser(
        "report", parents=[common], help="rapport à partir des données locales"
    )
    report.add_argument(
        "--days", type=_int_range(1, MAX_REPORT_DAYS), default=7, help="durée (défaut 7)"
    )
    report.add_argument(
        "--end", type=_iso_date, help="dernier jour inclus, AAAA-MM-JJ (défaut : aujourd'hui)"
    )
    report.add_argument("--format", choices=("markdown", "json", "summary"), default="markdown")
    report.add_argument("--fetch", action="store_true", help="synchroniser avant de calculer")
    report.set_defaults(handler=cmd_report)

    run = commands.add_parser(
        "run",
        parents=[common],
        help="rapport hebdo : synchronise, affiche, notifie selon REPORT_CHANNEL",
    )
    run.add_argument(
        "--days", type=_int_range(1, MAX_REPORT_DAYS), default=7, help="durée (défaut 7)"
    )
    run.add_argument("--no-fetch", action="store_true", help="ne pas interroger la banque")
    run.add_argument(
        "--no-send", action="store_true", help="ne pas envoyer de notification Home Assistant"
    )
    run.set_defaults(handler=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        config = Config.load(env_file=args.env_file, mock=True if args.mock else None)
        ctx = Context(
            config=config, store=Store(config.data_dir), now=datetime.now(config.timezone)
        )
        return args.handler(args, ctx)
    except SgReportError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
