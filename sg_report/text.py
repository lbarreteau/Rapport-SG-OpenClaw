"""Normalisation des libellés bancaires et mise en forme des noms."""

from __future__ import annotations

import re
import unicodedata

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

ACRONYMS = frozenset(
    {
        "AG2R", "APRR", "AXA", "BHV", "BNP", "BP", "CAF", "CIC", "CPAM", "DAB", "DGFIP", "EDF",
        "GMF", "HSBC", "IKEA", "ING", "KFC", "LCL", "MAAF", "MACIF", "MAIF", "MGEN", "MK2", "MMA",
        "OCS", "RATP", "SA", "SARL", "SAS", "SCI", "SFR", "SG", "SNCF", "TCL", "TER", "TGV", "UGC",
        "URSSAF",
    }
)  # fmt: skip
_LOWER_WORDS = frozenset({"AU", "AUX", "D", "DE", "DES", "DU", "EN", "ET", "L", "LA", "LE", "LES"})


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    """« Café d'Été / Paris » -> « CAFE D ETE PARIS »."""
    return _NON_ALNUM.sub(" ", strip_accents(value).upper()).strip()


def pretty_name(value: str) -> str:
    """« CARREFOUR CITY PARIS » -> « Carrefour City Paris », en gardant les sigles connus."""
    words = []
    for index, word in enumerate(value.split()):
        bare = word.upper().strip(".,")
        if bare in ACRONYMS or any(ch.isdigit() for ch in word):
            words.append(word.upper())
        elif index > 0 and bare in _LOWER_WORDS:
            words.append(word.lower())
        else:
            words.append(_capitalize(word))
    return " ".join(words)


def _capitalize(word: str) -> str:
    return "-".join(
        "'".join(part.capitalize() for part in chunk.split("'"))
        for chunk in word.lower().split("-")
    )
