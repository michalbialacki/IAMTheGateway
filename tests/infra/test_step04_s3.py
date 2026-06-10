"""
Infrastructure tests for Phase 01 Step 04 – S3 bucket (Knowledge Base).

Local:      terraform validate + fmt
Post-apply: boto3 – versioning, SSE-KMS, public access block, DenyNonTLS policy
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"

REGION = "eu-central-1"


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


def _bucket_name() -> str:
    """Resolve bucket name from terraform output."""
    result = subprocess.run(
        ["terraform", "output", "-raw", "knowledge_base_bucket_name"],
        cwd=TF_MAIN,
        capture_output=True,
        text=True,
    )
    name = result.stdout.strip()
    if not name:
        pytest.skip("terraform output not available – run terraform apply first")
    return name


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
def test_main_validates_with_s3():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_main_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── post-apply tests ─────────────────────────────────────────────────────────

@skip_no_aws
def test_bucket_exists():
    import boto3
    bucket = _bucket_name()
    s3 = boto3.client("s3", region_name=REGION)
    s3.head_bucket(Bucket=bucket)


@skip_no_aws
def test_bucket_versioning_enabled():
    import boto3
    bucket = _bucket_name()
    s3 = boto3.client("s3", region_name=REGION)
    resp = s3.get_bucket_versioning(Bucket=bucket)
    assert resp.get("Status") == "Enabled", f"Versioning not enabled: {resp}"


@skip_no_aws
def test_bucket_sse_kms():
    import boto3
    bucket = _bucket_name()
    s3 = boto3.client("s3", region_name=REGION)
    resp = s3.get_bucket_encryption(Bucket=bucket)
    rules = resp["ServerSideEncryptionConfiguration"]["Rules"]
    assert len(rules) == 1
    algo = rules[0]["ApplyServerSideEncryptionByDefault"]["SSEAlgorithm"]
    assert algo == "aws:kms", f"Expected aws:kms, got {algo}"
    assert rules[0].get("BucketKeyEnabled") is True


@skip_no_aws
def test_bucket_public_access_fully_blocked():
    import boto3
    bucket = _bucket_name()
    s3 = boto3.client("s3", region_name=REGION)
    resp = s3.get_public_access_block(Bucket=bucket)
    cfg = resp["PublicAccessBlockConfiguration"]
    assert cfg["BlockPublicAcls"] is True
    assert cfg["BlockPublicPolicy"] is True
    assert cfg["IgnorePublicAcls"] is True
    assert cfg["RestrictPublicBuckets"] is True


@skip_no_aws
def test_bucket_policy_denies_non_tls():
    import boto3
    bucket = _bucket_name()
    s3 = boto3.client("s3", region_name=REGION)
    policy = json.loads(s3.get_bucket_policy(Bucket=bucket)["Policy"])

    deny_non_tls = [
        s for s in policy["Statement"]
        if s.get("Effect") == "Deny"
        and s.get("Condition", {}).get("Bool", {}).get("aws:SecureTransport") == "false"
    ]
    assert deny_non_tls, "No DenyNonTLS statement found in bucket policy"
