"""Thin HTTP client for the FatSecret REST API.

All calls signed OAuth 1.0a. Error envelopes (`{"error": {...}}`) are surfaced
as exceptions — the API returns HTTP 200 on API-level failures, and swallowing
those quietly is how "log_food claimed success but nothing was written" bugs
happen.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from .oauth import Consumer, Token, sign_and_encode

API_URL = "https://platform.fatsecret.com/rest/server.api"
OAUTH_BASE = "https://authentication.fatsecret.com/oauth"


class FatSecretError(RuntimeError):
    """API-level error returned by FatSecret (non-transport)."""

    def __init__(self, code: int, message: str, method: str | None = None):
        self.code = code
        self.message = message
        self.method = method
        super().__init__(f"FatSecret error {code}: {message}" + (f" (method={method})" if method else ""))


@dataclass
class Client:
    consumer: Consumer
    token: Token | None = None
    timeout: float = 15.0

    def call(self, method: str, params: dict | None = None) -> dict:
        """Call a REST API method. Returns the parsed JSON dict on success.

        Raises FatSecretError on API-level errors, RuntimeError on HTTP errors.
        """
        params = {**(params or {}), "method": method, "format": "json"}
        body = sign_and_encode("POST", API_URL, params, self.consumer, self.token)
        req = urllib.request.Request(
            API_URL,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode()[:500]}") from None

        if isinstance(res, dict) and "error" in res and isinstance(res["error"], dict):
            err = res["error"]
            raise FatSecretError(int(err.get("code", 0)), str(err.get("message", "")), method=method)
        return res

    def request_token(self, callback_uri: str = "oob") -> Token:
        """Step 1 of 3-legged OAuth. Returns a short-lived request token."""
        body = sign_and_encode(
            "POST",
            f"{OAUTH_BASE}/request_token",
            {"oauth_callback": callback_uri},
            self.consumer,
        )
        req = urllib.request.Request(
            f"{OAUTH_BASE}/request_token",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = dict(_parse_qs(r.read().decode()))
        return Token(key=resp["oauth_token"], secret=resp["oauth_token_secret"])

    @staticmethod
    def authorize_url(request_token: Token) -> str:
        """Step 2. User visits this URL to approve the app; FS returns a verifier PIN."""
        return f"{OAUTH_BASE}/authorize?oauth_token={request_token.key}"

    def access_token(self, request_token: Token, verifier: str) -> Token:
        """Step 3. Exchange (request_token, verifier) for a permanent user token."""
        signed_client = Client(consumer=self.consumer, token=request_token)
        body = sign_and_encode(
            "POST",
            f"{OAUTH_BASE}/access_token",
            {"oauth_verifier": verifier},
            self.consumer,
            token=request_token,
        )
        req = urllib.request.Request(
            f"{OAUTH_BASE}/access_token",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            resp = dict(_parse_qs(r.read().decode()))
        return Token(key=resp["oauth_token"], secret=resp["oauth_token_secret"])


def _parse_qs(s: str) -> list[tuple[str, str]]:
    import urllib.parse as _p
    return _p.parse_qsl(s)
