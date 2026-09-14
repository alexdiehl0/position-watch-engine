"""Publishes the dashboard as a locked page for GitHub Pages.

The page lives in a separate public repository because GitHub Pages sites are
public even when built from a private repo. So the whole dashboard is
encrypted before it leaves this machine, with a fresh random key (AES-256-GCM)
each publish. That key is stored in the page twice, each copy locked:
  - by DASHBOARD_PASSCODE (PBKDF2-SHA256, 600,000 rounds), for typing it in;
  - by a link key derived from the passcode (HMAC-SHA256), which the daily
    email puts after the `#` of the dashboard address. Browsers never send
    that part of an address to a server, so GitHub never sees it; tapping the
    email's button opens the dashboard without typing anything.
Anyone with the email can therefore open the dashboard -- the email already
carries the portfolio summary. Changing the passcode invalidates every old
link. Without the passcode or a link the page shows nothing.

DASHBOARD_PASSCODE is a secret like the API keys. It is never written to the
repo, a report, an email or a log. If it is missing, nothing is published --
there is no unencrypted fallback.

    python -m position_watch dashboard publish <path to the dashboard repo checkout>
"""

import base64
import hashlib
import hmac
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from position_watch import settings
from position_watch.dashboard import render

ITERATIONS = 600_000
LINK_CONTEXT = b"position-watch dashboard link v2"


def link_key(passcode: str) -> bytes:
    return hmac.new(passcode.encode(), LINK_CONTEXT, hashlib.sha256).digest()


def private_link(base_url: str, passcode: str) -> str:
    """The dashboard address with its link key after the `#` (never sent to a server)."""
    token = base64.urlsafe_b64encode(link_key(passcode)).decode().rstrip("=")
    return f"{base_url}#k={token}"


def email_link() -> str | None:
    """The private link for the email, or the plain address if there's no passcode (None if no dashboard)."""
    base = settings.dashboard_url()
    passcode = os.environ.get("DASHBOARD_PASSCODE")
    return private_link(base, passcode) if base and passcode else base


def _wrap(key: bytes, secret: bytes) -> dict:
    nonce = os.urandom(12)
    return {"iv": _b64(nonce), "key": _b64(AESGCM(key).encrypt(nonce, secret, None))}


def encrypt(plaintext: str, passcode: str) -> dict:
    content_key, nonce, salt = os.urandom(32), os.urandom(12), os.urandom(16)
    pass_key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS).derive(
        passcode.encode()
    )
    return {
        "v": 2,
        "iter": ITERATIONS,
        "salt": _b64(salt),
        "iv": _b64(nonce),
        "data": _b64(AESGCM(content_key).encrypt(nonce, plaintext.encode(), None)),  # ciphertext || tag
        "by_passcode": _wrap(pass_key, content_key),
        "by_link": _wrap(link_key(passcode), content_key),
    }


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


LOCKED_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>AI Stock Portfolio Review</title>
<style>
  :root { --bg: #f5f6f7; --surface: #ffffff; --line: #e1e4e8; --ink: #11161c; --ink-3: #7d8792;
          --btn: #1c5cab; --btn-ink: #ffffff; --crit: #a42828; color-scheme: light; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #0d0f12; --surface: #16191d; --line: #2a2f35; --ink: #f2f4f6; --ink-3: #858e98;
            --btn: #86b6ef; --btn-ink: #0b1624; --crit: #ee7c7c; color-scheme: dark; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px 16px;
         background: var(--bg); color: var(--ink); font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
  form { width: 100%%; max-width: 360px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px;
         padding: 24px; display: flex; flex-direction: column; gap: 12px; }
  h1 { margin: 0; font: 600 1.4rem/1.2 Georgia, "Times New Roman", serif; }
  p { margin: 0; color: var(--ink-3); font-size: 0.85rem; }
  label { font-size: 0.72rem; letter-spacing: 0.06em; text-transform: uppercase; color: var(--ink-3); }
  input[type=password] { width: 100%%; padding: 10px 12px; border-radius: 7px; border: 1px solid var(--line);
                         background: var(--bg); color: var(--ink); font: inherit; }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--btn); outline-offset: 2px; }
  .row { display: flex; align-items: center; gap: 8px; font-size: 0.85rem; }
  button { padding: 10px 18px; border: 0; border-radius: 7px; background: var(--btn); color: var(--btn-ink);
           font: inherit; font-weight: 600; cursor: pointer; }
  button:disabled { opacity: 0.6; cursor: progress; }
  #msg { min-height: 1.3em; color: var(--crit); font-size: 0.85rem; }
