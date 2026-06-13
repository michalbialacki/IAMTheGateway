"""Tests for Phase 03 Step 01 – STS handler + ABAC trust policy.

Local tests (no AWS):
  - Unit tests for lambda/sts/handler.py (STS mocked via unittest.mock)
  - terraform validate + fmt
  - Trust policy condition check (parsed from .tf source)

Post-apply tests (require AWS credentials):
  - Lambda deployed with correct env var
  - bedrock_scoped trust policy has RequestTag conditions
"""

import importlib.util
import json
import subprocess
from datetime import datetime, timezone
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


# ─── Marks ───────────────────────────────────────────────────────────────────

skip_no_terraform = pytest.mark.skipif(
    not (TF_MAIN / ".terraform").exists(),
    reason="terraform not initialised – run: terraform init -backend=false",
)


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
    """Load a fresh copy of the STS handler (resets module-level _sts cache)."""
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


def _fake_sts_response() -> dict:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "AQoXnyc4lcK4w//example/token==",
            "Expiration": datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        }
    }


def _mock_sts(response: dict | None = None):
    """Patch boto3.client so _get_sts() returns a mock STS client."""
    mock_client = MagicMock()
    mock_client.assume_role.return_value = response or _fake_sts_response()
    return patch("boto3.client", return_value=mock_client)


def _event(
    user_id: str = "user-abc-123",
    department: str = "engineering",
    clearance_level: str = "2",
    jti: str = "jti-test-001",
) -> dict:
    return {
        "body": json.dumps({"message": "test query"}),
        "requestContext": {
            "authorizer": {
                "user_id": user_id,
                "department": department,
                "clearance_level": clearance_level,
                "jti": jti,
            }
        },
    }


def _tf(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["terraform"] + args,
        cwd=TF_MAIN,
        capture_output=True,
        text=True,
    )


# ─── Unit: sanitize_session_name ─────────────────────────────────────────────


class TestSanitizeSessionName:
    def _call(self, user_id: str) -> str:
        return _import_handler()._sanitize_session_name(user_id)

    def test_valid_uuid_prefixed_with_user(self):
        name = self._call("550e8400-e29b-41d4-a716-446655440000")
        assert name.startswith("user_")

    def test_hyphens_preserved(self):
        name = self._call("abc-def")
        assert "abc-def" in name

    def test_invalid_chars_replaced(self):
        # Spaces, !, # are not in the AWS-allowed set and must be replaced.
        # @ and - are valid RoleSessionName chars and are preserved.
        name = self._call("user with spaces!#")
        assert " " not in name
        assert "!" not in name
        assert "#" not in name

    def test_truncated_to_64_chars(self):
        long_id = "a" * 100
        assert len(self._call(long_id)) <= 64


# ─── Unit: lambda_handler – 403 paths ────────────────────────────────────────


class TestHandlerForbidden:
    # Handler validates body first, then authorizer context.
    # All events include a valid body so the 403 path is exercised.

    def test_missing_request_context(self):
        event = {"body": json.dumps({"message": "test"})}
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 403

    def test_empty_authorizer(self):
        event = {"body": json.dumps({"message": "test"}), "requestContext": {"authorizer": {}}}
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 403

    def test_missing_jti(self):
        event = _event()
        del event["requestContext"]["authorizer"]["jti"]
        result = _import_handler().lambda_handler(event, None)
        assert result["statusCode"] == 403

    def test_clearance_non_numeric(self):
        result = _import_handler().lambda_handler(_event(clearance_level="admin"), None)
        assert result["statusCode"] == 403

    def test_clearance_above_max(self):
        result = _import_handler().lambda_handler(_event(clearance_level="5"), None)
        assert result["statusCode"] == 403

    def test_clearance_negative(self):
        result = _import_handler().lambda_handler(_event(clearance_level="-1"), None)
        assert result["statusCode"] == 403


# ─── Unit: lambda_handler – success path ─────────────────────────────────────


