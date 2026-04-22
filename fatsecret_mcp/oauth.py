"""OAuth 1.0a signing for FatSecret.

FS quirks discovered the hard way (encoded here so callers don't have to):

- `request_token` must be POST (HTTP method is part of the signature base string;
  a GET with identical params produces a different, invalid signature).
- FS rejects OAuth params in the Authorization header ("Missing required
  parameter: oauth_consumer_key"). Params must live in the query string or
  POST body. We use POST body form-urlencoded.
- Consumer credentials for OAuth 1.0a are a SEPARATE pair from OAuth 2.0
  client credentials on the same app. The consumer_key is usually the same
  string but the secrets differ. Look for "REST API OAuth 1.0 Credentials"
  in the FS developer console.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse
from dataclasses import dataclass


@dataclass(frozen=True)
class Consumer:
    key: str
    secret: str


@dataclass(frozen=True)
class Token:
    key: str
    secret: str


def sign_and_encode(
    method: str,
    url: str,
    params: dict[str, str],
    consumer: Consumer,
    token: Token | None = None,
) -> bytes:
    """Return the `application/x-www-form-urlencoded` body for a signed request.

    The same params dict (minus oauth_signature) is built into the signature
    base string per RFC 5849 §3.4.1.
    """
    full = {
        **params,
        "oauth_consumer_key": consumer.key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_version": "1.0",
    }
    if token is not None:
        full["oauth_token"] = token.key

    normalized = "&".join(
        f"{_qs(k)}={_qs(str(v))}" for k, v in sorted(full.items())
    )
    base_string = f"{method}&{_qs(url)}&{_qs(normalized)}"
    signing_key = f"{_qs(consumer.secret)}&{_qs(token.secret) if token else ''}"
    sig = base64.b64encode(
        hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
    ).decode()

    full["oauth_signature"] = sig
    return urllib.parse.urlencode(full).encode()


def _qs(s: str) -> str:
    # RFC 3986 percent-encoding — `safe=''` so even / and : get escaped
    return urllib.parse.quote(s, safe="")
