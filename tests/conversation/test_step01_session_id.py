"""Tests for Phase 06 Step 01 – SessionId generation.

Verifies that:
  - Every successful response contains a session_id field
  - session_id is a valid UUID v4 string
  - Each request produces a unique session_id
  - Error responses (400, 403) do NOT include session_id
"""

import importlib.util
import json
import re
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLER_PATH = REPO_ROOT / "lambda" / "sts" / "handler.py"

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/iam-gateway-dev-bedrock-scoped"
UUID_V4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _import_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_sts_response() -> dict:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "AQoXnyc4lcK4w//example/token==",
            "Expiration": datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        }
    }


def _event(
    user_id: str = "user-abc-123",
    department: str = "engineering",
    clearance_level: str = "2",
    jti: str = "jti-test-001",
    message: str = "test query",
) -> dict:
    return {
        "body": json.dumps({"message": message}),
        "requestContext": {
            "authorizer": {
                "user_id": user_id,
                "department": department,
                "clearance_level": clearance_level,
                "jti": jti,
            }
        },
    }


def _run_handler(monkeypatch, event=None, **event_kwargs):
    monkeypatch.setenv("BEDROCK_ROLE_ARN",     FAKE_ROLE_ARN)
    monkeypatch.setenv("BEDROCK_MODEL_ID",     "amazon.titan-text-express-v1")
    monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", "arn:aws:bedrock:eu-central-1::foundation-model/amazon.titan-text-express-v1")
    monkeypatch.setenv("KNOWLEDGE_BASE_ID",    "test-kb-id-s06")
    monkeypatch.setenv("CONVERSATION_TABLE",   "iam-gateway-dev-conversation-history")
    monkeypatch.setenv("AWS_REGION",           "eu-central-1")
    sts_mock = MagicMock()
    sts_mock.assume_role.return_value = _fake_sts_response()
    mod = _import_handler()
    with patch.object(mod, "_get_sts", return_value=sts_mock), \
         patch.object(mod, "_retrieve_and_generate", return_value="answer"), \
         patch.object(mod, "_get_dynamodb", return_value=MagicMock()):
        result = mod.lambda_handler(event or _event(**event_kwargs), None)
    return result


class TestSessionIdPresence:
    def test_session_id_in_200_response(self, monkeypatch):
        result = _run_handler(monkeypatch)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert "session_id" in body

    def test_session_id_is_string(self, monkeypatch):
        body = json.loads(_run_handler(monkeypatch)["body"])
        assert isinstance(body["session_id"], str)

    def test_session_id_is_uuid_v4(self, monkeypatch):
        body = json.loads(_run_handler(monkeypatch)["body"])
        assert UUID_V4_RE.match(body["session_id"]), (
            f"Not a valid UUID v4: {body['session_id']}"
        )

    def test_session_id_parseable_by_uuid_lib(self, monkeypatch):
        body = json.loads(_run_handler(monkeypatch)["body"])
        parsed = uuid.UUID(body["session_id"])
        assert parsed.version == 4


class TestSessionIdUniqueness:
    def test_two_requests_produce_different_session_ids(self, monkeypatch):
        body1 = json.loads(_run_handler(monkeypatch)["body"])
        body2 = json.loads(_run_handler(monkeypatch)["body"])
        assert body1["session_id"] != body2["session_id"]

    def test_same_user_gets_different_session_ids(self, monkeypatch):
        ids = set()
        for _ in range(5):
            body = json.loads(_run_handler(monkeypatch, user_id="user-x", jti="jti-x")["body"])
            ids.add(body["session_id"])
        assert len(ids) == 5, "session_id must be unique per request, not per user"


class TestSessionIdAbsentOnErrors:
    def test_no_session_id_on_400_missing_message(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_ROLE_ARN", FAKE_ROLE_ARN)
        monkeypatch.setenv("KNOWLEDGE_BASE_ID", "test-kb-id-s06")
        event = {"body": json.dumps({}), "requestContext": {"authorizer": {
            "user_id": "u", "department": "eng", "clearance_level": "1", "jti": "j"
        }}}
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 400
        assert "session_id" not in json.loads(result["body"])

    def test_no_session_id_on_403_missing_context(self, monkeypatch):
        event = {"body": json.dumps({"message": "hello"})}
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 403
        assert "session_id" not in json.loads(result["body"])

    def test_no_session_id_on_403_bad_clearance(self, monkeypatch):
        result = _import_handler().lambda_handler(_event(clearance_level="99"), None)
        assert result["statusCode"] == 403
        assert "session_id" not in json.loads(result["body"])


class TestSessionIdCorrelation:
    def test_session_id_returned_alongside_user_id_and_jti_context(self, monkeypatch):
        """Response includes session_id + user_id — client can correlate them."""
        body = json.loads(_run_handler(monkeypatch, user_id="u-corr", jti="jti-corr")["body"])
        assert "session_id" in body
        assert body["user_id"] == "u-corr"
