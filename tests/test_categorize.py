import json
from datetime import date

import pytest
from helpers import CURRENT, SAVINGS, tx

from sg_report.categorize import (
    INTERNAL,
    OTHER_EXPENSES,
    OTHER_INCOME,
    Categorizer,
    load_user_rules,
    merchant_key,
    merchant_name,
)
from sg_report.errors import ConfigError

DAY = date(2026, 9, 21)


@pytest.fixture
def categorizer():
    return Categorizer(own_ibans=[CURRENT.iban, SAVINGS.iban])


@pytest.mark.parametrize(
    ("label", "amount", "expected"),
    [
        ("CARTE X4821 21/09 CARREFOUR CITY PARIS 11", -23.4, "Courses"),
        ("CARTE X4821 21/09 BOULANGERIE LA FOURNEE", -3.2, "Courses"),
        ("CARTE X4821 21/09 GRAND FRAIS", -40, "Courses"),
        ("CARTE X4821 21/09 BOULANGER", -299, "Shopping"),
        ("CARTE X4821 21/09 UBER EATS", -18, "Restaurants & sorties"),
        ("CARTE X4821 21/09 UBER *TRIP", -12, "Transport"),
        ("CARTE X4821 21/09 TOTALENERGIES STATION", -60, "Transport"),
        ("PRLV SEPA TOTALENERGIES ELECTRICITE ET GAZ FRANCE", -80, "Énergie & télécom"),
        ("PRLV SEPA EDF CLIENTS PARTICULIERS", -68.4, "Énergie & télécom"),
        ("CARTE X4821 10/09 NETFLIX.COM", -13.49, "Abonnements"),
        ("CARTE X4821 10/09 AMAZON PRIME FR", -6.99, "Abonnements"),
        ("CARTE X4821 10/09 AMAZON PAYMENTS", -45, "Shopping"),
        ("CARTE X4821 12/09 DISNEYLAND PARIS", -89, "Loisirs & voyages"),
        ("CARTE X4821 12/09 DISNEY PLUS", -11.99, "Abonnements"),
        ("VIR PERM POUR: SCI DES TILLEULS MOTIF: LOYER SEPTEMBRE", -950, "Logement"),
        ("PRLV SEPA DGFIP IMPOT PRELEVEMENT A LA SOURCE", -120, "Impôts & administration"),
        ("COTISATION JAZZ", -8.9, "Banque & frais"),
        ("RETRAIT DAB 20/09 14H32 PARIS 11", -60, "Retraits d'espèces"),
        ("CARTE X4821 10/09 MAGASIN INCONNU", -20, OTHER_EXPENSES),
        ("VIR EMIS POUR: NOUNOU MOTIF: SALAIRE SEPTEMBRE", -600, OTHER_EXPENSES),
    ],
)
def test_expense_categories(categorizer, label, amount, expected):
    item = categorizer.categorize(tx(label, amount, DAY))
    assert item.category == expected
    assert item.kind == "expense"


@pytest.mark.parametrize(
    ("label", "expected", "kind"),
    [
        ("VIR RECU 7284519302 DE: ACME CONSEIL SAS MOTIF: SALAIRE SEPTEMBRE", "Salaire", "income"),
        ("VIR RECU DE: CAF DE PARIS", "Aides & allocations", "income"),
        ("VIR RECU DE: JEAN DUPONT", OTHER_INCOME, "income"),
        # Un remboursement réduit la catégorie de dépense correspondante.
        ("VIR RECU DE: CPAM PARIS MOTIF: REMBT SOINS", "Santé", "expense"),
        ("VIR INST RECU DE: JULIE BERNARD MOTIF: RESTO", "Restaurants & sorties", "expense"),
    ],
)
def test_credit_categories(categorizer, label, expected, kind):
    item = categorizer.categorize(tx(label, 25, DAY))
    assert (item.category, item.kind) == (expected, kind)


def test_transfers_between_own_accounts_are_internal(categorizer):
    by_iban = categorizer.categorize(tx("VIR SEPA EMIS", -200, DAY, counterparty_iban=SAVINGS.iban))
    by_keyword = categorizer.categorize(tx("VIR PERM POUR: M TEST LIVRET A", -200, DAY))
    assert by_iban.category == by_keyword.category == INTERNAL
    assert by_iban.kind == "transfer"


def test_mcc_is_used_when_no_keyword_matches(categorizer):
    item = categorizer.categorize(tx("CARTE X4821 21/09 CHEZ MARCEL", -30, DAY, mcc="5812"))
    assert item.category == "Restaurants & sorties"


def test_user_rules_take_precedence(tmp_path):
    rules = tmp_path / "categories.json"
    rules.write_text(
        json.dumps({"_aide": "ignoré", "Enfants": ["CRECHE*"], "Courses": ["UBER EATS"]}),
        encoding="utf-8",
    )
    categorizer = Categorizer.load(rules)
    assert categorizer.category(tx("PRLV SEPA CRECHE LES PETITS LOUPS", -300, DAY)) == "Enfants"
    assert categorizer.category(tx("CARTE X4821 21/09 UBER EATS", -18, DAY)) == "Courses"


def test_user_rules_validation(tmp_path):
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_user_rules(bad_json)
    bad_shape = tmp_path / "shape.json"
    bad_shape.write_text(json.dumps({"Courses": "LIDL"}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_user_rules(bad_shape)
    with pytest.raises(ConfigError):
        load_user_rules(tmp_path / "absent.json")
    assert load_user_rules(None) == {}


@pytest.mark.parametrize(
    ("label", "counterparty", "expected"),
    [
        ("CARTE X4821 23/09 CARREFOUR CITY PARIS 11", "", "Carrefour City Paris"),
        ("CARTE X4821 10/09 NETFLIX.COM", "", "Netflix.com"),
        ("CARTE X4821 REMBT 12/09 AMAZON PAYMENTS", "", "Amazon Payments"),
        ("VIR RECU 7284519302 DE: ACME CONSEIL SAS MOTIF: SALAIRE", "", "Acme Conseil SAS"),
        ("PRLV SEPA FREE MOBILE ECH/150926 ID EMETTEUR/FR12ZZZ", "", "Free Mobile"),
        ("RETRAIT DAB 20/09 14H32 PARIS 11", "", "Retrait d'espèces"),
        ("COTISATION JAZZ", "", "Cotisation Jazz"),
        ("PRLV SEPA EDF CLIENTS PARTICULIERS", "EDF", "EDF"),
        ("VIR PERM POUR: SCI DES TILLEULS MOTIF: LOYER", "SCI DES TILLEULS", "SCI des Tilleuls"),
    ],
)
def test_merchant_name(label, counterparty, expected):
    assert merchant_name(tx(label, -10, DAY, counterparty=counterparty)) == expected


def test_merchant_key_groups_variants():
    assert merchant_key("Carrefour City Paris") == merchant_key("Carrefour City Lyon")
    assert merchant_key("Carrefour City Paris") != merchant_key("Carrefour Market")
