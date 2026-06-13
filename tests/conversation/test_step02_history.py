"""Tests for Phase 06 Step 02 – DynamoDB conversation history save/load.

Covers:
  - _save_exchange: correct PutItem payload (session_id, user_id, msgs, TTL, turn_index)
  - _load_history: Query called with correct params, returns chronological list
  - _load_history: graceful empty result and missing env var
  - lambda_handler: _save_exchange called on 200 with correct args
  - lambda_handler: _save_exchange NOT called on 400/403/502
  - lambda_handler: client-supplied session_id is reused
  - lambda_handler: new session_id generated when absent from body
"""

import importlib.util
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
_HANDLER_PATH = REPO_ROOT / "lambda" / "sts" / "handler.py"

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/iam-gateway-dev-bedrock-scoped"
TABLE = "iam-gateway-dev-conversation-history"


def _import_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
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


def _event(
    message: str = "test query",
    session_id: str | None = None,
    user_id: str = "user-123",
    department: str = "engineering",
    clearance_level: str = "2",
    jti: str = "jti-001",
) -> dict:
    body: dict = {"message": message}
    if session_id is not None:
        body["session_id"] = session_id
    return {
        "body": json.dumps(body),
        "requestContext": {"authorizer": {
            "user_id": user_id,
            "department": department,
            "clearance_level": clearance_level,
            "jti": jti,
        }},
    }


def _env(monkeypatch):
    monkeypatch.setenv("BEDROCK_ROLE_ARN",     FAKE_ROLE_ARN)
    monkeypatch.setenv("BEDROCK_MODEL_ID",     "amazon.titan-text-express-v1")
    monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", "arn:aws:bedrock:eu-central-1::foundation-model/amazon.titan-text-express-v1")
    monkeypatch.setenv("KNOWLEDGE_BASE_ID",    "test-kb-id")
    monkeypatch.setenv("CONVERSATION_TABLE",   TABLE)
    monkeypatch.setenv("AWS_REGION",           "eu-central-1")


# ─── Unit: _save_exchange ─────────────────────────────────────────────────────


class TestSaveExchange:
    def _call(self, monkeypatch, session_id="sess-1", user_id="u-1",
              user_msg="hello", assistant_msg="hi"):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            mod._save_exchange(session_id, user_id, user_msg, assistant_msg)
        return mock_db.put_item.call_args[1]

    def test_put_item_called_once(self, monkeypatch):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            mod._save_exchange("s", "u", "msg", "resp")
        mock_db.put_item.assert_called_once()

    def test_correct_table_name(self, monkeypatch):
        kwargs = self._call(monkeypatch)
        assert kwargs["TableName"] == TABLE

    def test_session_id_stored(self, monkeypatch):
        kwargs = self._call(monkeypatch, session_id="sess-abc")
        assert kwargs["Item"]["session_id"]["S"] == "sess-abc"

    def test_user_id_stored(self, monkeypatch):
        kwargs = self._call(monkeypatch, user_id="user-xyz")
        assert kwargs["Item"]["user_id"]["S"] == "user-xyz"

    def test_user_msg_stored(self, monkeypatch):
        kwargs = self._call(monkeypatch, user_msg="what is policy?")
        assert kwargs["Item"]["user_msg"]["S"] == "what is policy?"

    def test_assistant_msg_stored(self, monkeypatch):
        kwargs = self._call(monkeypatch, assistant_msg="policy is X")
        assert kwargs["Item"]["assistant_msg"]["S"] == "policy is X"

    def test_turn_index_is_numeric_string(self, monkeypatch):
        kwargs = self._call(monkeypatch)
        turn_index_val = kwargs["Item"]["turn_index"]["N"]
        assert turn_index_val.isdigit()

    def test_ttl_set_and_in_future(self, monkeypatch):
        kwargs = self._call(monkeypatch)
        expires_at = int(kwargs["Item"]["expires_at"]["N"])
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        assert expires_at > now_epoch + 3600  # at least 1h in the future


# ─── Unit: _load_history ─────────────────────────────────────────────────────