class TestHandlerSuccess:
    # Handler now calls both STS (assume_role) and Bedrock (invoke_model).
    # Patch _get_sts for STS and _invoke_bedrock to avoid real AWS calls.

    def _run(self, monkeypatch, event=None, **event_kwargs):
        monkeypatch.setenv("BEDROCK_ROLE_ARN",     FAKE_ROLE_ARN)
        monkeypatch.setenv("BEDROCK_MODEL_ID",     "amazon.titan-text-express-v1")
        monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", "arn:aws:bedrock:eu-central-1::foundation-model/amazon.titan-text-express-v1")
        monkeypatch.setenv("KNOWLEDGE_BASE_ID",    "test-kb-id-s01")
        monkeypatch.setenv("AWS_REGION",           "eu-central-1")
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _fake_sts_response()
        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="ok"):
            return mod.lambda_handler(event or _event(**event_kwargs), None), sts_mock

    def test_returns_200(self, monkeypatch):
        result, _ = self._run(monkeypatch)
        assert result["statusCode"] == 200

    def test_response_contains_response_field(self, monkeypatch):
        result, _ = self._run(monkeypatch)
        body = json.loads(result["body"])
        assert "response" in body
        assert "credentials" not in body

    def test_response_contains_abac_fields(self, monkeypatch):
        result, _ = self._run(monkeypatch, user_id="u-1", department="legal", clearance_level="1")
        body = json.loads(result["body"])
        assert body["user_id"] == "u-1"
        assert body["department"] == "legal"
        assert body["clearance_level"] == 1

    def test_assume_role_called_with_correct_tags(self, monkeypatch):
        _, sts_mock = self._run(monkeypatch, department="finance", clearance_level="3")
        tags = {t["Key"]: t["Value"] for t in sts_mock.assume_role.call_args[1]["Tags"]}
        assert tags["department"] == "finance"
        assert tags["clearance_level"] == "3"

    def test_assume_role_called_with_correct_arn(self, monkeypatch):
        _, sts_mock = self._run(monkeypatch)
        assert sts_mock.assume_role.call_args[1]["RoleArn"] == FAKE_ROLE_ARN

    def test_clearance_boundary_zero(self, monkeypatch):
        # Clearance 0 has topic restrictions; use an allowed keyword to pass topic gate.
        result, _ = self._run(monkeypatch, event=_event(clearance_level="0") | {
            "body": json.dumps({"message": "What is the company HR policy?"})
        })
        assert result["statusCode"] == 200

    def test_clearance_boundary_four(self, monkeypatch):
        result, _ = self._run(monkeypatch, clearance_level="4")
        assert result["statusCode"] == 200


# ─── Infra: terraform validate ────────────────────────────────────────────────


@skip_no_terraform
def test_terraform_validates_with_sts_lambda():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_terraform_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── Post-apply: AWS state ────────────────────────────────────────────────────


@pytest.mark.aws
@skip_no_aws
def test_sts_lambda_exists():
    import boto3

    fn = boto3.client("lambda", region_name=REGION).get_function(
        FunctionName=STS_LAMBDA_NAME
    )
    assert fn["Configuration"]["FunctionName"] == STS_LAMBDA_NAME


@pytest.mark.aws
@skip_no_aws
def test_sts_lambda_has_bedrock_role_env():
    import boto3

    fn = boto3.client("lambda", region_name=REGION).get_function(
        FunctionName=STS_LAMBDA_NAME
    )
    env_vars = fn["Configuration"]["Environment"]["Variables"]
    assert "BEDROCK_ROLE_ARN" in env_vars
    assert BEDROCK_ROLE in env_vars["BEDROCK_ROLE_ARN"]


@pytest.mark.aws
@skip_no_aws
def test_bedrock_trust_policy_requires_request_tags():
    """Trust policy must enforce aws:RequestTag conditions – deny assume without tags."""
    from urllib.parse import unquote

    import boto3

    iam = boto3.client("iam", region_name=REGION)
    role = iam.get_role(RoleName=BEDROCK_ROLE)["Role"]
    trust = json.loads(unquote(json.dumps(role["AssumeRolePolicyDocument"])))

    conditions = [
        stmt.get("Condition", {})
        for stmt in trust["Statement"]
        if stmt["Effect"] == "Allow"
    ]
    assert conditions, "No Allow statements in trust policy"

    for cond in conditions:
        null_block = cond.get("Null", {})
        assert null_block.get("aws:RequestTag/department") == "false", (
            "Trust policy missing Null condition for aws:RequestTag/department"
        )
        assert null_block.get("aws:RequestTag/clearance_level") == "false", (
            "Trust policy missing Null condition for aws:RequestTag/clearance_level"
        )
