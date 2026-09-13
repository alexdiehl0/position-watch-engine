import base64
import json
import re

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from position_watch.dashboard import publish


def _decrypt(payload, passcode):
    raw = {k: base64.b64decode(payload[k]) for k in ("salt", "iv", "data")}
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=raw["salt"], iterations=payload["iter"]).derive(
        passcode.encode()
    )
    return AESGCM(key).decrypt(raw["iv"], raw["data"], None).decode()


def test_round_trip_and_wrong_passcode():
    payload = publish.encrypt("<p>secret portfolio</p>", "correct horse battery staple")
    assert _decrypt(payload, "correct horse battery staple") == "<p>secret portfolio</p>"
    with pytest.raises(InvalidTag):
        _decrypt(payload, "wrong")


def test_refuses_to_publish_without_passcode(tmp_path):
    with pytest.raises(publish.PasscodeMissing):
        publish.publish(tmp_path)
    assert not (tmp_path / "index.html").exists()


def test_locked_page_leaks_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSCODE", "test passcode")
    page = publish.publish(tmp_path).read_text()

    payload = json.loads(re.search(r"var P = (\{.*?\});", page, re.S).group(1))
    outside = page.replace(payload["data"], "")
    for secret in ("US Stock Inc", "USDS", "1,500", "Fairly valued"):
        assert secret not in outside
    assert "US Stock Inc" in _decrypt(payload, "test passcode")
    assert (tmp_path / ".nojekyll").exists()
