"""Unit tests for Phase 02 Step 02 – Lambda Authorizer.

All tests run without AWS credentials. JWKS and DynamoDB are fully mocked.
RSA key pair is generated once per module and reused across tests.
"""

import importlib.util
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HANDLER_PATH = Path(__file__).resolve().parents[2] / "lambda" / "authorizer" / "handler.py"

_TEST_KID = "test-kid-1"
_TEST_JWKS_URI = "https://cognito-idp.eu-central-1.amazonaws.com/test-pool/.well-known/jwks.json"
_TEST_TABLE = "test-revoked-tokens"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def rsa_private_key():
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )


@pytest.fixture(scope="module")
def jwks_response(rsa_private_key):
    from jwt.algorithms import RSAAlgorithm

    public_key = rsa_private_key.public_key()
    jwk = json.loads(RSAAlgorithm.to_jwk(public_key))
    jwk["kid"] = _TEST_KID
    jwk["use"] = "sig"
    return {"keys": [jwk]}


@pytest.fixture(scope="module")
def make_token(rsa_private_key):
    """Token factory. Signs with test RSA key."""
    import jwt as pyjwt

    def _make(
        sub: str = "user-abc-123",
        groups: list[str] | None = None,
        token_use: str = "access",
        jti: str = "jti-test-001",
        exp_offset: int = 3600,
    ) -> str:
        if groups is None:
            groups = ["dept_engineering_cl_2"]
        return pyjwt.encode(
            {
                "sub": sub,
                "cognito:groups": groups,
                "token_use": token_use,
                "jti": jti,
                "exp": int(time.time()) + exp_offset,
                "iat": int(time.time()),
            },
            rsa_private_key,
            algorithm="RS256",
            headers={"kid": _TEST_KID},
        )

    return _make




@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("JWKS_URI", _TEST_JWKS_URI)
    monkeypatch.setenv("REVOKED_TOKENS_TABLE", _TEST_TABLE)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _urlopen_mock(jwks_response: dict):
    """Patch urllib.request.urlopen to return fake JWKS."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(jwks_response).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return patch("urllib.request.urlopen", return_value=resp)


def _dynamodb_mock(revoked: bool = False):
    """Patch boto3.resource('dynamodb') to simulate revocation state."""
    item = {"Item": {"jti": "revoked"}} if revoked else {}
    table = MagicMock()
    table.get_item.return_value = item
    resource = MagicMock()
    resource.Table.return_value = table
    return patch("boto3.resource", return_value=resource)


def _import_handler():
    """Load a fresh copy of the authorizer handler to reset module-level caches."""
    spec = importlib.util.spec_from_file_location("authorizer_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _event(token: str, arn: str = "arn:aws:execute-api:eu-central-1:123456789012:x/prod/POST/chat") -> dict:
    return {"authorizationToken": f"Bearer {token}", "methodArn": arn}


# ─── Tests: valid token ───────────────────────────────────────────────────────


class TestValidToken:
    def test_allow_policy_returned(self, mock_env, make_token, jwks_response):
        """Valid JWT → Effect Allow."""
        token = make_token()
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=False):
            result = _import_handler().lambda_handler(_event(token), None)
        assert result["policyDocument"]["Statement"][0]["Effect"] == "Allow"

    def test_context_fields_correct(self, mock_env, make_token, jwks_response):
        """ABAC context must contain correct user_id, department, clearance_level, jti."""
        token = make_token(sub="user-xyz", groups=["dept_legal_cl_1"], jti="jti-legal-001")
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=False):
            result = _import_handler().lambda_handler(_event(token), None)
        ctx = result["context"]
        assert ctx["user_id"] == "user-xyz"
        assert ctx["department"] == "legal"
        assert ctx["clearance_level"] == "1"
        assert ctx["jti"] == "jti-legal-001"


# ─── Tests: revoked token ─────────────────────────────────────────────────────


class TestRevokedToken:
    def test_deny_policy_returned(self, mock_env, make_token, jwks_response):
        """Revoked JWT (jti in DynamoDB) → Effect Deny, principalId='revoked'."""
        token = make_token(jti="jti-revoked-001")
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=True):
            result = _import_handler().lambda_handler(_event(token), None)
        assert result["policyDocument"]["Statement"][0]["Effect"] == "Deny"
        assert result["principalId"] == "revoked"


# ─── Tests: invalid tokens ────────────────────────────────────────────────────


class TestInvalidToken:
    def test_missing_bearer_prefix_raises(self, mock_env):
        """No 'Bearer ' prefix → Exception('Unauthorized')."""
        event = {"authorizationToken": "eyJhbGci...", "methodArn": "*"}
        with pytest.raises(Exception, match="Unauthorized"):
            _import_handler().lambda_handler(event, None)

    def test_empty_auth_header_raises(self, mock_env):
        """Empty authorizationToken → Exception('Unauthorized')."""
        event = {"authorizationToken": "", "methodArn": "*"}
        with pytest.raises(Exception, match="Unauthorized"):
            _import_handler().lambda_handler(event, None)

    def test_expired_token_raises(self, mock_env, make_token, jwks_response):
        """Expired JWT (exp in the past) → Exception('Unauthorized')."""
        token = make_token(exp_offset=-3600)
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=False):
            with pytest.raises(Exception, match="Unauthorized"):
                _import_handler().lambda_handler(_event(token), None)

    def test_wrong_token_use_raises(self, mock_env, make_token, jwks_response):
        """token_use='id' (not 'access') → Exception('Unauthorized')."""
        token = make_token(token_use="id")
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=False):
            with pytest.raises(Exception, match="Unauthorized"):
                _import_handler().lambda_handler(_event(token), None)


# ─── Tests: group parsing ─────────────────────────────────────────────────────


class TestGroupParsing:
    def test_first_matching_group_used(self, mock_env, make_token, jwks_response):
        """Non-dept groups are ignored; first dept_ match is used."""
        token = make_token(groups=["admin", "other-group", "dept_security_cl_4"])
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=False):
            result = _import_handler().lambda_handler(_event(token), None)
        assert result["context"]["department"] == "security"
        assert result["context"]["clearance_level"] == "4"

    def test_no_valid_group_raises(self, mock_env, make_token, jwks_response):
        """No dept_X_cl_N group → Exception('Unauthorized')."""
        token = make_token(groups=["admin", "superuser"])
        with _urlopen_mock(jwks_response), _dynamodb_mock(revoked=False):
            with pytest.raises(Exception, match="Unauthorized"):
                _import_handler().lambda_handler(_event(token), None)
