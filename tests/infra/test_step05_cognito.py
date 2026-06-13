"""
Infrastructure tests for Phase 01 Step 05 – Cognito User Pool + Groups.

Local:      terraform validate + fmt
Post-apply: boto3 – pool status, client config, ABAC groups
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"

REGION = "eu-central-1"

DEPARTMENTS = ["engineering", "legal", "finance", "hr", "security"]
CLEARANCE_LEVELS = [0, 1, 2, 3, 4]
EXPECTED_GROUPS = {
    f"dept_{dept}_cl_{lvl}"
    for dept in DEPARTMENTS
    for lvl in CLEARANCE_LEVELS
}


# ─── helpers ─────────────────────────────────────────────────────────────────

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


def _aws_credentials_available() -> bool:
    try:
        import boto3
        boto3.client("sts", region_name=REGION).get_caller_identity()
        return True
    except Exception:
        return False


skip_no_terraform = pytest.mark.skipif(
    not (TF_MAIN / ".terraform").exists(),
    reason="terraform not initialised",
)

skip_no_aws = pytest.mark.skipif(
    not _aws_credentials_available(),
    reason="AWS credentials not configured",
)


# ─── local tests ─────────────────────────────────────────────────────────────

@skip_no_terraform
def test_main_validates_with_cognito():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_main_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── post-apply: user pool ────────────────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_user_pool_exists():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    assert pool["Id"] == pool_id


@pytest.mark.aws
@skip_no_aws
def test_user_pool_admin_only_signup():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    assert pool["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True


@pytest.mark.aws
@skip_no_aws
def test_user_pool_email_as_username():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    assert "email" in pool.get("UsernameAttributes", [])


# ─── post-apply: app client ───────────────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_app_client_has_no_secret():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    client_id = _tf_output("cognito_app_client_id")
    idp = boto3.client("cognito-idp", region_name=REGION)
    client = idp.describe_user_pool_client(
        UserPoolId=pool_id, ClientId=client_id
    )["UserPoolClient"]
    assert "ClientSecret" not in client or not client.get("ClientSecret")


@pytest.mark.aws
@skip_no_aws
def test_app_client_allows_password_auth():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    client_id = _tf_output("cognito_app_client_id")
    idp = boto3.client("cognito-idp", region_name=REGION)
    client = idp.describe_user_pool_client(
        UserPoolId=pool_id, ClientId=client_id
    )["UserPoolClient"]
    flows = client.get("ExplicitAuthFlows", [])
    assert "ALLOW_USER_PASSWORD_AUTH" in flows
    assert "ALLOW_REFRESH_TOKEN_AUTH" in flows


# ─── post-apply: ABAC groups ─────────────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_all_abac_groups_exist():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    idp = boto3.client("cognito-idp", region_name=REGION)

    existing_groups = set()
    paginator = idp.get_paginator("list_groups")
    for page in paginator.paginate(UserPoolId=pool_id):
        for group in page["Groups"]:
            existing_groups.add(group["GroupName"])

    missing = EXPECTED_GROUPS - existing_groups
    assert not missing, f"Missing groups: {sorted(missing)}"


@pytest.mark.aws
@skip_no_aws
def test_abac_group_count():
    import boto3
    pool_id = _tf_output("cognito_user_pool_id")
    idp = boto3.client("cognito-idp", region_name=REGION)

    count = 0
    paginator = idp.get_paginator("list_groups")
    for page in paginator.paginate(UserPoolId=pool_id):
        count += len(page["Groups"])

    assert count == len(EXPECTED_GROUPS), (
        f"Expected {len(EXPECTED_GROUPS)} groups, found {count}"
    )
