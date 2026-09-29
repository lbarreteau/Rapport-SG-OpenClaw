from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from sg_report.config import Config


@pytest.fixture
def make_config(tmp_path: Path):
    """Config isolée du .env du dépôt, avec un dossier de données temporaire."""

    def factory(**overrides: str) -> Config:
        environ = {"SG_REPORT_DATA_DIR": str(tmp_path / "data"), **overrides}
        return Config.load(env_file=tmp_path / "absent.env", environ=environ)

    return factory


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pour la CLI : pas de .env du dépôt, données dans tmp_path."""
    for name in ("SG_REPORT_MOCK", "REPORT_CHANNEL", "HA_TOKEN", "SG_REPORT_RULES_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SG_REPORT_ENV_FILE", str(tmp_path / "absent.env"))
    monkeypatch.setenv("SG_REPORT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HA_TOKEN_FILE", str(tmp_path / "absent.token"))
    return tmp_path


@pytest.fixture(scope="session")
def rsa_key(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("openssl") is None:
        pytest.skip("openssl indisponible")
    key = tmp_path_factory.mktemp("keys") / "private.pem"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    return key
