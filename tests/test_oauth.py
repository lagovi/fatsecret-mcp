"""Offline OAuth 1.0a signature tests.

We reproduce the RFC 5849 reference example (adapted) so any regression in the
signing logic trips immediately. Uses monkeypatched nonce/timestamp for
determinism.
"""
import base64
import hashlib
import hmac
import urllib.parse

from fatsecret_mcp import oauth
from fatsecret_mcp.oauth import Consumer, Token, sign_and_encode


def test_sign_round_trip(monkeypatch):
    # Freeze nonce + timestamp so the signature is reproducible.
    monkeypatch.setattr(oauth.secrets, "token_hex", lambda n: "fixed_nonce")
    monkeypatch.setattr(oauth.time, "time", lambda: 1700000000)

    body = sign_and_encode(
        method="POST",
        url="https://platform.fatsecret.com/rest/server.api",
        params={"method": "foods.search", "search_expression": "butter", "format": "json"},
        consumer=Consumer(key="ck", secret="cs"),
        token=Token(key="tk", secret="ts"),
    )
    parsed = dict(urllib.parse.parse_qsl(body.decode()))
    # Reproduce the signature independently and compare.
    expected = {k: v for k, v in parsed.items() if k != "oauth_signature"}
    normalized = "&".join(f"{urllib.parse.quote(k, safe='')}={urllib.parse.quote(v, safe='')}" for k, v in sorted(expected.items()))
    base_string = f"POST&{urllib.parse.quote('https://platform.fatsecret.com/rest/server.api', safe='')}&{urllib.parse.quote(normalized, safe='')}"
    signing_key = f"{urllib.parse.quote('cs', safe='')}&{urllib.parse.quote('ts', safe='')}"
    sig = base64.b64encode(hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()).decode()
    assert parsed["oauth_signature"] == sig
    assert parsed["oauth_consumer_key"] == "ck"
    assert parsed["oauth_token"] == "tk"
    assert parsed["oauth_nonce"] == "fixed_nonce"
    assert parsed["oauth_timestamp"] == "1700000000"


def test_sign_without_token(monkeypatch):
    """Request token step: no user token yet, signing key ends in `&` with empty token secret."""
    monkeypatch.setattr(oauth.secrets, "token_hex", lambda n: "nnn")
    monkeypatch.setattr(oauth.time, "time", lambda: 1)
    body = sign_and_encode(
        method="POST",
        url="https://authentication.fatsecret.com/oauth/request_token",
        params={"oauth_callback": "oob"},
        consumer=Consumer(key="ck", secret="cs"),
        token=None,
    )
    parsed = dict(urllib.parse.parse_qsl(body.decode()))
    assert parsed["oauth_callback"] == "oob"
    assert "oauth_token" not in parsed
    assert parsed["oauth_consumer_key"] == "ck"


def test_error_envelope_raises():
    """FatSecretError is what `_call` throws when FS returns {'error':{...}} with HTTP 200."""
    from fatsecret_mcp.client import FatSecretError
    err = FatSecretError(code=10, message="Unknown method: please refer to the documentation", method="water.get")
    assert err.code == 10
    assert "Unknown method" in str(err)
    assert "water.get" in str(err)
