"""Tests for Phase 03 Step 05 – scope isolation, cache safety, deny outside scope.

Unit tests (no AWS):
  - Session tags always sourced from JWT authorizer context (not request body)
  - clearance_level in tags == user's actual clearance (not elevated)
  - department in tags == user's actual department (not overrideable)
  - Session policy scopes to user's department only
  - Different clearance levels use separate cache slots

Post-apply tests (require AWS):
  - BEDROCK_MODEL_ID env var is set on the Lambda
  - Lambda invocation with no message → 400
  - Lambda invocation with missing authorizer context → 403
  - Trust policy denies assume_role without required session tags (IAM condition)
"""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"
_HANDLER_PATH = REPO_ROOT / "lambda" / "sts" / "handler.py"

REGION = "eu-central-1"
PROJECT = "iam-gateway"
ENV = "dev"
STS_LAMBDA_NAME = f"{PROJECT}-{ENV}-sts-session"
BEDROCK_ROLE = f"{PROJECT}-{ENV}-bedrock-scoped"
FAKE_ROLE_ARN = f"arn:aws:iam::123456789012:role/{BEDROCK_ROLE}"
FAKE_MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"


# ─── Marks ───────────────────────────────────────────────────────────────────


def _aws_available() -> bool:
    try:
        import boto3
        boto3.client("sts", region_name=REGION).get_caller_identity()
        return True
    except Exception:
        return False


skip_no_aws = pytest.mark.skipif(
    not _aws_available(),
    reason="AWS credentials not configured or stack not applied",
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _import_handler():
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Offline tier: stub out DynamoDB conversation persistence (Phase 06).
    # These tests exercise the STS / sanitize / KB paths, not history;
    # persistence is covered in tests/conversation/. Without this stub,
    # _save_exchange() raises KeyError('CONVERSATION_TABLE') when the full
    # lambda_handler is invoked offline.
    mod._save_exchange = lambda *args, **kwargs: None
    mod._load_history = lambda *args, **kwargs: []
    return mod


def _fresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=900)


def _sts_response(expiry: datetime | None = None) -> dict:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "AQoXnyc4lcK4w//example/token==",
            "Expiration": expiry or _fresh_expiry(),
        }
    }


def _event(
    message: str = "What is the company policy?",
    user_id: str = "user-abc",
    department: str = "engineering",
    clearance_level: str = "2",
    jti: str = "jti-001",
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


def _env(monkeypatch):
    monkeypatch.setenv("BEDROCK_ROLE_ARN",    FAKE_ROLE_ARN)
    monkeypatch.setenv("BEDROCK_MODEL_ID",    FAKE_MODEL_ID)
    monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", f"arn:aws:bedrock:{REGION}::foundation-model/{FAKE_MODEL_ID}")
    monkeypatch.setenv("KNOWLEDGE_BASE_ID",   "test-kb-id-scope-isolation")
    monkeypatch.setenv("AWS_REGION",          REGION)


def _run_handler(mod, event, sts_mock):
    with patch.object(mod, "_get_sts", return_value=sts_mock), \
         patch.object(mod, "_retrieve_and_generate", return_value="ok"):
        return mod.lambda_handler(event, None)


# ─── Unit: scope isolation – session tags ────────────────────────────────────


class TestScopeIsolationTags:
    def _assume_kwargs(self, monkeypatch, **event_kwargs) -> dict:
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()
        _run_handler(mod, _event(**event_kwargs), sts_mock)
        return sts_mock.assume_role.call_args[1]

    def test_clearance_tag_equals_user_clearance(self, monkeypatch):
        """cl=1 user gets clearance_level tag '1', never elevated."""
        kwargs = self._assume_kwargs(monkeypatch, clearance_level="1")
        tags = {t["Key"]: t["Value"] for t in kwargs["Tags"]}
        assert tags["clearance_level"] == "1"

    def test_clearance_tag_not_elevated_for_low_clearance(self, monkeypatch):
        """cl=1 user cannot receive cl=3 credentials."""
        kwargs = self._assume_kwargs(monkeypatch, clearance_level="1")
        tags = {t["Key"]: t["Value"] for t in kwargs["Tags"]}
        assert tags["clearance_level"] != "3"
        assert tags["clearance_level"] != "4"

    def test_department_tag_equals_user_department(self, monkeypatch):
        """engineering user gets department tag 'engineering'."""
        kwargs = self._assume_kwargs(monkeypatch, department="engineering")
        tags = {t["Key"]: t["Value"] for t in kwargs["Tags"]}
        assert tags["department"] == "engineering"

    def test_department_from_authorizer_not_body(self, monkeypatch):
        """Department comes from JWT authorizer, cannot be overridden in request body."""
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()

        # Body tries to claim "security" department – must be ignored
        event = _event(department="engineering")
        event["body"] = json.dumps({"message": "test", "department": "security"})
        _run_handler(mod, event, sts_mock)

        kwargs = sts_mock.assume_role.call_args[1]
        tags = {t["Key"]: t["Value"] for t in kwargs["Tags"]}
        assert tags["department"] == "engineering"

    def test_clearance_from_authorizer_not_body(self, monkeypatch):
        """Clearance comes from JWT authorizer, cannot be overridden in request body."""
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()

        event = _event(clearance_level="1")
        event["body"] = json.dumps({"message": "test", "clearance_level": "4"})
        _run_handler(mod, event, sts_mock)

        kwargs = sts_mock.assume_role.call_args[1]
        tags = {t["Key"]: t["Value"] for t in kwargs["Tags"]}
        assert tags["clearance_level"] == "1"

    def test_both_tags_always_present(self, monkeypatch):
        """Every assume_role call includes both required tags."""
        for dept, cl in [("engineering", "0"), ("legal", "2"), ("security", "4")]:
            kwargs = self._assume_kwargs(monkeypatch, department=dept, clearance_level=cl)
            tag_keys = [t["Key"] for t in kwargs["Tags"]]
            assert "department" in tag_keys
            assert "clearance_level" in tag_keys


# ─── Unit: scope isolation – session policy ──────────────────────────────────


class TestScopeIsolationPolicy:
    def _policy(self, monkeypatch, department: str) -> dict:
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()
        _run_handler(mod, _event(department=department), sts_mock)
        return json.loads(sts_mock.assume_role.call_args[1]["Policy"])

    def test_session_policy_locks_to_user_department(self, monkeypatch):
        """Session policy StringEquals condition == user's actual department."""
        policy = self._policy(monkeypatch, "legal")
        dept_cond = policy["Statement"][0]["Condition"]["StringEquals"]
        assert dept_cond["aws:PrincipalTag/department"] == "legal"

    def test_engineering_policy_different_from_security(self, monkeypatch):
        """Session policies for different departments are distinct."""
        p_eng = self._policy(monkeypatch, "engineering")
        p_sec = self._policy(monkeypatch, "security")
        assert p_eng != p_sec

    def test_session_policy_does_not_allow_other_department(self, monkeypatch):
        """engineering user's policy does not grant access to finance."""
        policy = self._policy(monkeypatch, "engineering")
        dept_cond = policy["Statement"][0]["Condition"]["StringEquals"]
        assert dept_cond["aws:PrincipalTag/department"] != "finance"


# ─── Unit: cache safety across clearance levels ───────────────────────────────


class TestCacheSafetyIsolation:
    def test_cl1_and_cl3_use_separate_cache_slots(self, monkeypatch):
        """cl=1 and cl=3 entries never collide in cache."""
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()

        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="ok"):
            mod.lambda_handler(_event(user_id="alice", clearance_level="1"), None)
            mod.lambda_handler(_event(user_id="alice", clearance_level="3"), None)

        # Both calls go to STS (separate cache keys)
        assert sts_mock.assume_role.call_count == 2

    def test_same_user_same_clearance_uses_cache(self, monkeypatch):
        """cl=2 user called twice → assume_role once, second uses cache."""
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()

        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="ok"):
            mod.lambda_handler(_event(user_id="bob", clearance_level="2"), None)
            mod.lambda_handler(_event(user_id="bob", clearance_level="2"), None)

        assert sts_mock.assume_role.call_count == 1

    def test_different_users_same_clearance_use_separate_cache(self, monkeypatch):
        """alice and bob with same clearance get separate STS sessions."""
        _env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response()
        mod = _import_handler()

        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="ok"):
            mod.lambda_handler(_event(user_id="alice", clearance_level="2"), None)
            mod.lambda_handler(_event(user_id="bob", clearance_level="2"), None)

        assert sts_mock.assume_role.call_count == 2


