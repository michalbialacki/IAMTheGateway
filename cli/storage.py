"""Persistent JWT token storage backed by the OS credential store.

Uses the `keyring` library which maps to:
  - Windows: Windows Credential Manager
  - macOS: Keychain
  - Linux: SecretService / libsecret

One entry per username under service name "iam-gateway".
Tokens are stored as a JSON blob; `expires_at` (ISO UTC) replaces `expires_in`
so remaining lifetime can be recalculated after a restart.
"""

import json
from datetime import datetime, timedelta, timezone

import keyring
import keyring.errors

from cli.auth import AuthTokens

_SERVICE = "iam-gateway"


def save_tokens(username: str, tokens: AuthTokens) -> None:
    """Persist tokens to the OS credential store.

    Overwrites any existing entry for this username.
    `expires_at` is computed as now + expires_in so the deadline survives restarts.
    """
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=tokens.expires_in)
    ).isoformat()
    payload = json.dumps({
        "id_token":      tokens.id_token,
        "access_token":  tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at":    expires_at,
    })
    keyring.set_password(_SERVICE, username, payload)


def load_tokens(username: str) -> AuthTokens | None:
    """Load tokens from the OS credential store.

    Returns None if no entry exists or the stored data is malformed.
    `expires_in` in the returned AuthTokens reflects remaining seconds (min 0).
    """
    raw = keyring.get_password(_SERVICE, username)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        expires_at = datetime.fromisoformat(data["expires_at"])
        remaining = int((expires_at - datetime.now(timezone.utc)).total_seconds())
        return AuthTokens(
            id_token=data["id_token"],
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_in=max(0, remaining),
        )
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def clear_tokens(username: str) -> None:
    """Remove stored tokens for this username. Silent if no entry exists."""
    try:
        keyring.delete_password(_SERVICE, username)
    except keyring.errors.PasswordDeleteError:
        pass


def needs_refresh(username: str, buffer_seconds: int = 300) -> bool:
    """Return True if tokens are absent or expire within buffer_seconds.

    Default buffer of 300 s (5 min) gives the CLI time to exchange the
    refresh token before the id_token actually expires.
    """
    tokens = load_tokens(username)
    if tokens is None:
        return True
    return tokens.expires_in <= buffer_seconds
