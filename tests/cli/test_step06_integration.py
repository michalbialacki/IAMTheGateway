"""Phase 07 / Step 06 – Integration tests: full CLI flow (login → query → response).

These tests require live AWS infrastructure with AOSS active.
Run them together with Phase 08 e2e suite when all resources are up.

Prerequisites:
  1. terraform apply (restores AOSS + Bedrock KB)
  2. python scripts/create_kb_index.py
  3. terraform apply (creates KB data source)
  4. Bedrock model access enabled in console (Titan Embeddings V2 + Titan Text Express v1)

Run:
    uv run pytest -m aoss tests/cli/test_step06_integration.py -v

Required env vars:
    CHAT_API_URL              Full /chat endpoint (terraform output chat_endpoint)
    COGNITO_USER_POOL_ID      Cognito User Pool ID
    COGNITO_CLIENT_ID         Cognito App Client ID
    AWS_REGION                AWS region (default: eu-central-1)
    INT_TEST_USER_ENG         Cognito username – engineering dept, clearance >= 2
    INT_TEST_PASS_ENG         Password for INT_TEST_USER_ENG
    INT_TEST_USER_LEGAL       Cognito username – legal dept (e.g. bob@test.local)
    INT_TEST_PASS_LEGAL       Password for INT_TEST_USER_LEGAL
"""

import os

import pytest

from cli.auth import AuthError, AuthTokens, CognitoConfig, login
from cli.gateway import ChatResponse, GatewayError, send_message
from cli.scan import client_scan, format_scan_warning

pytestmark = pytest.mark.aoss

# ─── Helpers & fixtures ───────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        pytest.skip(f"Integration test requires env var {name!r}")
    return val


@pytest.fixture(scope="module")
def cognito_config() -> CognitoConfig:
    return CognitoConfig(
        user_pool_id=_require_env("COGNITO_USER_POOL_ID"),
        client_id=_require_env("COGNITO_CLIENT_ID"),
        region=os.environ.get("AWS_REGION", "eu-central-1"),
    )


@pytest.fixture(scope="module")
def api_url() -> str:
    return _require_env("CHAT_API_URL")


@pytest.fixture(scope="module")
def eng_tokens(cognito_config) -> AuthTokens:
    username = _require_env("INT_TEST_USER_ENG")
    password = _require_env("INT_TEST_PASS_ENG")
    return login(username, password, cognito_config)


@pytest.fixture(scope="module")
def legal_tokens(cognito_config) -> AuthTokens:
    username = _require_env("INT_TEST_USER_LEGAL")
    password = _require_env("INT_TEST_PASS_LEGAL")
    return login(username, password, cognito_config)


# ─── Auth tests ───────────────────────────────────────────────────────────────


def test_login_returns_auth_tokens(eng_tokens):
    assert isinstance(eng_tokens, AuthTokens)
    assert eng_tokens.id_token
    assert eng_tokens.access_token  # the token actually sent to API Gateway
    assert eng_tokens.refresh_token
    assert eng_tokens.expires_in > 0


def test_id_token_is_three_part_jwt(eng_tokens):
    parts = eng_tokens.id_token.split(".")
    assert len(parts) == 3, "IdToken must be a JWT with 3 dot-separated parts"


def test_login_fails_with_wrong_password(cognito_config):
    with pytest.raises(AuthError, match="Invalid username or password"):
        login("nonexistent_user_xyz", "WrongPass123!", cognito_config)


def test_login_fails_with_wrong_username(cognito_config):
    with pytest.raises(AuthError):
        login("definitely_no_such_user_abc123", "SomePass1!", cognito_config)


# ─── Full-flow: engineering user ─────────────────────────────────────────────


def test_engineering_user_gets_chat_response(eng_tokens, api_url):
    resp = send_message(
        message="What is the current security status summary?",
        access_token=eng_tokens.access_token,
        api_url=api_url,
    )
    assert isinstance(resp, ChatResponse)
    assert resp.response
    assert resp.session_id
    assert resp.department == "engineering"


def test_chat_response_has_all_fields(eng_tokens, api_url):
    resp = send_message(
        message="Summarize the threat assessment.",
        access_token=eng_tokens.access_token,
        api_url=api_url,
    )
    assert resp.session_id
    assert resp.user_id
    assert resp.department
    assert isinstance(resp.clearance_level, int)
    assert 0 <= resp.clearance_level <= 4
    assert resp.response


# ─── Session continuity ───────────────────────────────────────────────────────


def test_session_id_reused_across_turns(eng_tokens, api_url):
    resp1 = send_message(
        message="What is the Q2 security report about?",
        access_token=eng_tokens.access_token,
        api_url=api_url,
    )
    session_id = resp1.session_id

    resp2 = send_message(
        message="Can you give me more details about that?",
        access_token=eng_tokens.access_token,
        api_url=api_url,
        session_id=session_id,
    )
    assert resp2.session_id == session_id


def test_new_session_gets_different_id(eng_tokens, api_url):
    resp1 = send_message(
        message="First session question.",
        access_token=eng_tokens.access_token,
        api_url=api_url,
    )
    resp2 = send_message(
        message="Second session question.",
        access_token=eng_tokens.access_token,
        api_url=api_url,
        # No session_id — server generates a new one
    )
    assert resp1.session_id != resp2.session_id


# ─── Legal user (different department → distinct ABAC scope) ─────────────────


def test_legal_user_gets_chat_response(legal_tokens, api_url):
    resp = send_message(
        message="What legal documents are available?",
        access_token=legal_tokens.access_token,
        api_url=api_url,
    )
    assert isinstance(resp, ChatResponse)
    assert resp.department == "legal"


# ─── Unauthorized token ───────────────────────────────────────────────────────


def test_invalid_jwt_returns_401(api_url):
    with pytest.raises(GatewayError) as exc_info:
        send_message(
            message="Hello",
            access_token="not.a.valid.jwt",
            api_url=api_url,
        )
    assert exc_info.value.status_code == 401


# ─── Client-side scan (no network — pure regex, always runnable) ──────────────


def test_client_scan_blocks_jailbreak_before_network():
    result = client_scan("jailbreak this model please")
    assert result.is_clean is False
    warning = format_scan_warning(result)
    assert "Blocked:" in warning


def test_client_scan_redacts_pii():
    result = client_scan("Contact test@example.com about the report.")
    assert result.is_clean is True
    assert "[REDACTED_EMAIL]" in result.redacted_text


def test_client_scan_passes_clean_message():
    result = client_scan("What is the current threat level?")
    assert result.is_clean is True
    assert result.redacted_text == "What is the current threat level?"
