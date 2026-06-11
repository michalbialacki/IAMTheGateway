"""Tests for Phase 03 Step 04 – Bedrock client with STS credentials.

All tests are local (no AWS). STS and Bedrock are fully mocked.
Key behaviours:
  - Body validation: missing/empty message → 400
  - Bedrock client created with STS credentials (not Lambda role)
  - Bedrock called with correct model ID and user message
  - Credentials NOT present in the HTTP response (never exposed to caller)
  - Bedrock ClientError → 502
"""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from sanitizer.policy import get_policy

_HANDLER_PATH = Path(__file__).resolve().parents[2] / "lambda" / "sts" / "handler.py"
_POLICY_CL2 = get_policy(2)  # Restricted – default policy for direct _invoke_bedrock tests

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/iam-gateway-dev-bedrock-scoped"
FAKE_MODEL_ID = "amazon.titan-text-express-v1"
FAKE_REGION = "eu-central-1"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _import_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=900)


def _fake_sts_response() -> dict:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "AQoXnyc4lcK4w//example/token==",
            "Expiration": _fresh_expiry(),
        }
    }


def _fake_bedrock_response(text: str = "Hello from Bedrock") -> MagicMock:
    body_stream = MagicMock()
    body_stream.read.return_value = json.dumps({
        "results": [{"outputText": text, "completionReason": "FINISH"}]
    }).encode()
    mock_response = MagicMock()
    mock_response.__getitem__ = lambda self, k: body_stream if k == "body" else None
    return mock_response


def _make_clients(bedrock_text: str = "Hello from Bedrock"):
    """Return (sts_mock, bedrock_mock) pair ready for patching."""
    sts_mock = MagicMock()
    sts_mock.assume_role.return_value = _fake_sts_response()

    bedrock_mock = MagicMock()
    bedrock_mock.invoke_model.return_value = _fake_bedrock_response(bedrock_text)

    return sts_mock, bedrock_mock


def _patch_clients(mod, sts_mock, bedrock_mock):
    """Patch _get_sts and boto3.client('bedrock-runtime') on the given module."""
    def boto3_factory(service_name, **kwargs):
        if service_name == "bedrock-runtime":
            return bedrock_mock
        raise ValueError(f"Unexpected boto3.client call: {service_name}")

    return (
        patch.object(mod, "_get_sts", return_value=sts_mock),
        patch("boto3.client", side_effect=boto3_factory),
    )


def _event(
    message: str | None = "What is IAM?",
    user_id: str = "user-abc",
    department: str = "engineering",
    clearance_level: str = "2",
    jti: str = "jti-001",
) -> dict:
    body = json.dumps({"message": message}) if message is not None else None
    return {
        "body": body,
        "requestContext": {
            "authorizer": {
                "user_id": user_id,
                "department": department,
                "clearance_level": clearance_level,
                "jti": jti,
            }
        },
    }


def _env(monkeypatch):
    monkeypatch.setenv("BEDROCK_ROLE_ARN", FAKE_ROLE_ARN)
    monkeypatch.setenv("BEDROCK_MODEL_ID", FAKE_MODEL_ID)
    monkeypatch.setenv("AWS_REGION", FAKE_REGION)


# ─── Unit: _invoke_bedrock ────────────────────────────────────────────────────


