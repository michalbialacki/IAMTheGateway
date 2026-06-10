"""Infrastructure tests for Phase 02 Step 03 – API Gateway REST.

Local:      terraform validate + fmt
Post-apply: boto3 – REST API exists, JWT authorizer config, POST /chat method,
            stage throttling settings.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_MAIN = REPO_ROOT / "terraform"

REGION = "eu-central-1"


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
def test_main_validates_with_api_gateway():
    result = _tf(["validate", "-no-color"])
    assert result.returncode == 0, result.stderr


@skip_no_terraform
def test_main_fmt_clean():
    result = _tf(["fmt", "-check", "-no-color", "-recursive"])
    assert result.returncode == 0, f"Run 'terraform fmt':\n{result.stdout}"


# ─── post-apply: REST API ─────────────────────────────────────────────────────


@skip_no_aws
def test_rest_api_exists():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    api = apigw.get_rest_api(restApiId=api_id)
    assert api["id"] == api_id
    assert api["endpointConfiguration"]["types"] == ["REGIONAL"]


@skip_no_aws
def test_chat_resource_exists():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    resources = apigw.get_resources(restApiId=api_id)["items"]
    paths = [r["path"] for r in resources]
    assert "/chat" in paths


# ─── post-apply: JWT authorizer ───────────────────────────────────────────────


@skip_no_aws
def test_jwt_authorizer_is_token_type():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    authorizers = apigw.get_authorizers(restApiId=api_id)["items"]
    jwt_auth = next((a for a in authorizers if a["name"] == "jwt-authorizer"), None)
    assert jwt_auth is not None, "jwt-authorizer not found"
    assert jwt_auth["type"] == "TOKEN"
    assert jwt_auth["identitySource"] == "method.request.header.Authorization"


@skip_no_aws
def test_jwt_authorizer_ttl_is_zero():
    """TTL=0 ensures revocation takes effect on every request."""
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    authorizers = apigw.get_authorizers(restApiId=api_id)["items"]
    jwt_auth = next((a for a in authorizers if a["name"] == "jwt-authorizer"), None)
    assert jwt_auth is not None
    assert jwt_auth.get("authorizerResultTtlInSeconds", -1) == 0


# ─── post-apply: POST /chat method ───────────────────────────────────────────


@skip_no_aws
def test_chat_post_uses_custom_authorization():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    resources = apigw.get_resources(restApiId=api_id)["items"]
    chat = next((r for r in resources if r.get("path") == "/chat"), None)
    assert chat is not None
    method = apigw.get_method(
        restApiId=api_id,
        resourceId=chat["id"],
        httpMethod="POST",
    )
    assert method["authorizationType"] == "CUSTOM"
    assert "authorizerId" in method


# ─── post-apply: throttling ───────────────────────────────────────────────────


@skip_no_aws
def test_stage_throttling_configured():
    import boto3

    api_id = _tf_output("api_gateway_id")
    apigw = boto3.client("apigateway", region_name=REGION)
    stage = apigw.get_stage(restApiId=api_id, stageName="prod")
    settings = stage.get("methodSettings", {}).get("*/*", {})
    assert settings.get("throttlingRateLimit") == 100.0
    assert settings.get("throttlingBurstLimit") == 50
