"""End-to-end JWT tests for Phase 02 Step 05.

Tests the full auth flow: Cognito JWT → API Gateway (TOKEN authorizer) → response.
Requires deployed infrastructure and valid AWS credentials.
All tests are skipped when infrastructure is unavailable.

Covered cases:
  - valid access token → 200
  - missing Authorization header → 401
  - tampered JWT (wrong key / unknown kid) → 401
  - Cognito ID token used as access token (wrong token_use) → 401
  - revoked token (jti in DynamoDB) → 403
  - cross-tenant forged JWT (attacker claims different dept/clearance) → 401
"""

import subprocess
import time
from pathlib import Path

import pytest
import requests
from get_jwt import Tokens, login

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"
REGION = "eu-central-1"
_TIMEOUT = 15


# ─── Infrastructure helpers ───────────────────────────────────────────────────


def _tf_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=TF_MAIN,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _aws_ok() -> bool:
    try:
        import boto3

        boto3.client("sts", region_name=REGION).get_caller_identity()
        return True
    except Exception:
        return False


def _api_deployed() -> bool:
    return bool(_tf_output("chat_endpoint"))


skip_no_infra = pytest.mark.skipif(
    not _aws_ok() or not _api_deployed(),
    reason="AWS credentials or deployed API Gateway not available",
)


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def chat_url() -> str:
    url = _tf_output("chat_endpoint")
    if not url:
        pytest.skip("chat_endpoint not available – run terraform apply first")
    return url


@pytest.fixture(scope="module")
def revoke_url() -> str:
    base = _tf_output("api_gateway_endpoint")
    if not base:
        pytest.skip("api_gateway_endpoint not available – run terraform apply first")
    return f"{base}/revoke"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _decode_jti(token: str) -> str:
    import jwt

    payload = jwt.decode(token, options={"verify_signature": False})
    return payload["jti"]


def _make_tampered_jwt() -> str:
    """JWT signed with an ephemeral RSA key – kid won't match Cognito JWKS."""
    import jwt
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    fake_key = rsa.generate_private_key(65537, 2048, default_backend())
    return jwt.encode(
        {
            "sub": "attacker-sub",
            "cognito:groups": ["dept_security_cl_4"],
            "token_use": "access",
            "jti": f"tampered-{int(time.time())}",
            "exp": int(time.time()) + 3600,
            "iat": int(time.time()),
        },
        fake_key,
        algorithm="RS256",
        headers={"kid": "fake-kid-not-in-cognito-jwks"},
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ─── Tests ────────────────────────────────────────────────────────────────────


@skip_no_infra
def test_valid_jwt_returns_200(chat_url, alice_tokens: Tokens):
    """Alice's valid access token must pass the authorizer.

    200 = auth + Bedrock both OK.
    502 = auth OK, Bedrock ResourceNotFoundException (model access not yet enabled in account).
    401/403 would mean auth failure – that must not happen here.
    """
    r = requests.post(
        chat_url,
        headers={**_auth(alice_tokens.access_token), "Content-Type": "application/json"},
        json={"message": "ping"},
        timeout=_TIMEOUT,
    )
    assert r.status_code in (200, 502), (
        f"Expected 200 (full success) or 502 (Bedrock not enabled), got {r.status_code}: {r.text}"
    )


@skip_no_infra
def test_missing_authorization_header_returns_401(chat_url):
    """Request without Authorization header → 401 Unauthorized."""
    r = requests.post(chat_url, timeout=_TIMEOUT)
    assert r.status_code == 401


@skip_no_infra
def test_tampered_jwt_returns_401(chat_url):
    """JWT signed with unknown key → authorizer can't verify signature → 401."""
    r = requests.post(chat_url, headers=_auth(_make_tampered_jwt()), timeout=_TIMEOUT)
    assert r.status_code == 401


@skip_no_infra
def test_id_token_instead_of_access_token_returns_401(chat_url, alice_tokens: Tokens):
    """Cognito ID token has token_use='id'; authorizer requires 'access' → 401."""
    r = requests.post(chat_url, headers=_auth(alice_tokens.id_token), timeout=_TIMEOUT)
    assert r.status_code == 401


@skip_no_infra
def test_revoked_jwt_returns_403(chat_url, revoke_url, eve_tokens: Tokens):
    """Full revocation flow: get fresh token → 200 → revoke via /revoke → 403."""
    # Get a fresh token (don't touch session-scoped alice_tokens)
    fresh: Tokens = login("alice@test.local")
    jti = _decode_jti(fresh.access_token)

    # Confirm the token is accepted by the authorizer before revocation.
    # 200 = auth + Bedrock OK; 502 = auth OK but Bedrock not enabled.
    r = requests.post(
        chat_url,
        headers={**_auth(fresh.access_token), "Content-Type": "application/json"},
        json={"message": "ping"},
        timeout=_TIMEOUT,
    )
    assert r.status_code in (200, 502), (
        f"Expected 200 or 502 before revoke (auth must pass), got {r.status_code}: {r.text}"
    )

    # Revoke using eve (clearance_level=4, meets the ≥3 requirement)
    r = requests.post(
        revoke_url,
        headers={**_auth(eve_tokens.access_token), "Content-Type": "application/json"},
        json={"jti": jti},
        timeout=_TIMEOUT,
    )
    assert r.status_code == 200, f"Revoke request failed: {r.status_code}: {r.text}"

    # Confirm revocation is enforced (authorizer TTL=0 → no caching)
    r = requests.post(chat_url, headers=_auth(fresh.access_token), timeout=_TIMEOUT)
    assert r.status_code == 403, f"Expected 403 after revoke, got {r.status_code}"


@skip_no_infra
def test_cross_tenant_forged_jwt_returns_401(chat_url):
    """Attacker forges JWT claiming top-secret clearance with unknown key → 401."""
    r = requests.post(chat_url, headers=_auth(_make_tampered_jwt()), timeout=_TIMEOUT)
    assert r.status_code == 401