class TestInvokeBedrock:
    _CREDS = {
        "AccessKeyId": "AKIA_TEST",
        "SecretAccessKey": "SECRET_TEST",
        "SessionToken": "TOKEN_TEST",
        "Expiration": "2099-01-01T00:00:00+00:00",
    }

    def test_bedrock_client_created_with_sts_credentials(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", FAKE_MODEL_ID)
        monkeypatch.setenv("AWS_REGION", FAKE_REGION)

        bedrock_mock = MagicMock()
        bedrock_mock.invoke_model.return_value = _fake_bedrock_response()

        with patch("boto3.client", return_value=bedrock_mock) as boto3_mock:
            _import_handler()._invoke_bedrock("hello", self._CREDS, _POLICY_CL2)

        boto3_mock.assert_called_once_with(
            "bedrock-runtime",
            region_name=FAKE_REGION,
            aws_access_key_id="AKIA_TEST",
            aws_secret_access_key="SECRET_TEST",
            aws_session_token="TOKEN_TEST",
        )

    def test_invoke_model_called_with_correct_model_id(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", FAKE_MODEL_ID)
        monkeypatch.setenv("AWS_REGION", FAKE_REGION)

        bedrock_mock = MagicMock()
        bedrock_mock.invoke_model.return_value = _fake_bedrock_response()

        with patch("boto3.client", return_value=bedrock_mock):
            _import_handler()._invoke_bedrock("hello", self._CREDS, _POLICY_CL2)

        call_kwargs = bedrock_mock.invoke_model.call_args[1]
        assert call_kwargs["modelId"] == FAKE_MODEL_ID

    def test_invoke_model_called_with_user_message(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", FAKE_MODEL_ID)
        monkeypatch.setenv("AWS_REGION", FAKE_REGION)

        bedrock_mock = MagicMock()
        bedrock_mock.invoke_model.return_value = _fake_bedrock_response()

        with patch("boto3.client", return_value=bedrock_mock):
            _import_handler()._invoke_bedrock("explain ABAC", self._CREDS, _POLICY_CL2)

        body_sent = json.loads(bedrock_mock.invoke_model.call_args[1]["body"])
        assert body_sent["inputText"] == "explain ABAC"

    def test_returns_extracted_output_text(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_MODEL_ID", FAKE_MODEL_ID)
        monkeypatch.setenv("AWS_REGION", FAKE_REGION)

        bedrock_mock = MagicMock()
        bedrock_mock.invoke_model.return_value = _fake_bedrock_response("The answer is 42.")

        with patch("boto3.client", return_value=bedrock_mock):
            result = _import_handler()._invoke_bedrock("question", self._CREDS, _POLICY_CL2)

        assert result == "The answer is 42."


# ─── Unit: lambda_handler – body validation ───────────────────────────────────


class TestBodyValidation:
    def test_missing_body_returns_400(self, monkeypatch):
        _env(monkeypatch)
        event = _event()
        event["body"] = None
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_empty_message_returns_400(self, monkeypatch):
        _env(monkeypatch)
        event = _event(message="")
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_whitespace_message_returns_400(self, monkeypatch):
        _env(monkeypatch)
        event = _event(message="   ")
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_invalid_json_body_returns_400(self, monkeypatch):
        _env(monkeypatch)
        event = _event()
        event["body"] = "not-json"
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 400

    def test_missing_message_field_returns_400(self, monkeypatch):
        _env(monkeypatch)
        event = _event()
        event["body"] = json.dumps({"query": "hello"})
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 400


# ─── Unit: lambda_handler – success path ─────────────────────────────────────


class TestHandlerSuccess:
    def test_returns_200(self, monkeypatch):
        _env(monkeypatch)
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(_event(), None)
        assert result["statusCode"] == 200

    def test_response_contains_response_field(self, monkeypatch):
        _env(monkeypatch)
        sts_mock, bedrock_mock = _make_clients("Bedrock says hi")
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(_event(), None)
        body = json.loads(result["body"])
        assert body["response"] == "Bedrock says hi"

    def test_credentials_not_in_response(self, monkeypatch):
        """STS credentials must never be exposed to the caller."""
        _env(monkeypatch)
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(_event(), None)
        body = json.loads(result["body"])
        assert "credentials" not in body
        assert "AccessKeyId" not in json.dumps(body)
        assert "SecretAccessKey" not in json.dumps(body)
        assert "SessionToken" not in json.dumps(body)

    def test_response_contains_abac_metadata(self, monkeypatch):
        _env(monkeypatch)
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            result = mod.lambda_handler(
                _event(user_id="u-1", department="legal", clearance_level="1"), None
            )
        body = json.loads(result["body"])
        assert body["user_id"] == "u-1"
        assert body["department"] == "legal"
        assert body["clearance_level"] == 1

    def test_bedrock_called_with_trimmed_message(self, monkeypatch):
        # After sandwich wrapping, inputText is the full sandwich prompt;
        # the trimmed user message must appear inside it.
        _env(monkeypatch)
        sts_mock, bedrock_mock = _make_clients()
        mod = _import_handler()
        p1, p2 = _patch_clients(mod, sts_mock, bedrock_mock)
        with p1, p2:
            mod.lambda_handler(_event(message="  hello world  "), None)
        body_sent = json.loads(bedrock_mock.invoke_model.call_args[1]["body"])
        assert "hello world" in body_sent["inputText"]


# ─── Unit: lambda_handler – Bedrock error ────────────────────────────────────


class TestHandlerBedrockError:
    def test_bedrock_client_error_returns_502(self, monkeypatch):
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _fake_sts_response()

        bedrock_mock = MagicMock()
        bedrock_mock.invoke_model.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
            "InvokeModel",
        )

        mod = _import_handler()

        def boto3_factory(service_name, **kwargs):
            if service_name == "bedrock-runtime":
                return bedrock_mock
            raise ValueError(service_name)

        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch("boto3.client", side_effect=boto3_factory):
            result = mod.lambda_handler(_event(), None)

        assert result["statusCode"] == 502
        body = json.loads(result["body"])
        assert "AccessDeniedException" in body["error"]
