"""Phase 07 / Step 02 – JWT storage (keyring) unit tests.

All tests mock keyring calls — no OS credential store is touched.
Covers: save, load, clear, needs_refresh, edge cases (corruption, missing entry).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from cli.auth import AuthTokens
from cli.storage import _SERVICE, clear_tokens, load_tokens, needs_refresh, save_tokens

# ─── Helpers ──────────────────────────────────────────────────────────────────

_TOKENS = AuthTokens(
    id_token="id.jwt",
    access_token="access.jwt",
    refresh_token="refresh.opaque",
    expires_in=3600,
)


def _make_stored_payload(expires_in_seconds: int = 3600) -> str:
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)
    ).isoformat()
    return json.dumps({
        "id_token":      "id.jwt",
        "access_token":  "access.jwt",
        "refresh_token": "refresh.opaque",
        "expires_at":    expires_at,
    })


# ─── save_tokens ──────────────────────────────────────────────────────────────


@patch("cli.storage.keyring.set_password")
def test_save_calls_set_password(mock_set):
    save_tokens("alice", _TOKENS)
    assert mock_set.called


@patch("cli.storage.keyring.set_password")
def test_save_uses_correct_service_and_username(mock_set):
    save_tokens("alice", _TOKENS)
    args = mock_set.call_args[0]
    assert args[0] == _SERVICE
    assert args[1] == "alice"


@patch("cli.storage.keyring.set_password")
def test_save_payload_contains_all_token_fields(mock_set):
    save_tokens("alice", _TOKENS)
    payload = json.loads(mock_set.call_args[0][2])
    assert payload["id_token"] == "id.jwt"
    assert payload["access_token"] == "access.jwt"
    assert payload["refresh_token"] == "refresh.opaque"
    assert "expires_at" in payload


@patch("cli.storage.keyring.set_password")
def test_save_stores_expires_at_as_iso_utc(mock_set):
    save_tokens("alice", _TOKENS)
    payload = json.loads(mock_set.call_args[0][2])
    dt = datetime.fromisoformat(payload["expires_at"])
    # Must be timezone-aware and approximately now + 3600 s
    assert dt.tzinfo is not None
    delta = (dt - datetime.now(timezone.utc)).total_seconds()
    assert 3595 < delta < 3605


# ─── load_tokens ──────────────────────────────────────────────────────────────


@patch("cli.storage.keyring.get_password", return_value=None)
def test_load_returns_none_when_no_entry(_):
    assert load_tokens("alice") is None


@patch("cli.storage.keyring.get_password")
def test_load_returns_auth_tokens_on_valid_payload(mock_get):
    mock_get.return_value = _make_stored_payload(3600)
    tokens = load_tokens("alice")
    assert isinstance(tokens, AuthTokens)
    assert tokens.id_token == "id.jwt"
    assert tokens.access_token == "access.jwt"
    assert tokens.refresh_token == "refresh.opaque"


@patch("cli.storage.keyring.get_password")
def test_load_expires_in_reflects_remaining_time(mock_get):
    mock_get.return_value = _make_stored_payload(3600)
    tokens = load_tokens("alice")
    # expires_in should be close to 3600 (within a few seconds of test execution)
    assert 3590 < tokens.expires_in <= 3600


@patch("cli.storage.keyring.get_password")
def test_load_expires_in_is_zero_for_expired_tokens(mock_get):
    mock_get.return_value = _make_stored_payload(-60)  # expired 60 s ago
    tokens = load_tokens("alice")
    assert tokens.expires_in == 0


@patch("cli.storage.keyring.get_password", return_value="not json {{{")
def test_load_returns_none_on_corrupt_json(_):
    assert load_tokens("alice") is None


@patch("cli.storage.keyring.get_password")
def test_load_returns_none_on_missing_field(mock_get):
    mock_get.return_value = json.dumps({"id_token": "x"})  # incomplete
    assert load_tokens("alice") is None


@patch("cli.storage.keyring.get_password")
def test_load_uses_correct_service_and_username(mock_get):
    mock_get.return_value = None
    load_tokens("bob")
    mock_get.assert_called_once_with(_SERVICE, "bob")


# ─── clear_tokens ─────────────────────────────────────────────────────────────


@patch("cli.storage.keyring.delete_password")
def test_clear_calls_delete_password(mock_del):
    clear_tokens("alice")
    mock_del.assert_called_once_with(_SERVICE, "alice")


@patch("cli.storage.keyring.delete_password")
def test_clear_ignores_password_delete_error(mock_del):
    import keyring.errors
    mock_del.side_effect = keyring.errors.PasswordDeleteError("not found")
    clear_tokens("alice")  # must not raise


# ─── needs_refresh ────────────────────────────────────────────────────────────


@patch("cli.storage.keyring.get_password", return_value=None)
def test_needs_refresh_true_when_no_tokens(_):
    assert needs_refresh("alice") is True


@patch("cli.storage.keyring.get_password")
def test_needs_refresh_true_when_expires_within_buffer(mock_get):
    mock_get.return_value = _make_stored_payload(200)  # expires in 200 s, buffer=300
    assert needs_refresh("alice", buffer_seconds=300) is True


@patch("cli.storage.keyring.get_password")
def test_needs_refresh_true_when_expires_exactly_at_buffer(mock_get):
    mock_get.return_value = _make_stored_payload(300)  # exactly at buffer
    assert needs_refresh("alice", buffer_seconds=300) is True


@patch("cli.storage.keyring.get_password")
def test_needs_refresh_false_when_plenty_of_time(mock_get):
    mock_get.return_value = _make_stored_payload(3600)
    assert needs_refresh("alice", buffer_seconds=300) is False


@patch("cli.storage.keyring.get_password")
def test_needs_refresh_custom_buffer(mock_get):
    mock_get.return_value = _make_stored_payload(600)
    assert needs_refresh("alice", buffer_seconds=60) is False
    assert needs_refresh("alice", buffer_seconds=700) is True
