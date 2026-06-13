"""Tests for Phase 03 Step 02 – session policy + Lambda integration wiring.

Local tests (no AWS):
  - Unit: session policy structure and content
  - Unit: session policy passed to assume_role
  - terraform validate + fmt

Post-apply tests (require AWS credentials):
  - /chat integration type is AWS_PROXY (not MOCK)
  - integration URI references the sts_session Lambda
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
FAKE_ROLE_ARN = f"arn:aws:iam::123456789012:role/{PROJECT}-{ENV}-bedrock-scoped"


# ─── Marks ───────────────────────────────────────────────────────────────────

skip_no_terraform = pytest.mark.skipif(
    not (TF_MAIN / ".terraform").exists(),
    reason="terraform not initialised",
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


def _tf_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", name],
        cwd=TF_MAIN,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if not value:
        pytest.skip(f"terraform output '{name}' not available – apply first")
    return value


# ─── Unit: _build_session_policy ─────────────────────────────────────────────


class TestBuildSessionPolicy:
    def test_returns_valid_json(self):
        policy_str = _import_handler()._build_session_policy("engineering")
        parsed = json.loads(policy_str)
        assert parsed["Version"] == "2012-10-17"

    def test_contains_allow_statement(self):
        policy_str = _import_handler()._build_session_policy("finance")
        parsed = json.loads(policy_str)
        assert any(s["Effect"] == "Allow" for s in parsed["Statement"])

    def test_contains_bedrock_actions(self):
        policy_str = _import_handler()._build_session_policy("legal")
        parsed = json.loads(policy_str)
        actions = parsed["Statement"][0]["Action"]
        assert "bedrock:RetrieveAndGenerate" in actions
        assert "bedrock:Retrieve" in actions
        assert "bedrock:InvokeModel" in actions

    def test_condition_restricts_to_given_department(self):
        policy_str = _import_handler()._build_session_policy("security")
        parsed = json.loads(policy_str)
        condition = parsed["Statement"][0]["Condition"]
        assert condition["StringEquals"]["aws:PrincipalTag/department"] == "security"

    def test_different_departments_produce_different_policies(self):
        h = _import_handler()
        assert h._build_session_policy("engineering") != h._build_session_policy("legal")

    def test_policy_within_size_limit(self):
        # AWS session policy size limit: 2048 characters
        policy_str = _import_handler()._build_session_policy("a" * 128)
        assert len(policy_str) < 2048


# ─── Unit: assume_role called with Policy kwarg ───────────────────────────────


class TestHandlerSessionPolicy:
    # Handler now calls _invoke_bedrock after assume_role.
    # Patch _get_sts to capture assume_role kwargs; patch _invoke_bedrock to skip Bedrock.

    def _run(self, monkeypatch, department: str = "engineering") -> tuple:
        monkeypatch.setenv("BEDROCK_ROLE_ARN",     FAKE_ROLE_ARN)
        monkeypatch.setenv("BEDROCK_MODEL_ID",     "amazon.titan-text-express-v1")
        monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", "arn:aws:bedrock:eu-central-1::foundation-model/amazon.titan-text-express-v1")
        monkeypatch.setenv("KNOWLEDGE_BASE_ID",    "test-kb-id-s02-session")
        monkeypatch.setenv("AWS_REGION",           "eu-central-1")
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _fake_sts_response()
        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_retrieve_and_generate", return_value="ok"):
            mod.lambda_handler(_event(department=department), None)
        return sts_mock.assume_role.call_args[1]

    def test_policy_passed_to_assume_role(self, monkeypatch):
        kwargs = self._run(monkeypatch, department="finance")
        assert "Policy" in kwargs

    def test_policy_contains_correct_department(self, monkeypatch):
        kwargs = self._run(monkeypatch, department="hr")
        policy = json.loads(kwargs["Policy"])
        dept_cond = policy["Statement"][0]["Condition"]["StringEquals"]
        assert dept_cond["aws:PrincipalTag/department"] == "hr"

    def test_policy_is_valid_json_string(self, monkeypatch):
        kwargs = self._run(monkeypatch)
        assert isinstance(kwargs["Policy"], str)
        json.loads(kwargs["Policy"])

    def test_policy_scope_differs_between_departments(self, monkeypatch):
        assert self._run(monkeypatch, "engineering")["Policy"] != \
               self._run(monkeypatch, "legal")["Policy"]


# ─── Infra: terraform validate ────────────────────────────────────────────────


@skip_no_terraform
def test_terraform_validates_with_lambda_integration():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_terraform_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── Post-apply: /chat integration is Lambda proxy ───────────────────────────


@pytest.mark.aws
@skip_no_aws
def test_chat_integration_type_is_lambda_proxy():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    resources = apigw.get_resources(restApiId=api_id)["items"]
    chat = next((r for r in resources if r.get("path") == "/chat"), None)
    assert chat is not None
    integration = apigw.get_integration(
        restApiId=api_id,
        resourceId=chat["id"],
        httpMethod="POST",
    )
    assert integration["type"] == "AWS_PROXY"


@pytest.mark.aws
@skip_no_aws
def test_chat_integration_uri_contains_sts_lambda():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    resources = apigw.get_resources(restApiId=api_id)["items"]
    chat = next((r for r in resources if r.get("path") == "/chat"), None)
    assert chat is not None
    integration = apigw.get_integration(
        restApiId=api_id,
        resourceId=chat["id"],
        httpMethod="POST",
    )
    assert STS_LAMBDA_NAME in integration.get("uri", "")
