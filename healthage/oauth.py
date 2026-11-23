"""
OAuth 2.0 for installed apps (loopback redirect + PKCE), standard library only.

Each user brings their own Google Cloud OAuth client ("Desktop app" type),
so no shared secret ships with this code. Google lets Desktop clients redirect
to any loopback port, so the callback is http://127.0.0.1:<port>/oauth/callback.

While the OAuth consent screen is in Testing mode, Google expires refresh
tokens after 7 days; the app then asks the user to reconnect.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from . import store
from .google_health import SCOPES

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


class AuthError(Exception):
    pass


def parse_client(raw: str) -> dict:
    """Accept the JSON Google Cloud downloads ({"installed": {...}} or
    {"web": {...}}) or a bare {"client_id", "client_secret"}."""
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AuthError("That is not valid JSON. Paste the whole file Google Cloud downloaded.") from e
    node = obj.get("installed") or obj.get("web") or obj
    cid, secret = node.get("client_id"), node.get("client_secret")
    if not cid or not secret or not str(cid).endswith(".apps.googleusercontent.com"):
        raise AuthError("No client_id / client_secret found. Use an OAuth client of type Desktop app.")
    return {"client_id": cid, "client_secret": secret, "type": "web" if "web" in obj else "installed"}


def client() -> Optional[dict]:
    return store.read("client.json")


def save_client(raw: str) -> dict:
    c = parse_client(raw)
    store.write("client.json", c)
    return c


_pending: dict = {}


def auth_url(redirect_uri: str) -> str:
    c = client()
    if not c:
        raise AuthError("Add your Google OAuth client first (Settings).")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    _pending.clear()
    _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "t": time.time()}
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": c["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})


def _post(url: str, fields: dict) -> dict:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except (json.JSONDecodeError, OSError):
            err = {}
        raise AuthError(err.get("error_description") or err.get("error") or f"HTTP {e.code}") from None


def finish(state: str, code: str) -> None:
    p = _pending.pop(state, None)
    if not p or time.time() - p["t"] > 900:
        raise AuthError("This sign-in link expired or was not started here. Click Connect again.")
    c = client()
    tok = _post(TOKEN_URL, {"code": code, "client_id": c["client_id"], "client_secret": c["client_secret"],
                            "redirect_uri": p["redirect_uri"], "grant_type": "authorization_code",
                            "code_verifier": p["verifier"]})
    tok["expires_at"] = time.time() + float(tok.get("expires_in", 3600)) - 60
    store.write("token.json", tok)


def connected() -> bool:
    t = store.read("token.json")
    return bool(t and t.get("refresh_token"))


def access_token(force_refresh: bool = False) -> str:
    tok = store.read("token.json")
    if not tok:
        raise AuthError("Not connected.")
    if force_refresh or time.time() >= tok.get("expires_at", 0):
        c = client()
        try:
            new = _post(TOKEN_URL, {"client_id": c["client_id"], "client_secret": c["client_secret"],
                                    "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
        except AuthError as e:
            if "invalid_grant" in str(e) or "expired" in str(e).lower() or "revoked" in str(e).lower():
                store.delete("token.json")
                raise AuthError("Google expired the connection (Testing-mode apps last 7 days). "
                                "Click Connect Google Health again.") from None
            raise
        tok["access_token"] = new["access_token"]
        tok["expires_at"] = time.time() + float(new.get("expires_in", 3600)) - 60
        store.write("token.json", tok)
    return tok["access_token"]


def disconnect() -> None:
    tok = store.read("token.json")
    if tok and tok.get("refresh_token"):
        try:
            _post(REVOKE_URL, {"token": tok["refresh_token"]})
        except AuthError:
            pass
    store.delete("token.json")
