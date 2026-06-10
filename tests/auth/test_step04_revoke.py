"""Unit tests for Phase 02 Step 04 – JWT Revocation endpoint.

All tests run without AWS credentials. DynamoDB is fully mocked.
Tests cover authorization (clearance check), input validation, and DynamoDB write.
"""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HANDLER_PATH = Path(__file__).resolve().parents[2] / "lambda" / "revoke" / "handler.py"

_TEST_TABLE = "test-revoked-tokens"


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("REVOKED_TOKENS_TABLE", _TEST_TABLE)


def _import_handler():
    """Load a fresh copy of the revoke handler to reset module-level state."""
    spec = importlib.util.spec_from_file_location("revoke_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _event(
    jti: str | None = "jti-to-revoke",
    expires_at: int | None = None,
    clearance: int = 4,
    user_id: str = "user-sec-001",
) -> dict:
    body: dict = {}
    if jti is not None:
        body["jti"] = jti
    if expires_at is not None:
        body["expires_at"] = expires_at
    return {
        "requestContext": {
            "authorizer": {
                "clearance_level": str(clearance),
                "user_id": user_id,
                "department": "security",
            }
        },
        "body": json.dumps(body),
    }


def _mock_dynamodb() -> tuple:
    """Returns (patch_ctx, mock_table) for asserting put_item calls."""
    table = MagicMock()
    table.put_item.return_value = {}
    resource = MagicMock()
    resource.Table.return_value = table
    return patch("boto3.resource", return_value=resource), table


# ─── Tests: happy path ───────────────────────────────────────────────────────


class TestValidRevoke:
    def test_returns_200_and_jti(self, mock_env):
        patcher, table = _mock_dynamodb()
        with patcher:
            result = _import_handler().lambda_handler(_event(), None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["revoked"] is True
        assert body["jti"] == "jti-to-revoke"

    def test_writes_jti_to_dynamodb(self, mock_env):
        patcher, table = _mock_dynamodb()
        with patcher:
            _import_handler().lambda_handler(_event(jti="jti-abc"), None)
        call_kwargs = table.put_item.call_args.kwargs
        assert call_kwargs["Item"]["jti"] == "jti-abc"

    def test_includes_expires_at_when_provided(self, mock_env):
        patcher, table = _mock_dynamodb()
        with patcher:
            _import_handler().lambda_handler(_event(expires_at=9999999999), None)
        item = table.put_item.call_args.kwargs["Item"]
        assert item["expires_at"] == 9999999999

    def test_omits_expires_at_when_not_provided(self, mock_env):
        patcher, table = _mock_dynamodb()
        with patcher:
            _import_handler().lambda_handler(_event(), None)
        item = table.put_item.call_args.kwargs["Item"]
        assert "expires_at" not in item


# ─── Tests: authorization ─────────────────────────────────────────────────────


class TestRevokeAuthz:
    def test_clearance_2_returns_403(self, mock_env):
        """Restricted (cl=2) is below the minimum required for revocation."""
        result = _import_handler().lambda_handler(_event(clearance=2), None)
        assert result["statusCode"] == 403

    def test_clearance_3_is_allowed(self, mock_env):
        """Secret (cl=3) meets the minimum clearance."""
        patcher, _ = _mock_dynamodb()
        with patcher:
            result = _import_handler().lambda_handler(_event(clearance=3), None)
        assert result["statusCode"] == 200

    def test_missing_clearance_returns_403(self, mock_env):
        event = {"requestContext": {"authorizer": {}}, "body": json.dumps({"jti": "x"})}
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 403


# ─── Tests: input validation ──────────────────────────────────────────────────


class TestRevokeValidation:
    def test_missing_jti_returns_400(self, mock_env):
        result = _import_handler().lambda_handler(_event(jti=None), None)
        assert result["statusCode"] == 400
        assert "jti" in json.loads(result["body"])["error"]

    def test_invalid_json_body_returns_400(self, mock_env):
        event = _event()
        event["body"] = "not-json"
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_expires_at_non_integer_returns_400(self, mock_env):
        event = _event()
        event["body"] = json.dumps({"jti": "x", "expires_at": "not-a-number"})
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 400