class TestLoadHistory:
    def _mock_query_response(self, items: list[dict]) -> dict:
        """Build a minimal DynamoDB Query response from plain dicts."""
        return {
            "Items": [
                {
                    "session_id":    {"S": item["session_id"]},
                    "turn_index":    {"N": str(item["turn_index"])},
                    "user_msg":      {"S": item["user_msg"]},
                    "assistant_msg": {"S": item["assistant_msg"]},
                }
                for item in items
            ]
        }

    def test_returns_empty_list_when_no_items(self, monkeypatch):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        mock_db.query.return_value = {"Items": []}
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            result = mod._load_history("sess-1")
        assert result == []

    def test_returns_empty_when_table_env_missing(self, monkeypatch):
        monkeypatch.delenv("CONVERSATION_TABLE", raising=False)
        mod = _import_handler()
        result = mod._load_history("sess-1")
        assert result == []

    def test_query_uses_session_id(self, monkeypatch):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        mock_db.query.return_value = {"Items": []}
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            mod._load_history("my-session-id")
        kwargs = mock_db.query.call_args[1]
        assert kwargs["ExpressionAttributeValues"][":sid"]["S"] == "my-session-id"

    def test_query_is_descending(self, monkeypatch):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        mock_db.query.return_value = {"Items": []}
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            mod._load_history("s")
        assert mock_db.query.call_args[1]["ScanIndexForward"] is False

    def test_default_limit_is_5(self, monkeypatch):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        mock_db.query.return_value = {"Items": []}
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            mod._load_history("s")
        assert mock_db.query.call_args[1]["Limit"] == 5

    def test_results_returned_in_chronological_order(self, monkeypatch):
        """Query returns DESC; _load_history must reverse to chronological."""
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        # Simulate DynamoDB returning newest-first (DESC)
        raw = [
            {"session_id": "s", "turn_index": 3000, "user_msg": "third", "assistant_msg": "r3"},
            {"session_id": "s", "turn_index": 2000, "user_msg": "second", "assistant_msg": "r2"},
            {"session_id": "s", "turn_index": 1000, "user_msg": "first",  "assistant_msg": "r1"},
        ]
        mock_db.query.return_value = self._mock_query_response(raw)
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            result = mod._load_history("s")
        assert [r["user_msg"] for r in result] == ["first", "second", "third"]

    def test_result_contains_user_and_assistant_msg(self, monkeypatch):
        monkeypatch.setenv("CONVERSATION_TABLE", TABLE)
        monkeypatch.setenv("AWS_REGION", "eu-central-1")
        mod = _import_handler()
        mock_db = MagicMock()
        raw = [{"session_id": "s", "turn_index": 1, "user_msg": "hi", "assistant_msg": "hello"}]
        mock_db.query.return_value = self._mock_query_response(raw)
        with patch.object(mod, "_get_dynamodb", return_value=mock_db):
            result = mod._load_history("s")
        assert result[0] == {"user_msg": "hi", "assistant_msg": "hello"}


# ─── Integration: lambda_handler ─────────────────────────────────────────────


class TestHandlerSavesBehavior:
    def _run(self, monkeypatch, event, mock_db=None):
        _env(monkeypatch)
        sts_mock = _fake_sts()
        db_mock = mock_db or MagicMock()
        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="bedrock answer"), \
             patch.object(mod, "_get_dynamodb", return_value=db_mock):
            result = mod.lambda_handler(event, None)
        return result, db_mock

    def test_save_exchange_called_on_success(self, monkeypatch):
        result, db_mock = self._run(monkeypatch, _event())
        assert result["statusCode"] == 200
        db_mock.put_item.assert_called_once()

    def test_save_exchange_not_called_on_400(self, monkeypatch):
        _env(monkeypatch)
        event = {"body": json.dumps({}), "requestContext": {"authorizer": {
            "user_id": "u", "department": "eng", "clearance_level": "1", "jti": "j"
        }}}
        mod = _import_handler()
        db_mock = MagicMock()
        with patch.object(mod, "_get_dynamodb", return_value=db_mock):
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 400
        db_mock.put_item.assert_not_called()

    def test_save_exchange_not_called_on_403(self, monkeypatch):
        _env(monkeypatch)
        mod = _import_handler()
        db_mock = MagicMock()
        event = {"body": json.dumps({"message": "hello"})}
        with patch.object(mod, "_get_dynamodb", return_value=db_mock):
            result = mod.lambda_handler(event, None)
        assert result["statusCode"] == 403
        db_mock.put_item.assert_not_called()

    def test_save_exchange_not_called_on_502(self, monkeypatch):
        from botocore.exceptions import ClientError
        _env(monkeypatch)
        sts_mock = _fake_sts()
        db_mock = MagicMock()
        mod = _import_handler()
        bedrock_error = ClientError(
            {"Error": {"Code": "ThrottlingException", "Message": "throttled"}},
            "RetrieveAndGenerate",
        )
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", side_effect=bedrock_error), \
             patch.object(mod, "_get_dynamodb", return_value=db_mock):
            result = mod.lambda_handler(_event(), None)
        assert result["statusCode"] == 502
        db_mock.put_item.assert_not_called()

    def test_save_stores_user_msg_pre_sandwich(self, monkeypatch):
        """Stored user_msg must be the original (PII-stripped) message, not the sandwich-wrapped one."""
        result, db_mock = self._run(monkeypatch, _event(message="what is the HR policy?"))
        item = db_mock.put_item.call_args[1]["Item"]
        stored_msg = item["user_msg"]["S"]
        assert stored_msg == "what is the HR policy?"
        assert "You are a secure" not in stored_msg  # sandwich prefix must NOT be stored

    def test_client_session_id_reused(self, monkeypatch):
        client_sid = str(uuid.uuid4())
        result, db_mock = self._run(monkeypatch, _event(session_id=client_sid))
        body = json.loads(result["body"])
        assert body["session_id"] == client_sid
        stored_sid = db_mock.put_item.call_args[1]["Item"]["session_id"]["S"]
        assert stored_sid == client_sid

    def test_new_session_id_generated_when_absent(self, monkeypatch):
        result, _ = self._run(monkeypatch, _event())  # no session_id in body
        body = json.loads(result["body"])
        # Must be a valid UUID v4
        parsed = uuid.UUID(body["session_id"])
        assert parsed.version == 4

    def test_session_id_consistent_between_response_and_stored(self, monkeypatch):
        result, db_mock = self._run(monkeypatch, _event())
        response_sid = json.loads(result["body"])["session_id"]
        stored_sid = db_mock.put_item.call_args[1]["Item"]["session_id"]["S"]
        assert response_sid == stored_sid