</style>
</head>
<body>
<form id="unlock">
  <h1>AI Stock Portfolio Review</h1>
  <p>Updated %(updated)s. Open it from the button in today&rsquo;s email, or enter the passcode.</p>
  <label for="pass">Passcode</label>
  <input type="password" id="pass" autocomplete="current-password" required>
  <label class="row" for="remember"><input type="checkbox" id="remember"> Remember on this device</label>
  <button type="submit" id="go">Open dashboard</button>
  <div id="msg" role="status" aria-live="polite"></div>
  <noscript><p>This page needs JavaScript to unlock.</p></noscript>
</form>
<script>
var P = %(payload)s;
(function () {
  var KEY = 'position-watch-passcode';
  function bytes(s) { return Uint8Array.from(atob(s), function (c) { return c.charCodeAt(0); }); }
  function fromUrl(s) { s = s.replace(/-/g, '+').replace(/_/g, '/'); while (s.length %% 4) s += '='; return bytes(s); }
  async function aes(raw) { return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt']); }
  async function unwrap(kek, box) {
    return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes(box.iv) }, kek, bytes(box.key)));
  }
  async function show(contentKey) {
    var plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: bytes(P.iv) }, await aes(contentKey), bytes(P.data));
    var page = new TextDecoder().decode(plain);
    document.open(); document.write(page); document.close();
  }
  async function openWithPasscode(pass) {
    var base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveKey']);
    var kek = await crypto.subtle.deriveKey({ name: 'PBKDF2', salt: bytes(P.salt), iterations: P.iter, hash: 'SHA-256' },
                                            base, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
    await show(await unwrap(kek, P.by_passcode));
  }
  async function openWithLink(token) { await show(await unwrap(await aes(fromUrl(token)), P.by_link)); }
  function store(fn) { try { return fn(window.localStorage); } catch (e) { return null; } }
  var form = document.getElementById('unlock'), input = document.getElementById('pass');
  var btn = document.getElementById('go'), msg = document.getElementById('msg');
  form.addEventListener('submit', async function (ev) {
    ev.preventDefault();
    btn.disabled = true; msg.textContent = '';
    try {
      await openWithPasscode(input.value);
      if (document.getElementById('remember') && document.getElementById('remember').checked)
        store(function (s) { s.setItem(KEY, input.value); });
    } catch (e) {
      msg.textContent = 'That passcode didn\\u2019t open it. Check it and try again.';
      btn.disabled = false; input.select();
    }
  });
  var match = /[#&]k=([A-Za-z0-9_-]+)/.exec(location.hash);
  if (match) {
    history.replaceState(null, '', location.pathname + location.search);  // keep the key out of the address bar
    openWithLink(match[1]).then(null, function () {
      msg.textContent = 'This link is from an older email. Open today\\u2019s email, or enter the passcode.';
    });
    return;
  }
  var saved = store(function (s) { return s.getItem(KEY); });
  if (saved) {
    openWithPasscode(saved).then(null, function () { store(function (s) { s.removeItem(KEY); }); });
  }
})();
</script>
</body>
</html>
"""


class PasscodeMissing(RuntimeError):
    """Raised instead of publishing when DASHBOARD_PASSCODE is not set."""


def locked_page(dashboard_html: str, passcode: str) -> str:
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return LOCKED_PAGE % {"payload": json.dumps(encrypt(dashboard_html, passcode)), "updated": html.escape(updated)}


def publish(out_dir) -> Path:
    """Writes the locked dashboard into `out_dir` (the position-watch checkout)
    and returns the page's path. Pushing it is left to the caller."""
    passcode = os.environ.get("DASHBOARD_PASSCODE")
    if not passcode:
        raise PasscodeMissing(
            "DASHBOARD_PASSCODE is not set (.env locally, a secret in the cloud environment); "
            "nothing was published. The dashboard is never published unencrypted."
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(locked_page(render.build_page(), passcode))
    (out / ".nojekyll").touch()
    return out / "index.html"
