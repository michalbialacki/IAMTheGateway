"""Phase 08 / Step 01 – E2E full-flow matrix: role × clearance.

Drives the complete deployed path for every dev test user:
    Cognito login → POST /chat (Bearer access_token) → authorizer → STS →
    Bedrock KB (ABAC metadataFilter) → DynamoDB history → response.

Matrix (scripts/create_test_users.py):
    alice → engineering / cl=2 (restricted)
    bob   → legal       / cl=1 (classified)
    eve   → security    / cl=4 (top_secret)

All tests reach Bedrock → @pytest.mark.aoss (requires AOSS ACTIVE + ingest_docs.py run).
"""

import pytest

from cli.gateway import ChatResponse, send_message

pytestmark = pytest.mark.aoss

# A clearance-agnostic prompt: topic gating only restricts cl=0, and no test user
# is cl=0, so this passes the topic gate for every user in the matrix.
_GENERIC_QUERY = "Give me a brief operational status summary for my department."


# ─── Login works for every user in the matrix ────────────────────────────────


def test_login_returns_usable_access_token(user_tokens):
    assert user_tokens.access_token
    assert len(user_tokens.access_token.split(".")) == 3, "access token must be a JWT"
    assert user_tokens.expires_in > 0


# ─── Chat returns server-derived ABAC context matching the user's group ───────


def test_chat_returns_response(user_tokens, chat_url):
    resp = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    assert isinstance(resp, ChatResponse)
    assert resp.response, "model returned an empty response"


def test_chat_department_matches_user(user_tokens, e2e_user, chat_url):
    resp = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    assert resp.department == e2e_user.department


def test_chat_clearance_matches_user(user_tokens, e2e_user, chat_url):
    resp = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    assert resp.clearance_level == e2e_user.clearance


def test_chat_response_has_all_fields(user_tokens, chat_url):
    resp = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    assert resp.session_id
    assert resp.user_id
    assert resp.department
    assert 0 <= resp.clearance_level <= 4


# ─── Session continuity (per user) ────────────────────────────────────────────


def test_session_id_reused_across_turns(user_tokens, chat_url):
    first = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    follow = send_message(
        "Can you expand on that previous point?",
        user_tokens.access_token,
        chat_url,
        session_id=first.session_id,
    )
    assert follow.session_id == first.session_id


def test_new_request_without_session_gets_new_id(user_tokens, chat_url):
    first = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    second = send_message(_GENERIC_QUERY, user_tokens.access_token, chat_url)
    assert first.session_id != second.session_id
