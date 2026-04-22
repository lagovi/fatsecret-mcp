"""Config loader. Resolution order:

1. Env vars: FATSECRET_CONSUMER_KEY / _SECRET / _USER_TOKEN / _USER_TOKEN_SECRET
2. Config file path in $FATSECRET_MCP_CONFIG
3. Default: ~/.config/fatsecret-mcp/config.json
4. Legacy fallback: ~/.fatsecret_creds + ~/.fatsecret_user_token (for users
   migrating from the pre-package monolith script).

Config file shape:
  {
    "consumer_key": "...",
    "consumer_secret": "...",
    "user_token": "...",           # optional — only after 3-legged flow
    "user_token_secret": "..."     # optional
  }
"""
from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass

from .oauth import Consumer, Token


def config_path() -> pathlib.Path:
    if env := os.environ.get("FATSECRET_MCP_CONFIG"):
        return pathlib.Path(env).expanduser()
    base = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", "~/.config")).expanduser()
    return base / "fatsecret-mcp" / "config.json"


@dataclass
class Config:
    consumer: Consumer
    user_token: Token | None

    @classmethod
    def load(cls) -> "Config":
        ck = os.environ.get("FATSECRET_CONSUMER_KEY")
        cs = os.environ.get("FATSECRET_CONSUMER_SECRET")
        tk = os.environ.get("FATSECRET_USER_TOKEN")
        ts = os.environ.get("FATSECRET_USER_TOKEN_SECRET")

        if not (ck and cs):
            data = _load_file()
            ck = ck or data.get("consumer_key")
            cs = cs or data.get("consumer_secret")
            tk = tk or data.get("user_token")
            ts = ts or data.get("user_token_secret")

        if not (ck and cs):
            raise RuntimeError(
                "FatSecret consumer credentials not found. "
                f"Set FATSECRET_CONSUMER_KEY/SECRET, or write to {config_path()}. "
                "See: fatsecret-mcp auth --help"
            )

        return cls(
            consumer=Consumer(key=ck.strip(), secret=cs.strip()),
            user_token=Token(key=tk.strip(), secret=ts.strip()) if tk and ts else None,
        )

    def save(self) -> pathlib.Path:
        """Persist this config to the config-file path, mode 0600."""
        p = config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "consumer_key": self.consumer.key,
            "consumer_secret": self.consumer.secret,
        }
        if self.user_token:
            data["user_token"] = self.user_token.key
            data["user_token_secret"] = self.user_token.secret
        p.write_text(json.dumps(data, indent=2))
        os.chmod(p, 0o600)
        return p


def _load_file() -> dict:
    p = config_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (OSError, json.JSONDecodeError):
            pass

    # Legacy fallback paths — backward compat with pre-package monolith.
    home = pathlib.Path.home()
    merged: dict = {}
    creds = home / ".fatsecret_creds"
    if creds.exists():
        try:
            d = json.loads(creds.read_text())
            # normalize key names across old variants
            merged["consumer_key"] = d.get("consumer_key") or d.get("client_id")
            merged["consumer_secret"] = d.get("consumer_secret") or d.get("client_secret")
        except (OSError, json.JSONDecodeError):
            pass
    tok = home / ".fatsecret_user_token"
    if tok.exists():
        try:
            d = json.loads(tok.read_text())
            merged["user_token"] = d.get("oauth_token") or d.get("user_token")
            merged["user_token_secret"] = d.get("oauth_token_secret") or d.get("user_token_secret")
        except (OSError, json.JSONDecodeError):
            pass
    return merged
