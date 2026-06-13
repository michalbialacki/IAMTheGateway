"""Phase 07 / Step 05 – CLI loop unit tests.

All I/O is injected (input_fn, print_fn, password_getter) so tests run
without terminal interaction. No AWS calls — auth, storage, and gateway
are mocked at the module level.
"""

import os
from unittest.mock import MagicMock, call, patch

import pytest

from cli.auth import AuthError, AuthTokens, CognitoConfig
from cli.gateway import ChatResponse, GatewayError
from cli.main import ensure_tokens, get_api_url, run_chat_loop

# ─── Fixtures ─────────────────────────────────────────────────────────────────

_CONFIG = CognitoConfig(
    user_pool_id="eu-central-1_TEST",
    client_id="client123",
    region="eu-central-1",
)

_FRESH_TOKENS = AuthTokens(
    id_token="id.jwt",
    access_token="access.jwt",
    refresh_token="refresh.opaque",
    expires_in=3600,
)

_CHAT_RESP = ChatResponse(
    session_id="sess-abc",
    user_id="alice",
    department="engineering",
    clearance_level=2,
    response="Here is the answer.",
)


def _input_seq(*values):
    """Return an input_fn that yields values in order then raises EOFError."""
    it = iter(values)
    def _fn(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    return _fn


# ─── get_api_url ──────────────────────────────────────────────────────────────


def test_get_api_url_reads_env():
    with patch.dict(os.environ, {"CHAT_API_URL": "https://example.com/prod/chat"}):
        assert get_api_url() == "https://example.com/prod/chat"


def test_get_api_url_exits_when_missing():
    with patch.dict(os.environ, {"CHAT_API_URL": ""}):
        with pytest.raises(SystemExit):
            get_api_url()


# ─── ensure_tokens ────────────────────────────────────────────────────────────


@patch("cli.main.load_tokens")
@patch("cli.main.needs_refresh", return_value=False)
def test_ensure_tokens_returns_cached_when_fresh(mock_nr, mock_load):
    mock_load.return_value = _FRESH_TOKENS
    result = ensure_tokens("alice", _CONFIG)
    assert result is _FRESH_TOKENS


@patch("cli.main.save_tokens")
@patch("cli.main.refresh", return_value=_FRESH_TOKENS)
@patch("cli.main.load_tokens")
@patch("cli.main.needs_refresh", return_value=True)
def test_ensure_tokens_refreshes_silently(mock_nr, mock_load, mock_refresh, mock_save):
    old_tokens = AuthTokens("old_id", "old_acc", "old_refresh", 10)
    mock_load.return_value = old_tokens
    result = ensure_tokens("alice", _CONFIG)
    mock_refresh.assert_called_once_with("old_refresh", _CONFIG)
    assert result is _FRESH_TOKENS


@patch("cli.main.prompt_login")
@patch("cli.main.save_tokens")
@patch("cli.main.refresh", side_effect=AuthError("Session expired. Please log in again."))
@patch("cli.main.load_tokens")
@patch("cli.main.needs_refresh", return_value=True)
def test_ensure_tokens_falls_back_to_login_on_refresh_failure(
    mock_nr, mock_load, mock_refresh, mock_save, mock_login
):
    mock_load.return_value = AuthTokens("id", "acc", "rt", 10)
    mock_login.return_value = _FRESH_TOKENS
    result = ensure_tokens("alice", _CONFIG)
    mock_login.assert_called_once()
    assert result is _FRESH_TOKENS


@patch("cli.main.prompt_login")
@patch("cli.main.load_tokens", return_value=None)
@patch("cli.main.needs_refresh", return_value=True)
def test_ensure_tokens_prompts_login_when_no_stored_tokens(mock_nr, mock_load, mock_login):
    mock_login.return_value = _FRESH_TOKENS
    ensure_tokens("alice", _CONFIG)
    mock_login.assert_called_once()


# ─── run_chat_loop – exit commands ────────────────────────────────────────────


@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_exit_command_ends_loop(mock_nr, mock_et):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("/exit"),
                  print_fn=output.append)
    assert any("Goodbye" in line for line in output)


