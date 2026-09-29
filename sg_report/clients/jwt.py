"""Jetons JWT RS256 pour Enable Banking, signés via `cryptography` ou la commande `openssl`.

L'add-on OpenClaw fournit python3 et openssl mais ni pip ni venv : la signature doit
fonctionner sans paquet Python supplémentaire.
"""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path

from ..errors import ConfigError

JWT_TTL_SECONDS = 3600


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def sign_rs256(message: bytes, key_path: Path, *, backend: str = "auto") -> bytes:
    if backend not in ("auto", "cryptography", "openssl"):
        raise ValueError(f"backend inconnu : {backend}")
    if backend != "openssl":
        try:
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import padding
        except ImportError:
            if backend == "cryptography":
                raise ConfigError("Le module Python « cryptography » n'est pas installé.") from None
        else:
            try:
                key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
                return key.sign(message, padding.PKCS1v15(), hashes.SHA256())
            except (OSError, TypeError, ValueError) as exc:
                raise ConfigError(
                    f"Clé privée Enable Banking inutilisable ({key_path}) : {exc}"
                ) from exc
    return _sign_with_openssl(message, key_path)


def _sign_with_openssl(message: bytes, key_path: Path) -> bytes:
    openssl = shutil.which("openssl")
    if openssl is None:
        raise ConfigError(
            "Impossible de signer les requêtes Enable Banking : "
            "installez la commande openssl ou le module Python cryptography."
        )
    result = subprocess.run(
        [openssl, "dgst", "-sha256", "-sign", str(key_path)],
        input=message,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise ConfigError(f"Signature openssl impossible avec la clé {key_path} : {detail}")
    return result.stdout


def make_jwt(
    app_id: str,
    key_path: Path,
    *,
    issued_at: int,
    ttl: int = JWT_TTL_SECONDS,
    backend: str = "auto",
) -> str:
    header = {"typ": "JWT", "alg": "RS256", "kid": app_id}
    payload = {
        "iss": "enablebanking.com",
        "aud": "api.enablebanking.com",
        "iat": issued_at,
        "exp": issued_at + ttl,
    }
    signing_input = ".".join(
        b64url(json.dumps(part, separators=(",", ":")).encode("utf-8"))
        for part in (header, payload)
    )
    signature = sign_rs256(signing_input.encode("ascii"), key_path, backend=backend)
    return f"{signing_input}.{b64url(signature)}"
