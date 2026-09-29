import json

import pytest

from sg_report import cli
from sg_report.config import Config, parse_env_file
from sg_report.errors import ConfigError


def test_demo_run_prints_a_weekly_report(isolated_env, capsys):
    assert cli.main(["--mock", "run"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("**Rapport hebdo Société Générale (démo)**")
    for section in ("**Soldes**", "**Bilan de la semaine**", "**Dépenses par catégorie**"):
        assert section in out
    assert "Compte courant (…5821)" in out and "Livret A (…7734)" in out
    assert (isolated_env / "data" / "demo" / "ledger.json").is_file()


def test_mock_flag_is_accepted_after_the_command(isolated_env, capsys):
    assert cli.main(["report", "--mock", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["demo"] is True
    assert data["period"]["days"] == 7


def test_status_and_fetch_in_demo_mode(isolated_env, capsys):
    assert cli.main(["--mock", "fetch", "--days", "30"]) == 0
    assert "Synchronisation terminée : 2 compte(s)" in capsys.readouterr().out
    assert cli.main(["--mock", "status"]) == 0
    out = capsys.readouterr().out
    assert "Mode : démo" in out and "Accès bancaire : valable jusqu'au" in out


def test_missing_bank_access_exits_with_code_2(isolated_env, capsys):
    assert cli.main(["run"]) == 2
    assert "Aucun accès bancaire configuré" in capsys.readouterr().err
    assert cli.main(["link"]) == 2
    assert "ENABLE_BANKING_APP_ID" in capsys.readouterr().err


def test_ha_channel_sends_the_report(isolated_env, capsys, monkeypatch):
    sent = {}

    def fake_send(config, *, title, message, summary):
        sent.update(title=title, message=message, summary=summary)
        return ["persistent_notification.create"]

    monkeypatch.setattr(cli, "send_home_assistant", fake_send)
    monkeypatch.setenv("REPORT_CHANNEL", "ha")
    assert cli.main(["--mock", "run"]) == 0
    captured = capsys.readouterr()
    assert sent["message"] == captured.out.rstrip("\n")
    assert sent["title"].startswith("Rapport SG du ")
    assert "Notification Home Assistant envoyée" in captured.err

    sent.clear()
    assert cli.main(["--mock", "run", "--no-send"]) == 0
    assert sent == {}


def test_invalid_arguments_are_rejected(isolated_env):
    with pytest.raises(SystemExit):
        cli.main(["report", "--days", "0"])
    with pytest.raises(SystemExit):
        cli.main(["report", "--end", "29/09/2026"])


def test_env_file_parsing(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# commentaire\n"
        "export SG_REPORT_MOCK=1\n"
        "ENABLE_BANKING_ASPSP_NAME=Société Générale\n"
        'ENABLE_BANKING_REDIRECT_URL="https://localhost/cb#frag"\n'
        "SG_REPORT_LOW_BALANCE=150,5 # seuil\n"
        "LIGNE_INVALIDE\n",
        encoding="utf-8",
    )
    values = parse_env_file(env)
    assert values["SG_REPORT_MOCK"] == "1"
    assert values["ENABLE_BANKING_ASPSP_NAME"] == "Société Générale"
    assert values["ENABLE_BANKING_REDIRECT_URL"] == "https://localhost/cb#frag"
    assert values["SG_REPORT_LOW_BALANCE"] == "150,5"
    assert "LIGNE_INVALIDE" not in values

    config = Config.load(env_file=env, environ={"SG_REPORT_DATA_DIR": str(tmp_path / "d")})
    assert config.mock is True
    assert config.data_dir == tmp_path / "d" / "demo"
    assert str(config.low_balance) == "150.5"


@pytest.mark.parametrize(
    "overrides",
    [
        {"REPORT_CHANNEL": "email"},
        {"ENABLE_BANKING_PSU_TYPE": "corporate"},
        {"ENABLE_BANKING_CONSENT_DAYS": "365"},
        {"SG_REPORT_TIMEZONE": "Mars/Olympus"},
        {"SG_REPORT_LOW_BALANCE": "beaucoup"},
    ],
)
def test_invalid_configuration(make_config, overrides):
    with pytest.raises(ConfigError):
        make_config(**overrides)
