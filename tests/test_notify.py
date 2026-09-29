import json

import pytest

from sg_report.errors import NotifyError
from sg_report.net import HttpResponse
from sg_report.notify import send_home_assistant


class Recorder:
    def __init__(self, status=200):
        self.status = status
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, json.loads(body)))
        return HttpResponse(self.status, b"[]")


def test_full_report_and_push_summary(make_config):
    config = make_config(HA_TOKEN="tok", HA_PUSH_SERVICE="notify.mobile_app_pixel")
    transport = Recorder()
    sent = send_home_assistant(
        config, title="Rapport", message="complet", summary="court", transport=transport
    )

    assert sent == ["persistent_notification.create", "notify.mobile_app_pixel"]
    (_, url1, headers, body1), (_, url2, _, body2) = transport.calls
    assert url1 == "http://127.0.0.1:8123/api/services/persistent_notification/create"
    assert headers["Authorization"] == "Bearer tok"
    assert body1 == {"title": "Rapport", "message": "complet", "notification_id": "sg_report"}
    assert url2.endswith("/api/services/notify/mobile_app_pixel")
    assert body2 == {"title": "Rapport", "message": "court"}


def test_token_from_addon_secret_file(tmp_path, make_config):
    token_file = tmp_path / "homeassistant.token"
    token_file.write_text("from-file\n")
    config = make_config(HA_TOKEN_FILE=str(token_file))
    assert config.ha_token == "from-file"


@pytest.mark.parametrize(
    ("overrides", "status", "fragment"),
    [
        ({"HA_TOKEN_FILE": "/nonexistent"}, 200, "Aucun jeton"),
        ({"HA_TOKEN": "tok", "HA_NOTIFY_SERVICE": "invalide"}, 200, "domaine.service"),
        ({"HA_TOKEN": "tok"}, 401, "HTTP 401"),
    ],
)
def test_errors(make_config, overrides, status, fragment):
    with pytest.raises(NotifyError, match=fragment):
        send_home_assistant(
            make_config(**overrides),
            title="t",
            message="m",
            summary="s",
            transport=Recorder(status),
        )
