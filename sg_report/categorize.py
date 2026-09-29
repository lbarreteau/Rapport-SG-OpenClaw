"""Catégorisation des opérations par mots-clés, adaptée aux libellés Société Générale.

Un mot-clé est comparé mot à mot au libellé normalisé (« CARTE X4821 21/09 CARREFOUR
CITY » -> « CARTE X4821 21 09 CARREFOUR CITY ») ; un « * » final l'autorise comme
préfixe (« BOULANGERIE* »). En cas de conflit, le mot-clé le plus long l'emporte :
« UBER EATS » (restaurant) passe devant « UBER » (transport).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError
from .models import Transaction
from .text import normalize_text, pretty_name, strip_accents

INTERNAL = "Épargne & virements internes"
OTHER_EXPENSES = "Autres dépenses"
OTHER_INCOME = "Autres revenus"
CASH = "Retraits d'espèces"
INCOME_CATEGORIES = frozenset({"Salaire", "Aides & allocations", OTHER_INCOME})

EXPENSE_RULES: dict[str, tuple[str, ...]] = {
    "Courses": (
        "CARREFOUR*", "LECLERC", "AUCHAN", "INTERMARCHE", "LIDL", "ALDI", "MONOPRIX", "MONOP",
        "FRANPRIX", "CASINO", "SUPER U", "HYPER U", "U EXPRESS", "SYSTEME U", "PICARD",
        "NATURALIA", "BIOCOOP", "LA VIE CLAIRE", "GRAND FRAIS", "CORA", "SPAR", "NETTO",
        "LEADER PRICE", "G20", "PROXI", "VIVAL", "CHRONODRIVE", "LA GRANDE EPICERIE",
        "BOULANGERIE*", "PATISSERIE*", "BOUCHERIE*", "FROMAGERIE*", "PRIMEUR*", "EPICERIE*",
        "HELLOFRESH", "QUITOQUE",
    ),
    "Restaurants & sorties": (
        "RESTAURANT*", "RESTO", "BRASSERIE*", "BISTRO*", "CAFE*", "BAR", "PUB", "MCDONALD*",
        "MC DONALD*", "BURGER*", "BURGER KING", "KFC", "QUICK", "FIVE GUYS", "UBER EATS",
        "DELIVEROO", "JUST EAT", "STARBUCKS", "COLUMBUS CAFE", "SUSHI*", "PIZZA*", "PIZZERIA*",
        "DOMINOS", "KEBAB", "O TACOS", "TACOS", "CREPERIE*", "TRAITEUR", "BIG MAMMA",
        "PRET A MANGER", "COJEAN", "BAGELSTEIN", "SUBWAY", "HIPPOPOTAMUS", "BUFFALO GRILL",
        "COURTEPAILLE",
    ),
    "Transport": (
        "SNCF*", "OUIGO", "TGV", "TER", "RATP", "NAVIGO", "COMUTITRES", "UBER", "BOLT", "HEETCH",
        "G7", "TAXI*", "FREE NOW", "BLABLACAR", "TOTALENERGIES*", "TOTAL ACCESS", "ESSO", "SHELL",
        "BP", "AVIA", "CARBURANT*", "STATION SERVICE", "PEAGE*", "VINCI AUTOROUTES", "SANEF",
        "APRR", "ASF", "ULYS", "BIP BIP", "PARKING*", "INDIGO", "SAEMES", "ONEPARK", "ZENPARK",
        "TRANSDEV", "KEOLIS", "TCL", "TISSEO", "LIME", "DOTT", "VELIB*", "GETAROUND", "NORAUTO",
        "MIDAS", "SPEEDY", "FEU VERT",
    ),
    "Logement": (
        "LOYER*", "FONCIA", "NEXITY", "CITYA", "ORALIA", "SYNDIC*", "COPRO*",
        "CHARGES LOCATIVES", "CREDIT IMMO*", "PRET IMMO*", "ECHEANCE PRET", "AGENCE IMMO*",
        "SQUARE HABITAT", "ACTION LOGEMENT",
    ),
    "Énergie & télécom": (
        "EDF", "ENGIE", "TOTALENERGIES ELEC*", "TOTALENERGIES GAZ*", "TOTAL DIRECT ENERGIE",
        "ENI GAZ*", "EKWATEUR", "OHM ENERGIE", "MINT ENERGIE", "PLANETE OUI", "VEOLIA", "SUEZ",
        "EAU DE PARIS", "SAUR", "ORANGE", "SOSH", "SFR", "RED BY SFR", "BOUYGUES*", "B YOU",
        "FREE MOBILE", "FREE TELECOM", "FREEBOX", "FREE HAUT DEBIT", "PRIXTEL", "LA POSTE MOBILE",
        "CORIOLIS", "SYMA", "NRJ MOBILE", "LEBARA", "LYCAMOBILE",
    ),
    "Abonnements": (
        "NETFLIX*", "SPOTIFY", "DEEZER", "DISNEY*", "PRIME VIDEO", "AMAZON PRIME", "AMZN PRIME",
        "APPLE COM BILL", "ITUNES", "GOOGLE PLAY", "GOOGLE STORAGE", "GOOGLE ONE", "YOUTUBE*",
        "CANAL*", "MOLOTOV", "OCS", "PARAMOUNT*", "CRUNCHYROLL", "AUDIBLE", "KINDLE", "OPENAI",
        "CHATGPT", "ANTHROPIC", "MICROSOFT*", "ADOBE", "DROPBOX", "ICLOUD", "PLAYSTATION", "PSN",
        "XBOX", "NINTENDO", "BASIC FIT", "FITNESS PARK", "NEONESS", "ON AIR", "L ORANGE BLEUE",
        "KEEP COOL", "CLUB MED GYM", "SALLE DE SPORT", "LE MONDE", "LE FIGARO", "MEDIAPART",
        "LIBERATION", "L EQUIPE", "CAFEYN", "PATREON", "TWITCH", "DUOLINGO",
    ),
    "Shopping": (
        "AMAZON*", "AMZN*", "FNAC", "DARTY", "BOULANGER", "DECATHLON", "IKEA", "LEROY MERLIN",
        "CASTORAMA", "BRICOMARCHE", "BRICO DEPOT", "MR BRICOLAGE", "ZARA", "H M", "UNIQLO",
        "KIABI", "SEPHORA", "NOCIBE", "MARIONNAUD", "DOUGLAS", "YVES ROCHER", "LUSH", "ACTION",
        "LA REDOUTE", "VINTED", "CDISCOUNT", "ALIEXPRESS", "SHEIN", "TEMU", "ZALANDO",
        "GALERIES LAFAYETTE", "PRINTEMPS", "BHV", "PRIMARK", "CELIO", "JULES", "NIKE", "ADIDAS",
        "APPLE STORE", "MAISONS DU MONDE", "CONFORAMA", "NATURE ET DECOUVERTES", "CULTURA",
        "GIFI", "LIBRAIRIE*", "ETSY", "EBAY", "LEBONCOIN", "PAYPAL", "JOUECLUB", "KING JOUET",
        "ELECTRO DEPOT", "LDLC", "RUE DU COMMERCE",
    ),
    "Santé": (
        "PHARMACIE*", "PHARMA*", "PHIE", "MEDECIN*", "DOCTEUR", "DR", "DENTIST*", "ORTHODONT*",
        "KINE*", "OSTEO*", "PODOLOGUE", "OPHTALMO*", "LABO", "LABORATOIRE*", "BIOGROUP",
        "CERBALLIANCE", "HOPITAL*", "CLINIQUE*", "CENTRE MEDICAL", "OPTIC*", "OPTIQUE*", "KRYS",
        "ATOL", "AFFLELOU", "AUDITION", "DOCTOLIB", "CPAM", "AMELI", "ASSURANCE MALADIE",
        "MUTUELLE*", "HARMONIE MUTUELLE", "MGEN", "MALAKOFF*", "AG2R",
    ),
    "Assurances": (
        "ASSURANCE*", "AXA", "MAIF", "MACIF", "MATMUT", "ALLIANZ", "GROUPAMA", "GMF", "MAAF",
        "MMA", "GENERALI", "SOGESSUR", "SOGECAP", "PACIFICA", "LUKO", "LEOCARE", "HISCOX",
        "DIRECT ASSURANCE", "OLIVIER ASSURANCE", "ACHEEL", "LOVYS", "CARDIF", "ABEILLE*",
    ),
    "Banque & frais": (
        "COTISATION JAZZ", "COTISATION CARTE", "COTIS CARTE", "COTISATION MENSUELLE", "JAZZ",
        "SOBRIO", "KAPSULE", "FRAIS", "FRAIS BANCAIRES", "FRAIS PAIEMENT*", "FRAIS RETRAIT*",
        "FRAIS TENUE*", "FRAIS INCIDENT*", "FRAIS REJET*", "FRAIS CARTE*", "FRAIS VIREMENT*",
        "COMMISSION*", "AGIOS", "INTERETS DEBITEURS", "TENUE DE COMPTE", "OPPOSITION",
    ),
    CASH: ("RETRAIT", "RETRAIT DAB", "RETRAIT GAB", "DAB", "GAB"),
    "Impôts & administration": (
        "DGFIP", "IMPOT*", "TRESOR PUBLIC", "FINANCES PUBLIQUES", "TAXE*", "AMENDE*", "ANTAI",
        "URSSAF", "ANTS", "PREFECTURE", "TIMBRE FISCAL", "SERVICE PUBLIC",
    ),
    "Loisirs & voyages": (
        "CINEMA*", "CINE", "UGC", "PATHE", "MK2", "GAUMONT", "CGR", "TICKETMASTER",
        "FNAC SPECTACLES", "BILLETREDUC", "SEETICKETS", "SHOTGUN", "MUSEE*", "THEATRE*",
        "CONCERT*", "BOWLING", "ESCAPE*", "PARC ASTERIX", "DISNEYLAND*", "FUTUROSCOPE", "AIRBNB",
        "BOOKING*", "HOTEL*", "HOTELS COM", "ACCOR*", "IBIS", "NOVOTEL", "MERCURE", "AIR FRANCE",
        "EASYJET", "RYANAIR", "TRANSAVIA", "VOLOTEA", "VUELING", "LASTMINUTE", "OPODO",
        "EXPEDIA", "CLUB MED", "PIERRE ET VACANCES", "CENTER PARCS", "STEAM*", "EPIC GAMES",
    ),
    INTERNAL: (
        "LIVRET*", "LDDS", "LDD", "LEP", "PEL", "CEL", "ASSURANCE VIE", "PERP", "EPARGNE*",
        "VIR INTERNE", "VIREMENT INTERNE", "VIR CPTE A CPTE", "VIR COMPTE A COMPTE",
        "VIREMENT DE COMPTE A COMPTE",
    ),
}  # fmt: skip

INCOME_RULES: dict[str, tuple[str, ...]] = {
    "Salaire": ("SALAIRE*", "PAIE", "REMUNERATION*", "NET A PAYER"),
    "Aides & allocations": (
        "CAF", "ALLOCATION*", "FRANCE TRAVAIL", "POLE EMPLOI", "PRIME D ACTIVITE",
        "PRIME ACTIVITE", "CARSAT", "AGIRC ARRCO", "RETRAITE*", "PENSION*",
    ),
}  # fmt: skip

# Codes MCC (ISO 18245), utilisés quand la banque les fournit et qu'aucun mot-clé ne correspond.
MCC_RANGES: tuple[tuple[int, int, str], ...] = (
    (5411, 5411, "Courses"), (5422, 5422, "Courses"), (5441, 5451, "Courses"),
    (5462, 5462, "Courses"), (5499, 5499, "Courses"),
    (5811, 5814, "Restaurants & sorties"),
    (4011, 4131, "Transport"), (4784, 4789, "Transport"), (5541, 5542, "Transport"),
    (7512, 7549, "Transport"),
    (3000, 3350, "Loisirs & voyages"), (3501, 3999, "Loisirs & voyages"),
    (4511, 4511, "Loisirs & voyages"), (4722, 4722, "Loisirs & voyages"),
    (7011, 7012, "Loisirs & voyages"), (7832, 7841, "Loisirs & voyages"),
    (7911, 7999, "Loisirs & voyages"),
    (4812, 4816, "Énergie & télécom"), (4899, 4900, "Énergie & télécom"),
    (5912, 5912, "Santé"), (8011, 8099, "Santé"),
    (6300, 6399, "Assurances"),
    (6010, 6011, CASH),
    (9211, 9399, "Impôts & administration"),
    (5200, 5999, "Shopping"),
)  # fmt: skip


def category_from_mcc(mcc: str) -> str | None:
    if not mcc.isdigit():
        return None
    code = int(mcc)
    return next((category for low, high, category in MCC_RANGES if low <= code <= high), None)


def kind_of(category: str, tx: Transaction) -> str:
    """« transfer » (hors bilan), « income » (revenu) ou « expense » (dépense ou remboursement)."""
    if category == INTERNAL:
        return "transfer"
    if not tx.is_debit and (category in INCOME_CATEGORIES or category.startswith("Revenus")):
        return "income"
    return "expense"


class KeywordIndex:
    def __init__(self, rules: Mapping[str, Iterable[str]]):
        entries: list[tuple[str, str]] = []
        for category, keywords in rules.items():
            for keyword in keywords:
                prefix = keyword.endswith("*")
                normalized = normalize_text(keyword.rstrip("*"))
                if normalized:
                    needle = f" {normalized}" if prefix else f" {normalized} "
                    entries.append((needle, category))
        self._entries = sorted(entries, key=lambda entry: len(entry[0].strip()), reverse=True)

    def match(self, normalized_text: str) -> str | None:
        padded = f" {normalized_text} "
        return next((category for needle, category in self._entries if needle in padded), None)


# --- Nom du commerçant à partir du libellé ---------------------------------------

_PARTY = re.compile(r"^(?:VIR|VIREMENT|PRLV|PRELEVEMENT)\b.*?\b(?:DE|POUR)\s*:\s*(.+)$")
_CARD_PREFIX = re.compile(
    r"^(?:PAIEMENT\s+(?:PAR\s+)?)?(?:CARTE|CB)\b\s*\*?\s*(?:X?\d{4}\s+)?(?:REMBT\s+)?"
    r"(?:\d{2}/\d{2}(?:/\d{2,4})?\s+)?"
)
_DEBIT_PREFIX = re.compile(r"^(?:PRLV|PRELEVEMENT)\s+(?:SEPA\s+)?(?:EUROPEEN\s+)?")
_TRANSFER_PREFIX = re.compile(
    r"^(?:VIR|VIREMENT)\s+(?:(?:SEPA|INST|INSTANTANE|EUROPEEN|PERM|PERMANENT|RECU|EMIS)\s+)*"
)
_CUT_MARKERS = re.compile(r"\s(?:MOTIF|REF|ID|ECH|MDT|RUM|LIB|DATE)\b.*$")
_NOISE = re.compile(r"\b\d{2}/\d{2}(?:/\d{2,4})?\b|\b\d{2}H\d{2}\b|\bX\d{4}\b|\b\d{4,}\b|[*/:]")
_TRAILING_NUMBERS = re.compile(r"(?:\s+\d{1,3})+$")


def merchant_name(tx: Transaction) -> str:
    if tx.counterparty:
        return pretty_name(" ".join(tx.counterparty.split()[:4]))
    raw = " ".join(strip_accents(tx.label).upper().split())
    if raw.startswith(("RETRAIT DAB", "RETRAIT GAB", "RETRAIT ")):
        return "Retrait d'espèces"
    party = _PARTY.match(raw)
    if party:
        name = party.group(1)
    else:
        name = _CARD_PREFIX.sub("", raw, count=1)
        name = _DEBIT_PREFIX.sub("", name, count=1)
        name = _TRANSFER_PREFIX.sub("", name, count=1)
    name = _CUT_MARKERS.sub("", name)
    name = _TRAILING_NUMBERS.sub("", " ".join(_NOISE.sub(" ", name).split())).strip()
    words = name.split()[:3] or raw.split()[:3]
    return pretty_name(" ".join(words)) if words else "Opération"


def merchant_key(name: str) -> str:
    return " ".join(normalize_text(name).split()[:2])


@dataclass(frozen=True)
class Categorized:
    tx: Transaction
    category: str
    kind: str
    merchant: str
    merchant_key: str


class Categorizer:
    def __init__(
        self,
        *,
        own_ibans: Iterable[str] = (),
        user_rules: Mapping[str, Iterable[str]] | None = None,
    ):
        self.own_ibans = {iban.replace(" ", "").upper() for iban in own_ibans if iban}
        self._user = KeywordIndex(user_rules or {})
        self._expense = KeywordIndex(EXPENSE_RULES)
        self._income = KeywordIndex(INCOME_RULES)

    @classmethod
    def load(cls, rules_file: Path | None, *, own_ibans: Iterable[str] = ()) -> Categorizer:
        return cls(own_ibans=own_ibans, user_rules=load_user_rules(rules_file))

    def category(self, tx: Transaction) -> str:
        if tx.counterparty_iban and tx.counterparty_iban in self.own_ibans:
            return INTERNAL
        text = normalize_text(f"{tx.label} {tx.counterparty}")
        user = self._user.match(text)
        if user:
            return user
        if tx.is_debit:
            return self._expense.match(text) or category_from_mcc(tx.mcc) or OTHER_EXPENSES
        # Crédit : revenu, sinon remboursement d'une dépense (il vient alors la réduire).
        return (
            self._income.match(text)
            or self._expense.match(text)
            or category_from_mcc(tx.mcc)
            or OTHER_INCOME
        )

    def categorize(self, tx: Transaction) -> Categorized:
        category = self.category(tx)
        merchant = merchant_name(tx)
        return Categorized(tx, category, kind_of(category, tx), merchant, merchant_key(merchant))


def load_user_rules(rules_file: Path | None) -> dict[str, tuple[str, ...]]:
    """Fichier JSON {"Catégorie": ["MOT-CLÉ", ...]} ; les clés commençant par « _ » sont ignorées."""
    if rules_file is None:
        return {}
    try:
        data = json.loads(rules_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Fichier de catégories introuvable : {rules_file}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Fichier de catégories invalide ({rules_file}) : {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f'{rules_file} doit contenir un objet JSON {{"Catégorie": [...]}}')
    rules: dict[str, tuple[str, ...]] = {}
    for category, keywords in data.items():
        if category.startswith("_"):
            continue
        if not isinstance(keywords, list) or not all(isinstance(k, str) for k in keywords):
            raise ConfigError(f"{rules_file} : « {category} » doit être une liste de mots-clés")
        rules[category] = tuple(keywords)
    return rules
