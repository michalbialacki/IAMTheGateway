"""
Phase 02 Step 01 – Cognito test users.

Verifies: users exist, correct group membership, can authenticate + receive JWT.
"""

import base64
import json

import pytest

pytestmark = pytest.mark.aws

EXPECTED = {
    "alice@test.local": "dept_engineering_cl_2",
    "bob@test.local":   "dept_legal_cl_1",
    "eve@test.local":   "dept_security_cl_4",
}


def _decode_jwt_payload(token: str) -> dict:
    """Decode JWT payload without signature verification (inspection only)."""
    payload_b64 = token.split(".")[1]
    # add padding if needed
    payload_b64 += "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))


# ─── user existence + group membership ───────────────────────────────────────

@pytest.mark.parametrize("username,expected_group", EXPECTED.items())
def test_user_exists(idp, pool_id, username, expected_group):
    user = idp.admin_get_user(UserPoolId=pool_id, Username=username)
    # Cognito with email as username_attribute stores UUID as Username internally.
    # Verify by checking the email attribute instead.
    email = next(
        (a["Value"] for a in user["UserAttributes"] if a["Name"] == "email"),
        None,
    )
    assert email == username


@pytest.mark.parametrize("username,expected_group", EXPECTED.items())
def test_user_in_correct_group(idp, pool_id, username, expected_group):
    groups_resp = idp.admin_list_groups_for_user(
        UserPoolId=pool_id, Username=username
    )
    group_names = [g["GroupName"] for g in groups_resp["Groups"]]
    assert expected_group in group_names, (
        f"{username} not in group '{expected_group}'. Has: {group_names}"
    )


# ─── authentication + JWT ─────────────────────────────────────────────────────

def test_alice_can_authenticate(alice_tokens):
    assert alice_tokens.access_token
    assert alice_tokens.id_token


def test_bob_can_authenticate(bob_tokens):
    assert bob_tokens.access_token
    assert bob_tokens.id_token


def test_eve_can_authenticate(eve_tokens):
    assert eve_tokens.access_token
    assert eve_tokens.id_token


def test_alice_access_token_contains_groups(alice_tokens):
    payload = _decode_jwt_payload(alice_tokens.access_token)
    groups = payload.get("cognito:groups", [])
    assert "dept_engineering_cl_2" in groups


def test_bob_access_token_contains_groups(bob_tokens):
    payload = _decode_jwt_payload(bob_tokens.access_token)
    groups = payload.get("cognito:groups", [])
    assert "dept_legal_cl_1" in groups


def test_eve_access_token_contains_groups(eve_tokens):
    payload = _decode_jwt_payload(eve_tokens.access_token)
    groups = payload.get("cognito:groups", [])
    assert "dept_security_cl_4" in groups
