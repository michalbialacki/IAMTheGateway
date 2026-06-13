"""Tests for Phase 05 Step 04 – Bedrock Retrieve & Generate pipeline.

Verifies that _retrieve_and_generate:
  - Creates a bedrock-agent-runtime client with STS credentials
  - Passes KB ID, model ARN, and metadataFilter correctly
  - Applies ClearancePolicy generation parameters
  - Returns response["output"]["text"]
  - Wires build_metadata_filter into the lambda_handler call chain

All tests are local (no AWS). bedrock-agent-runtime is fully mocked.
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

FAKE_REGION    = "eu-central-1"
FAKE_MODEL_ID  = "amazon.titan-text-express-v1"
FAKE_MODEL_ARN = f"arn:aws:bedrock:{FAKE_REGION}::foundation-model/{FAKE_MODEL_ID}"
FAKE_KB_ID     = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"

_CREDS = {
    "AccessKeyId":     "AKIA_TEST",
    "SecretAccessKey": "SECRET_TEST",
    "SessionToken":    "TOKEN_TEST",
    "Expiration":      "2099-01-01T00:00:00+00:00",
}


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _load_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _set_env(monkeypatch):
    monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", FAKE_MODEL_ARN)
    monkeypatch.setenv("KNOWLEDGE_BASE_ID",    FAKE_KB_ID)
    monkeypatch.setenv("AWS_REGION",           FAKE_REGION)


def _rag_response(text: str = "Answer from KB") -> dict:
    return {"output": {"text": text}}


def _call_rag(monkeypatch, message: str = "test", policy=None, meta_filter=None):
    _set_env(monkeypatch)
    if policy is None:
        policy = get_policy(2)
    if meta_filter is None:
        meta_filter = {"andAll": []}
    bedrock_mock = MagicMock()
    bedrock_mock.retrieve_and_generate.return_value = _rag_response()
    with patch("boto3.client", return_value=bedrock_mock):
        _load_handler()._retrieve_and_generate(message, _CREDS, policy, meta_filter)
    return bedrock_mock


# ─── Client creation ──────────────────────────────────────────────────────────


class TestClientCreation:
    def test_uses_bedrock_agent_runtime_service(self, monkeypatch):
        _set_env(monkeypatch)
        bedrock_mock = MagicMock()
        bedrock_mock.retrieve_and_generate.return_value = _rag_response()
        with patch("boto3.client", return_value=bedrock_mock) as boto3_mock:
            _load_handler()._retrieve_and_generate("hi", _CREDS, get_policy(1), {})
        assert boto3_mock.call_args[0][0] == "bedrock-agent-runtime"

    def test_client_uses_sts_access_key_id(self, monkeypatch):
        _set_env(monkeypatch)
        bedrock_mock = MagicMock()
        bedrock_mock.retrieve_and_generate.return_value = _rag_response()
        with patch("boto3.client", return_value=bedrock_mock) as boto3_mock:
            _load_handler()._retrieve_and_generate("hi", _CREDS, get_policy(1), {})
        assert boto3_mock.call_args[1]["aws_access_key_id"] == "AKIA_TEST"

    def test_client_uses_sts_secret_key(self, monkeypatch):
        _set_env(monkeypatch)
        bedrock_mock = MagicMock()
        bedrock_mock.retrieve_and_generate.return_value = _rag_response()
        with patch("boto3.client", return_value=bedrock_mock) as boto3_mock:
            _load_handler()._retrieve_and_generate("hi", _CREDS, get_policy(1), {})
        assert boto3_mock.call_args[1]["aws_secret_access_key"] == "SECRET_TEST"

    def test_client_uses_sts_session_token(self, monkeypatch):
        _set_env(monkeypatch)
        bedrock_mock = MagicMock()
        bedrock_mock.retrieve_and_generate.return_value = _rag_response()
        with patch("boto3.client", return_value=bedrock_mock) as boto3_mock:
            _load_handler()._retrieve_and_generate("hi", _CREDS, get_policy(1), {})
        assert boto3_mock.call_args[1]["aws_session_token"] == "TOKEN_TEST"

    def test_client_uses_correct_region(self, monkeypatch):
        _set_env(monkeypatch)
        bedrock_mock = MagicMock()
        bedrock_mock.retrieve_and_generate.return_value = _rag_response()
        with patch("boto3.client", return_value=bedrock_mock) as boto3_mock:
            _load_handler()._retrieve_and_generate("hi", _CREDS, get_policy(1), {})
        assert boto3_mock.call_args[1]["region_name"] == FAKE_REGION


# ─── API call parameters ──────────────────────────────────────────────────────


class TestApiCallParameters:
    def _kb_conf(self, monkeypatch) -> dict:
        mock = _call_rag(monkeypatch)
        cfg  = mock.retrieve_and_generate.call_args[1]
        return cfg["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]

    def test_kb_id_from_env(self, monkeypatch):
        assert self._kb_conf(monkeypatch)["knowledgeBaseId"] == FAKE_KB_ID

    def test_model_arn_from_env(self, monkeypatch):
        assert self._kb_conf(monkeypatch)["modelArn"] == FAKE_MODEL_ARN

    def test_configuration_type_is_knowledge_base(self, monkeypatch):
        mock = _call_rag(monkeypatch)
        cfg  = mock.retrieve_and_generate.call_args[1]
        assert cfg["retrieveAndGenerateConfiguration"]["type"] == "KNOWLEDGE_BASE"

    def test_input_text_matches_message(self, monkeypatch):
        mock = _call_rag(monkeypatch, message="explain ABAC")
        cfg  = mock.retrieve_and_generate.call_args[1]
        assert cfg["input"]["text"] == "explain ABAC"

    def test_number_of_results_is_five(self, monkeypatch):
        kb_conf   = self._kb_conf(monkeypatch)
        retrieval = kb_conf["retrievalConfiguration"]["vectorSearchConfiguration"]
        assert retrieval["numberOfResults"] == 5

    def test_metadata_filter_passed_through(self, monkeypatch):
        test_filter = {
            "andAll": [
                {"equals":           {"key": "department",     "value": "alpha"}},
                {"lessThanOrEquals": {"key": "clearance_level","value": 2}},
            ]
        }
        mock      = _call_rag(monkeypatch, meta_filter=test_filter)
        kb_conf   = mock.retrieve_and_generate.call_args[1]["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
        retrieval = kb_conf["retrievalConfiguration"]["vectorSearchConfiguration"]
        assert retrieval["filter"] == test_filter


# ─── Generation parameters from ClearancePolicy ──────────────────────────────


@pytest.mark.parametrize("cl", [0, 1, 2, 3, 4])
def test_generation_params_match_policy(monkeypatch, cl: int):
    _set_env(monkeypatch)
    policy       = get_policy(cl)
    bedrock_mock = MagicMock()
    bedrock_mock.retrieve_and_generate.return_value = _rag_response()
    with patch("boto3.client", return_value=bedrock_mock):
        _load_handler()._retrieve_and_generate("q", _CREDS, policy, {})

    kb_conf = bedrock_mock.retrieve_and_generate.call_args[1][
        "retrieveAndGenerateConfiguration"
    ]["knowledgeBaseConfiguration"]
    text_cfg = kb_conf["generationConfiguration"]["inferenceConfig"]["textInferenceConfig"]

    assert text_cfg["temperature"] == policy.temperature, f"cl={cl}: temperature mismatch"
    assert text_cfg["topP"]        == policy.top_p,       f"cl={cl}: topP mismatch"
    assert text_cfg["maxTokens"]   == policy.max_tokens,  f"cl={cl}: maxTokens mismatch"


# ─── Return value ─────────────────────────────────────────────────────────────


def test_returns_output_text(monkeypatch):
    _set_env(monkeypatch)
    bedrock_mock = MagicMock()
    bedrock_mock.retrieve_and_generate.return_value = _rag_response("KB answer here")
    with patch("boto3.client", return_value=bedrock_mock):
        result = _load_handler()._retrieve_and_generate("q", _CREDS, get_policy(2), {})
    assert result == "KB answer here"


# ─── Integration with lambda_handler: filter wiring ──────────────────────────


class TestHandlerFilterWiring:
    """Verify that lambda_handler passes a correct metadataFilter to _retrieve_and_generate."""

    def _fresh_expiry(self):
        return datetime.now(timezone.utc) + timedelta(seconds=900)

    def _sts_mock(self):
        m = MagicMock()
        m.assume_role.return_value = {
            "Credentials": {
                "AccessKeyId":     "AKIA",
                "SecretAccessKey": "SECRET",
                "SessionToken":    "TOKEN",
                "Expiration":      self._fresh_expiry(),
            }
        }
        return m

    def _event(self, dept: str, cl: str) -> dict:
        return {
            "body": json.dumps({"message": "What is the company policy on leave?"}),
            "requestContext": {
                "authorizer": {
                    "user_id":         "user-1",
                    "department":      dept,
                    "clearance_level": cl,
                    "jti":             "jti-test",
                }
            },
        }

    def _call_handler(self, monkeypatch, dept: str, cl: str):
        monkeypatch.setenv("BEDROCK_ROLE_ARN",     "arn:aws:iam::123456789012:role/test")
        monkeypatch.setenv("BEDROCK_MODEL_ID",     FAKE_MODEL_ID)
        monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", FAKE_MODEL_ARN)
        monkeypatch.setenv("KNOWLEDGE_BASE_ID",    FAKE_KB_ID)
        monkeypatch.setenv("AWS_REGION",           FAKE_REGION)

        sts_mock     = self._sts_mock()
        bedrock_mock = MagicMock()
        bedrock_mock.retrieve_and_generate.return_value = _rag_response("ok")

        def boto3_factory(service_name, **kwargs):
            if service_name == "bedrock-agent-runtime":
                return bedrock_mock
            raise ValueError(service_name)

        mod = _load_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch("boto3.client", side_effect=boto3_factory):
            mod.lambda_handler(self._event(dept, cl), None)

        return bedrock_mock.retrieve_and_generate.call_args[1]

    def test_filter_department_matches_authorizer_context(self, monkeypatch):
        call_kwargs = self._call_handler(monkeypatch, "alpha", "2")
        kb_conf     = call_kwargs["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
        retrieval   = kb_conf["retrievalConfiguration"]["vectorSearchConfiguration"]
        dept_cond   = retrieval["filter"]["andAll"][0]
        assert dept_cond["equals"]["value"] == "alpha"

    def test_filter_clearance_level_matches_authorizer_context(self, monkeypatch):
        call_kwargs = self._call_handler(monkeypatch, "bravo", "3")
        kb_conf     = call_kwargs["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
        retrieval   = kb_conf["retrievalConfiguration"]["vectorSearchConfiguration"]
        cl_cond     = retrieval["filter"]["andAll"][1]
        assert cl_cond["lessThanOrEquals"]["value"] == 3

    def test_filter_clearance_level_is_int_not_str(self, monkeypatch):
        """Authorizer passes clearance_level as str; handler must cast to int for filter."""
        call_kwargs = self._call_handler(monkeypatch, "alpha", "1")
        kb_conf     = call_kwargs["retrieveAndGenerateConfiguration"]["knowledgeBaseConfiguration"]
        retrieval   = kb_conf["retrievalConfiguration"]["vectorSearchConfiguration"]
        cl_cond     = retrieval["filter"]["andAll"][1]
        assert isinstance(cl_cond["lessThanOrEquals"]["value"], int)
