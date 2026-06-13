"""
Infrastructure tests for Phase 01 Step 02 – IAM roles + ABAC policies.

Local tests (no AWS):  terraform validate + fmt
Post-apply tests:      boto3 verification of deployed roles/policies
                       – skipped automatically if AWS credentials are absent
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"

PROJECT = "iam-gateway"
ENV = "dev"
REGION = "eu-central-1"
LAMBDA_ROLE = f"{PROJECT}-{ENV}-lambda-exec"
BEDROCK_ROLE = f"{PROJECT}-{ENV}-bedrock-scoped"


# ─── helpers ─────────────────────────────────────────────────────────────────

def _tf(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["terraform"] + args,
        cwd=TF_MAIN,
        capture_output=True,
        text=True,
    )


def _aws_credentials_available() -> bool:
    try:
        import boto3

        boto3.client("sts", region_name=REGION).get_caller_identity()
        return True
    except Exception:
        return False


skip_no_terraform = pytest.mark.skipif(
    not (TF_MAIN / ".terraform").exists(),
    reason="terraform not initialised – run: terraform init -backend=false",
)

skip_no_aws = pytest.mark.skipif(
    not _aws_credentials_available(),
    reason="AWS credentials not configured or terraform not applied yet",
)


# ─── local tests (no AWS) ─────────────────────────────────────────────────────

@skip_no_terraform
def test_main_validates_with_iam():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_main_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, (
        f"Run 'terraform fmt' to fix:\n{result.stdout}"
    )


# ─── post-apply tests (require AWS credentials) ───────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_lambda_exec_role_exists():
    import boto3

    iam = boto3.client("iam", region_name=REGION)
    role = iam.get_role(RoleName=LAMBDA_ROLE)["Role"]
    assert role["RoleName"] == LAMBDA_ROLE


@pytest.mark.aws
@skip_no_aws
def test_lambda_exec_role_has_required_policies():
    import boto3

    iam = boto3.client("iam", region_name=REGION)
    policies = iam.list_role_policies(RoleName=LAMBDA_ROLE)["PolicyNames"]
    assert "cloudwatch-logs" in policies
    assert "sts-assume-bedrock-scoped-role" in policies
    assert "dynamodb-project-tables" in policies


@pytest.mark.aws
@skip_no_aws
def test_bedrock_scoped_role_exists():
    import boto3

    iam = boto3.client("iam", region_name=REGION)
    role = iam.get_role(RoleName=BEDROCK_ROLE)["Role"]
    assert role["RoleName"] == BEDROCK_ROLE


@pytest.mark.aws
@skip_no_aws
def test_bedrock_scoped_role_trust_principal_is_lambda_exec():
    from urllib.parse import unquote

    import boto3

    iam = boto3.client("iam", region_name=REGION)
    role = iam.get_role(RoleName=BEDROCK_ROLE)["Role"]
    trust = json.loads(unquote(json.dumps(role["AssumeRolePolicyDocument"])))

    principals = [
        stmt["Principal"].get("AWS", "")
        for stmt in trust["Statement"]
        if stmt["Effect"] == "Allow"
    ]
    assert any(LAMBDA_ROLE in p for p in principals), (
        f"Expected trust principal to contain '{LAMBDA_ROLE}', got: {principals}"
    )


@pytest.mark.aws
@skip_no_aws
def test_bedrock_policy_requires_session_tags():
    import boto3

    iam = boto3.client("iam", region_name=REGION)
    # boto3 returns PolicyDocument already deserialized as dict
    policy_doc = iam.get_role_policy(
        RoleName=BEDROCK_ROLE,
        PolicyName="bedrock-retrieve-and-generate",
    )["PolicyDocument"]

    conditions = [
        stmt.get("Condition", {})
        for stmt in policy_doc["Statement"]
    ]
    # Every statement must require department + clearance_level tags to be present
    for cond in conditions:
        null_block = cond.get("Null", {})
        assert null_block.get("aws:PrincipalTag/department") == "false", (
            "Missing Null condition for aws:PrincipalTag/department"
        )
        assert null_block.get("aws:PrincipalTag/clearance_level") == "false", (
            "Missing Null condition for aws:PrincipalTag/clearance_level"
        )
