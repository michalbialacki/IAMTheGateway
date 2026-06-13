"""
Infrastructure tests for Phase 01 Step 03 – DynamoDB tables.

Local:      terraform validate + fmt
Post-apply: boto3 – key schema, TTL, SSE, billing mode, GSI
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"

PROJECT = "iam-gateway"
ENV = "dev"
REGION = "eu-central-1"

SESSIONS_TABLE = f"{PROJECT}-{ENV}-sessions"
HISTORY_TABLE = f"{PROJECT}-{ENV}-conversation-history"
REVOKED_TABLE = f"{PROJECT}-{ENV}-revoked-tokens"


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
    reason="terraform not initialised",
)

skip_no_aws = pytest.mark.skipif(
    not _aws_credentials_available(),
    reason="AWS credentials not configured",
)


def _get_table(name: str) -> dict:
    import boto3
    return boto3.client("dynamodb", region_name=REGION).describe_table(
        TableName=name
    )["Table"]


def _key_schema(table: dict) -> dict[str, str]:
    """Return {AttributeName: KeyType} mapping."""
    return {k["AttributeName"]: k["KeyType"] for k in table["KeySchema"]}


# ─── local tests ─────────────────────────────────────────────────────────────

@skip_no_terraform
def test_main_validates_with_dynamodb():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_main_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── post-apply: sessions table ──────────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_sessions_table_exists():
    table = _get_table(SESSIONS_TABLE)
    assert table["TableStatus"] == "ACTIVE"


@pytest.mark.aws
@skip_no_aws
def test_sessions_table_key_schema():
    table = _get_table(SESSIONS_TABLE)
    keys = _key_schema(table)
    assert keys == {"session_id": "HASH"}


@pytest.mark.aws
@skip_no_aws
def test_sessions_table_ttl_enabled():
    import boto3
    resp = boto3.client("dynamodb", region_name=REGION).describe_time_to_live(
        TableName=SESSIONS_TABLE
    )
    ttl = resp["TimeToLiveDescription"]
    assert ttl["TimeToLiveStatus"] == "ENABLED"
    assert ttl["AttributeName"] == "expires_at"


@pytest.mark.aws
@skip_no_aws
def test_sessions_table_has_user_id_gsi():
    table = _get_table(SESSIONS_TABLE)
    gsi_names = [g["IndexName"] for g in table.get("GlobalSecondaryIndexes", [])]
    assert "user_id-index" in gsi_names


@pytest.mark.aws
@skip_no_aws
def test_sessions_table_sse_enabled():
    table = _get_table(SESSIONS_TABLE)
    assert table.get("SSEDescription", {}).get("Status") == "ENABLED"


# ─── post-apply: conversation_history table ───────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_history_table_exists():
    table = _get_table(HISTORY_TABLE)
    assert table["TableStatus"] == "ACTIVE"


@pytest.mark.aws
@skip_no_aws
def test_history_table_key_schema():
    table = _get_table(HISTORY_TABLE)
    keys = _key_schema(table)
    assert keys == {"session_id": "HASH", "turn_index": "RANGE"}


@pytest.mark.aws
@skip_no_aws
def test_history_table_ttl_enabled():
    import boto3
    resp = boto3.client("dynamodb", region_name=REGION).describe_time_to_live(
        TableName=HISTORY_TABLE
    )
    ttl = resp["TimeToLiveDescription"]
    assert ttl["TimeToLiveStatus"] == "ENABLED"
    assert ttl["AttributeName"] == "expires_at"


# ─── post-apply: revoked_tokens table ────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_revoked_tokens_table_exists():
    table = _get_table(REVOKED_TABLE)
    assert table["TableStatus"] == "ACTIVE"


@pytest.mark.aws
@skip_no_aws
def test_revoked_tokens_table_key_schema():
    table = _get_table(REVOKED_TABLE)
    keys = _key_schema(table)
    assert keys == {"jti": "HASH"}


@pytest.mark.aws
@skip_no_aws
def test_revoked_tokens_ttl_enabled():
    import boto3
    resp = boto3.client("dynamodb", region_name=REGION).describe_time_to_live(
        TableName=REVOKED_TABLE
    )
    ttl = resp["TimeToLiveDescription"]
    assert ttl["TimeToLiveStatus"] == "ENABLED"
    assert ttl["AttributeName"] == "expires_at"
