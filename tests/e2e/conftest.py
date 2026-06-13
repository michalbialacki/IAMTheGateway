"""Shared fixtures for Phase 08 end-to-end tests.

These tests drive the deployed stack (Cognito → API Gateway → Lambda → STS →
Bedrock KB) using the dev test users provisioned by scripts/create_test_users.py.

Tiering (see pyproject markers):
  - @pytest.mark.aws  : reaches API Gateway / authorizer / revoke, rejected BEFORE
                        Bedrock (401/403/400). Safe to run with AOSS OFF (~$0).
  - @pytest.mark.aoss : exercises the full chat path through Bedrock KB (200 + content).
                        Requires AOSS ACTIVE.

Bearer token note:
  The Lambda Authorizer requires token_use == "access" (lambda/authorizer/handler.py).
  Therefore e2e tests send tokens.access_token as the bearer — NOT the id_token.
  (Cognito access tokens carry cognito:groups, which the authorizer parses.)

Config via env (all required for live tiers; tests skip if missing):
  COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID, AWS_REGION (default eu-central-1)
  CHAT_API_URL    – full POST /chat endpoint (terraform output chat_endpoint)
  REVOKE_API_URL  – full POST /revoke endpoint (optional; derived from CHAT_API_URL)

User credentials default to scripts/create_test_users.py (dev-only accounts);
override per user with E2E_USER_<KEY> / E2E_PASS_<KEY> (KEY = alice|bob|eve).
"""

import os

import pytest

from cli.auth import AuthTokens, CognitoConfig, login
from tests.e2e._helpers import DEFAULT_REGION, E2E_USERS, E2EUser


def _require_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        pytest.skip(f"e2e test requires env var {name!r}")
    return val


@pytest.fixture(scope="session")
def cognito_config() -> CognitoConfig:
    return CognitoConfig(
        user_pool_id=_require_env("COGNITO_USER_POOL_ID"),
        client_id=_require_env("COGNITO_CLIENT_ID"),
        region=os.environ.get("AWS_REGION", DEFAULT_REGION),
    )


@pytest.fixture(scope="session")
def chat_url() -> str:
    return _require_env("CHAT_API_URL")


@pytest.fixture(scope="session")
def revoke_url() -> str:
    """Full /revoke endpoint. Uses REVOKE_API_URL or derives it from CHAT_API_URL."""
    explicit = os.environ.get("REVOKE_API_URL", "").strip()
    if explicit:
        return explicit
    chat = _require_env("CHAT_API_URL")
    if chat.endswith("/chat"):
        return chat[: -len("/chat")] + "/revoke"
    pytest.skip("Cannot derive REVOKE_API_URL from CHAT_API_URL; set REVOKE_API_URL")


def login_user(user: E2EUser, config: CognitoConfig) -> AuthTokens:
    """Live Cognito login for an E2EUser (raises AuthError on failure)."""
    return login(user.username, user.password, config)


@pytest.fixture(
    params=E2E_USERS,
    ids=[u.key for u in E2E_USERS],
    scope="module",
)
def e2e_user(request) -> E2EUser:
    """Parametrized fixture: one run per dev test user (the role × clearance matrix)."""
    return request.param


@pytest.fixture(scope="module")
def user_tokens(e2e_user: E2EUser, cognito_config: CognitoConfig) -> AuthTokens:
    return login_user(e2e_user, cognito_config)
