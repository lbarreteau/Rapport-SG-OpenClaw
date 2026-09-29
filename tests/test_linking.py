from datetime import UTC, datetime, timedelta

import pytest

from sg_report.errors import ConfigError, ConsentError
from sg_report.linking import complete_link, find_aspsp, parse_callback, start_link
from sg_report.storage import Store

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
SG = {"name": "Société Générale", "country": "FR", "maximum_consent_validity": 90 * 86400}


class FakeClient:
    def __init__(self, session_response=None):
        self.auth_request = None
        self.codes = []
        self.session_response = session_response or {
            "session_id": "sess-1",
            "accounts": [
                {
                    "uid": "u1",
                    "account_id": {"iban": "FR7630003000000000000001111"},
                    "product": "Compte courant",
                    "cash_account_type": "CACC",
                },
            ],
            "aspsp": {"name": "Société Générale", "country": "FR"},
            "psu_type": "personal",
            "access": {"valid_until": "2026-12-28T11:00:00+00:00"},
        }

    def list_aspsps(self, country, psu_type=None):
        return [{"name": "Societe Generale Professionnels"}, SG, {"name": "BNP Paribas"}]

    def start_authorization(self, **kwargs):
        self.auth_request = kwargs
        return {
            "url": "https://auth.enablebanking.com/ais/start?sessionid=abc",
            "authorization_id": "abc",
        }

    def create_session(self, code):
        self.codes.append(code)
        return self.session_response


def test_find_aspsp_ignores_accents_and_case():
    assert find_aspsp([SG], "societe generale") is SG
    with pytest.raises(ConfigError, match="Noms proches : « Société Générale »"):
        find_aspsp([SG], "Societe Generales")


def test_start_link_caps_consent_to_bank_maximum(tmp_path, make_config):
    client, store = FakeClient(), Store(tmp_path)
    config = make_config(
        ENABLE_BANKING_CONSENT_DAYS="180", ENABLE_BANKING_REDIRECT_URL="https://localhost/cb"
    )
    url = start_link(client, store, config, now=NOW)

    assert url.startswith("https://auth.enablebanking.com/")
    request = client.auth_request
    assert request["aspsp_name"] == "Société Générale"
    assert request["redirect_url"] == "https://localhost/cb"
    assert request["psu_type"] == "personal"
    assert request["valid_until"] == NOW + timedelta(days=90) - timedelta(hours=1)
    assert store.load_pending()["state"] == request["state"]


def test_complete_link_saves_session_and_clears_pending(tmp_path, make_config):
    client, store = FakeClient(), Store(tmp_path)
    start_link(client, store, make_config(), now=NOW)
    state = store.load_pending()["state"]

    session = complete_link(
        client, store, now=NOW, callback_url=f"https://localhost/cb?state={state}&code=the-code"
    )
    assert client.codes == ["the-code"]
    assert session.session_id == "sess-1"
    assert session.accounts[0].key == "FR7630003000000000000001111"
    assert session.valid_until == datetime(2026, 12, 28, 11, 0, tzinfo=UTC)
    assert store.load_session() == session
    assert store.load_pending() is None


def test_complete_link_rejects_mismatched_or_refused_callbacks(tmp_path, make_config):
    client, store = FakeClient(), Store(tmp_path)
    start_link(client, store, make_config(), now=NOW)
    with pytest.raises(ConsentError, match="ne correspond pas"):
        complete_link(
            client, store, now=NOW, callback_url="https://localhost/cb?state=other&code=x"
        )
    with pytest.raises(ConsentError, match="Cancelled by user"):
        complete_link(
            client,
            store,
            now=NOW,
            callback_url="https://localhost/cb?error=access_denied&error_description=Cancelled+by+user",
        )
    with pytest.raises(ConfigError, match="code="):
        complete_link(client, store, now=NOW, callback_url="https://localhost/cb?state=x")
    assert client.codes == []


def test_complete_link_without_accounts_explains_restricted_mode(tmp_path):
    client = FakeClient(session_response={"session_id": "s", "accounts": []})
    with pytest.raises(ConfigError, match="mode restreint"):
        complete_link(client, Store(tmp_path), now=NOW, code="abc")


def test_parse_callback():
    params = parse_callback("https://localhost/cb?code=c1&state=s1")
    assert (params.code, params.state, params.error) == ("c1", "s1", None)
    assert parse_callback("https://localhost/cb#code=c2").code == "c2"
