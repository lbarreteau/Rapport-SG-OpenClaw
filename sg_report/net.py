"""Client HTTP minimal sur urllib : aucune dépendance à installer dans l'add-on OpenClaw."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import __version__

USER_AGENT = f"sg-report/{__version__}"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes = b""

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None

    def text(self, limit: int = 300) -> str:
        return self.body.decode("utf-8", errors="replace")[:limit]


# (méthode, url, en-têtes, corps, timeout) -> réponse ; les erreurs réseau lèvent OSError.
Transport = Callable[[str, str, dict[str, str], bytes | None, float], HttpResponse]


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> HttpResponse:
    request = urllib.request.Request(
        url, data=body, method=method, headers={"User-Agent": USER_AGENT, **headers}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read() or b"")