# ─── Post-apply: Lambda env + invocation ─────────────────────────────────────


@pytest.mark.aws
@skip_no_aws
def test_lambda_has_bedrock_model_id():
    import boto3
    fn = boto3.client("lambda", region_name=REGION).get_function(
        FunctionName=STS_LAMBDA_NAME
    )
    env = fn["Configuration"]["Environment"]["Variables"]
    assert "BEDROCK_MODEL_ID" in env
    assert env["BEDROCK_MODEL_ID"] == FAKE_MODEL_ID


@pytest.mark.aws
@skip_no_aws
def test_lambda_invocation_no_message_returns_400():
    """Invoke Lambda directly with empty body → 400 (no AWS GW in path)."""
    import boto3
    payload = {"body": None, "requestContext": {"authorizer": {}}}
    resp = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=STS_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    result = json.loads(resp["Payload"].read())
    assert result["statusCode"] == 400


@pytest.mark.aws
@skip_no_aws
def test_lambda_invocation_missing_context_returns_403():
    """Invoke Lambda with message but no authorizer context → 403."""
    import boto3
    payload = {
        "body": json.dumps({"message": "hello"}),
        "requestContext": {"authorizer": {}},
    }
    resp = boto3.client("lambda", region_name=REGION).invoke(
        FunctionName=STS_LAMBDA_NAME,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode(),
    )
    result = json.loads(resp["Payload"].read())
    assert result["statusCode"] == 403


@pytest.mark.aws
@skip_no_aws
def test_bedrock_role_trust_policy_has_request_tag_conditions():
    """Trust policy must block assume_role without department + clearance_level tags."""
    from urllib.parse import unquote

    import boto3

    iam = boto3.client("iam", region_name=REGION)
    role = iam.get_role(RoleName=BEDROCK_ROLE)["Role"]
    trust = json.loads(unquote(json.dumps(role["AssumeRolePolicyDocument"])))

    for stmt in trust["Statement"]:
        if stmt["Effect"] == "Allow":
            null_cond = stmt.get("Condition", {}).get("Null", {})
            assert null_cond.get("aws:RequestTag/department") == "false", (
                "Trust policy must require aws:RequestTag/department"
            )
            assert null_cond.get("aws:RequestTag/clearance_level") == "false", (
                "Trust policy must require aws:RequestTag/clearance_level"
            )
