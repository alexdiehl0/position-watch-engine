import base64
import json
import re

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from position_watch.dashboard import publish


def _open(payload, kek, box):
    content_key = AESGCM(kek).decrypt(base64.b64decode(payload[box]["iv"]), base64.b64decode(payload[box]["key"]), None)
    return (
        AESGCM(content_key).decrypt(base64.b64decode(payload["iv"]), base64.b64decode(payload["data"]), None).decode()
    )


def _decrypt(payload, passcode):
    kek = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=base64.b64decode(payload["salt"]),
                     iterations=payload["iter"]).derive(passcode.encode())  # fmt: skip
    return _open(payload, kek, "by_passcode")


def test_private_link_opens_the_same_page_without_the_passcode():
    payload = publish.encrypt("<p>secret portfolio</p>", "correct horse battery staple")
    link = publish.private_link("https://owner.github.io/dash/", "correct horse battery staple")
    assert link.startswith("https://owner.github.io/dash/#k=") and "correct" not in link
    token = link.split("#k=")[1]
    kek = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    assert _open(payload, kek, "by_link") == "<p>secret portfolio</p>"
    assert publish.private_link("https://x/", "other passcode") != link


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
