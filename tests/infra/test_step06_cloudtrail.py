"""
Infrastructure tests for Phase 01 Step 06 – CloudTrail + CloudWatch Logs.

Local:      terraform validate + fmt
Post-apply: boto3 – trail config, CW log group retention, metric filters
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"

REGION = "eu-central-1"
PROJECT = "iam-gateway"
ENV = "dev"
TRAIL_NAME = f"{PROJECT}-{ENV}-trail"
LOG_GROUP = f"/aws/cloudtrail/{PROJECT}-{ENV}"


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


# ─── local tests ─────────────────────────────────────────────────────────────

@skip_no_terraform
def test_main_validates_with_cloudtrail():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_main_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── post-apply: CloudTrail ───────────────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_trail_exists_and_logging():
    import boto3
    ct = boto3.client("cloudtrail", region_name=REGION)
    trails = ct.describe_trails(trailNameList=[TRAIL_NAME], includeShadowTrails=False)
    assert trails["trailList"], f"Trail '{TRAIL_NAME}' not found"
    trail = trails["trailList"][0]
    assert trail["Name"] == TRAIL_NAME

    status = ct.get_trail_status(Name=TRAIL_NAME)
    assert status["IsLogging"] is True


@pytest.mark.aws
@skip_no_aws
def test_trail_is_multi_region():
    import boto3
    ct = boto3.client("cloudtrail", region_name=REGION)
    trail = ct.describe_trails(trailNameList=[TRAIL_NAME], includeShadowTrails=False)["trailList"][0]
    assert trail["IsMultiRegionTrail"] is True


@pytest.mark.aws
@skip_no_aws
def test_trail_log_file_validation_enabled():
    import boto3
    ct = boto3.client("cloudtrail", region_name=REGION)
    trail = ct.describe_trails(trailNameList=[TRAIL_NAME], includeShadowTrails=False)["trailList"][0]
    assert trail["LogFileValidationEnabled"] is True


@pytest.mark.aws
@skip_no_aws
def test_trail_sends_to_cloudwatch():
    import boto3
    ct = boto3.client("cloudtrail", region_name=REGION)
    trail = ct.describe_trails(trailNameList=[TRAIL_NAME], includeShadowTrails=False)["trailList"][0]
    assert "CloudWatchLogsLogGroupArn" in trail
    assert LOG_GROUP in trail["CloudWatchLogsLogGroupArn"]


# ─── post-apply: CloudWatch Log Group ────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_log_group_exists_with_retention():
    import boto3
    logs = boto3.client("logs", region_name=REGION)
    groups = logs.describe_log_groups(logGroupNamePrefix=LOG_GROUP)["logGroups"]
    assert groups, f"Log group '{LOG_GROUP}' not found"
    assert groups[0]["retentionInDays"] == 30


# ─── post-apply: metric filters ──────────────────────────────────────────────

@pytest.mark.aws
@skip_no_aws
def test_unauthorized_api_calls_metric_filter_exists():
    import boto3
    logs = boto3.client("logs", region_name=REGION)
    filters = logs.describe_metric_filters(
        logGroupName=LOG_GROUP,
        filterNamePrefix=f"{PROJECT}-{ENV}-unauthorized-api-calls",
    )["metricFilters"]
    assert filters, "UnauthorizedApiCalls metric filter not found"
    assert filters[0]["metricTransformations"][0]["metricName"] == "UnauthorizedApiCalls"


@pytest.mark.aws
@skip_no_aws
def test_sts_assume_role_metric_filter_exists():
    import boto3
    logs = boto3.client("logs", region_name=REGION)
    filters = logs.describe_metric_filters(
        logGroupName=LOG_GROUP,
        filterNamePrefix=f"{PROJECT}-{ENV}-sts-assume-role",
    )["metricFilters"]
    assert filters, "StsAssumeRoleCalls metric filter not found"
    assert filters[0]["metricTransformations"][0]["metricName"] == "StsAssumeRoleCalls"