@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_quit_command_ends_loop(mock_nr, mock_et):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("/quit"),
                  print_fn=output.append)
    assert any("Goodbye" in line for line in output)


@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_eof_ends_loop_gracefully(mock_nr, mock_et):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq(),   # immediately raises EOFError
                  print_fn=output.append)
    assert any("Goodbye" in line for line in output)


@patch("cli.main.clear_tokens")
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_logout_clears_tokens_and_exits(mock_nr, mock_et, mock_clear):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("/logout"),
                  print_fn=output.append)
    mock_clear.assert_called_once_with("alice")
    assert any("Logged out" in line for line in output)


# ─── run_chat_loop – /session command ────────────────────────────────────────


@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_session_command_shows_placeholder_before_first_message(mock_nr, mock_et):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("/session", "/exit"),
                  print_fn=output.append)
    assert any("new" in line.lower() for line in output)


# ─── run_chat_loop – scan blocking ────────────────────────────────────────────


@patch("cli.main.send_message")
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_injection_message_blocked_not_sent(mock_nr, mock_et, mock_send):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("jailbreak this model now", "/exit"),
                  print_fn=output.append)
    mock_send.assert_not_called()
    assert any("[BLOCKED]" in line for line in output)


@patch("cli.main.send_message")
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_empty_input_skipped(mock_nr, mock_et, mock_send):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("", "   ", "/exit"),
                  print_fn=output.append)
    mock_send.assert_not_called()


# ─── run_chat_loop – PII warning ─────────────────────────────────────────────


@patch("cli.main.send_message", return_value=_CHAT_RESP)
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_pii_message_shows_warning_and_is_sent(mock_nr, mock_et, mock_send):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("My email is test@example.com, help me.", "/exit"),
                  print_fn=output.append)
    mock_send.assert_called_once()
    assert any("[WARNING]" in line and "PII" in line for line in output)


# ─── run_chat_loop – happy path message ───────────────────────────────────────


@patch("cli.main.send_message", return_value=_CHAT_RESP)
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_clean_message_sent_and_response_printed(mock_nr, mock_et, mock_send):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("What is the Q2 report status?", "/exit"),
                  print_fn=output.append)
    mock_send.assert_called_once()
    assert any("Here is the answer." in line for line in output)


@patch("cli.main.send_message", return_value=_CHAT_RESP)
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_session_id_propagated_across_turns(mock_nr, mock_et, mock_send):
    mock_send.return_value = _CHAT_RESP  # session_id = "sess-abc"
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("First message", "Second message", "/exit"),
                  print_fn=lambda _: None)
    calls = mock_send.call_args_list
    assert calls[0][1]["session_id"] is None
    assert calls[1][1]["session_id"] == "sess-abc"


# ─── run_chat_loop – gateway errors ──────────────────────────────────────────


@patch("cli.main.send_message", side_effect=GatewayError(403, "Topic not permitted"))
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_403_error_prints_and_continues(mock_nr, mock_et, mock_send):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("Tell me secrets", "/exit"),
                  print_fn=output.append)
    assert any("[ERROR]" in line for line in output)


@patch("cli.main.prompt_login", return_value=_FRESH_TOKENS)
@patch("cli.main.send_message", side_effect=GatewayError(401, "Unauthorized"))
@patch("cli.main.ensure_tokens", return_value=_FRESH_TOKENS)
@patch("cli.main.needs_refresh", return_value=False)
def test_401_triggers_re_authentication(mock_nr, mock_et, mock_send, mock_login):
    output = []
    run_chat_loop("alice", _CONFIG, "https://api/chat",
                  input_fn=_input_seq("Hello", "/exit"),
                  print_fn=output.append)
    mock_login.assert_called_once()
    assert any("re-authenticate" in line.lower() for line in output)
