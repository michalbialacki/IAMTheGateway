"""Tests for Phase 06 Step 03 – History injected into prompt context.

Covers:
  - build_sandwich_prompt: no history → no [CONVERSATION HISTORY] section
  - build_sandwich_prompt: with history → section present, turns in order
  - build_sandwich_prompt: history formatting (Turn N:, User:, Assistant:)
  - build_sandwich_prompt: backward compat (no history arg = same as before)
  - lambda_handler: _load_history called with the resolved session_id
  - lambda_handler: non-empty history reaches _retrieve_and_generate prompt
  - lambda_handler: empty history → no history section in prompt
  - lambda_handler: new session starts with empty history
"""

import importlib.util
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLER_PATH  = REPO_ROOT / "lambda" / "sts" / "handler.py"
_SANDWICH_PATH = REPO_ROOT / "lambda" / "sanitizer" / "sandwich.py"

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/iam-gateway-dev-bedrock-scoped"
TABLE = "iam-gateway-dev-conversation-history"


def _import_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_sandwich():
    spec = importlib.util.spec_from_file_location("sandwich", _SANDWICH_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_sts():
    mock = MagicMock()
    mock.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "ASIA",
            "SecretAccessKey": "SECRET",
            "SessionToken": "TOKEN",
            "Expiration": datetime(2099, 1, 1, tzinfo=timezone.utc),
        }
    }
    return mock


def _event(message="test query", session_id=None):
    body: dict = {"message": message}
    if session_id is not None:
        body["session_id"] = session_id
    return {
        "body": json.dumps(body),
        "requestContext": {"authorizer": {
            "user_id": "user-123",
            "department": "engineering",
            "clearance_level": "2",
            "jti": "jti-001",
        }},
    }


def _env(monkeypatch):
    monkeypatch.setenv("BEDROCK_ROLE_ARN",     FAKE_ROLE_ARN)
    monkeypatch.setenv("BEDROCK_MODEL_ID",     "amazon.titan-text-express-v1")
    monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", "arn:aws:bedrock:eu-central-1::foundation-model/amazon.titan-text-express-v1")
    monkeypatch.setenv("KNOWLEDGE_BASE_ID",    "test-kb-id")
    monkeypatch.setenv("CONVERSATION_TABLE",   TABLE)
    monkeypatch.setenv("AWS_REGION",           "eu-central-1")


SAMPLE_HISTORY = [
    {"user_msg": "first question", "assistant_msg": "first answer"},
    {"user_msg": "second question", "assistant_msg": "second answer"},
]


# ─── Unit: build_sandwich_prompt with history ─────────────────────────────────


class TestSandwichWithHistory:
    def test_no_history_section_when_history_is_none(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2)
        assert "[CONVERSATION HISTORY]" not in result

    def test_no_history_section_when_history_is_empty_list(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=[])
        assert "[CONVERSATION HISTORY]" not in result

    def test_history_section_present_when_non_empty(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=SAMPLE_HISTORY)
        assert "[CONVERSATION HISTORY]" in result

    def test_history_turns_numbered(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=SAMPLE_HISTORY)
        assert "Turn 1:" in result
        assert "Turn 2:" in result

    def test_history_contains_user_labels(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=SAMPLE_HISTORY)
        assert "User: first question" in result
        assert "User: second question" in result

    def test_history_contains_assistant_labels(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=SAMPLE_HISTORY)
        assert "Assistant: first answer" in result
        assert "Assistant: second answer" in result

    def test_history_appears_before_user_message(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("current question", "eng", 2, history=SAMPLE_HISTORY)
        history_pos = result.index("[CONVERSATION HISTORY]")
        user_pos = result.index("[USER] current question")
        assert history_pos < user_pos

    def test_history_appears_after_system_opening(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=SAMPLE_HISTORY)
        system_pos = result.index("[SYSTEM]")
        history_pos = result.index("[CONVERSATION HISTORY]")
        assert system_pos < history_pos

    def test_closing_reminder_still_present_with_history(self):
        sandwich = _import_sandwich()
        result = sandwich.build_sandwich_prompt("hi", "eng", 2, history=SAMPLE_HISTORY)
        assert "[REMINDER]" in result

    def test_backward_compat_no_history_arg(self):
        sandwich = _import_sandwich()
        without = sandwich.build_sandwich_prompt("hi", "eng", 2)
        with_none = sandwich.build_sandwich_prompt("hi", "eng", 2, history=None)
        assert without == with_none

    def test_single_turn_history(self):
        sandwich = _import_sandwich()
        history = [{"user_msg": "only q", "assistant_msg": "only a"}]
        result = sandwich.build_sandwich_prompt("new q", "eng", 2, history=history)
        assert "Turn 1:" in result
        assert "Turn 2:" not in result


# ─── Integration: lambda_handler history flow ─────────────────────────────────


class TestHandlerHistoryInjection:
    def _run(self, monkeypatch, event, history=None):
        _env(monkeypatch)
        sts_mock = _fake_sts()
        db_mock = MagicMock()
        captured_prompt = {}
        mod = _import_handler()

        def fake_rag(user_message, credentials, policy, metadata_filter):
            captured_prompt["text"] = user_message
            return "bedrock answer"

        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", side_effect=fake_rag), \
             patch.object(mod, "_get_dynamodb", return_value=db_mock), \
             patch.object(mod, "_load_history", return_value=history or []):
            result = mod.lambda_handler(event, None)
        return result, captured_prompt, mod

    def test_load_history_called_with_session_id(self, monkeypatch):
        _env(monkeypatch)
        sts_mock = _fake_sts()
        db_mock = MagicMock()
        sid = str(uuid.uuid4())
        mod = _import_handler()
        load_calls = []

        def capture_load(session_id, limit=5):
            load_calls.append(session_id)
            return []

        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="ok"), \
             patch.object(mod, "_get_dynamodb", return_value=db_mock), \
             patch.object(mod, "_load_history", side_effect=capture_load):
            mod.lambda_handler(_event(session_id=sid), None)

        assert load_calls == [sid]

    def test_history_present_in_prompt_when_non_empty(self, monkeypatch):
        result, prompt, _ = self._run(monkeypatch, _event(), history=SAMPLE_HISTORY)
        assert result["statusCode"] == 200
        assert "[CONVERSATION HISTORY]" in prompt["text"]
        assert "first question" in prompt["text"]

    def test_no_history_section_in_prompt_when_empty(self, monkeypatch):
        result, prompt, _ = self._run(monkeypatch, _event(), history=[])
        assert result["statusCode"] == 200
        assert "[CONVERSATION HISTORY]" not in prompt["text"]

    def test_history_section_before_user_message_in_prompt(self, monkeypatch):
        result, prompt, _ = self._run(monkeypatch, _event(message="current q"), history=SAMPLE_HISTORY)
        text = prompt["text"]
        assert text.index("[CONVERSATION HISTORY]") < text.index("[USER] current q")

    def test_new_session_starts_with_no_history(self, monkeypatch):
        """New session_id → _load_history returns [] → no history in prompt."""
        result, prompt, _ = self._run(monkeypatch, _event(), history=[])
        assert "[CONVERSATION HISTORY]" not in prompt["text"]

    def test_client_session_history_loaded_for_existing_session(self, monkeypatch):
        sid = str(uuid.uuid4())
        result, prompt, _ = self._run(
            monkeypatch,
            _event(session_id=sid),
            history=[{"user_msg": "past q", "assistant_msg": "past a"}],
        )
        assert "past q" in prompt["text"]
        assert "past a" in prompt["text"]
