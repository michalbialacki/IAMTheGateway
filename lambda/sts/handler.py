"""Chat handler for IAM Gateway.

Called by API Gateway after JWT authorizer has validated the token.
Flow per request:
  1. Parse and validate the user message from the request body.
  2. Read ABAC context (user_id, department, clearance_level, jti) set by the authorizer.
  3. Obtain scoped STS credentials via assume_role (cached for the container lifetime).
  4. Call Bedrock with those credentials — credentials never leave this Lambda.
  5. Return the model response to the caller.
"""

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError

from sanitizer.policy import ClearancePolicy, get_policy
from sanitizer.sandwich import build_sandwich_prompt
from sanitizer.sanitizer import scan_input

_sts = None

# Cache: keyed "{user_id}:{clearance_level}" → {"credentials": {...}, "expiry": datetime}
# Lives for the lifetime of the Lambda container (warm invocations reuse it).
_sts_cache: dict[str, dict] = {}

_SESSION_NAME_INVALID = re.compile(r"[^a-zA-Z0-9=,+@._-]")
_MAX_SESSION_NAME_LEN = 64
_MAX_CLEARANCE = 4
_CACHE_BUFFER_SECONDS = 60


# ─── STS helpers ─────────────────────────────────────────────────────────────


def _get_sts():
    global _sts
    if _sts is None:
        _sts = boto3.client("sts")
    return _sts


def _cache_key(user_id: str, clearance_level: int) -> str:
    return f"{user_id}:{clearance_level}"


def _get_cached(user_id: str, clearance_level: int) -> dict | None:
    entry = _sts_cache.get(_cache_key(user_id, clearance_level))
    if entry is None:
        return None
    if datetime.now(timezone.utc) >= entry["expiry"] - timedelta(seconds=_CACHE_BUFFER_SECONDS):
        del _sts_cache[_cache_key(user_id, clearance_level)]
        return None
    return entry["credentials"]


def _put_cached(user_id: str, clearance_level: int, credentials: dict, expiry: datetime) -> None:
    _sts_cache[_cache_key(user_id, clearance_level)] = {
        "credentials": credentials,
        "expiry": expiry,
    }


def _sanitize_session_name(user_id: str) -> str:
    """Build a valid RoleSessionName from user_id. Allowed chars: =,+@-_.a-zA-Z0-9."""
    safe = _SESSION_NAME_INVALID.sub("_", user_id)
    return f"user_{safe}"[:_MAX_SESSION_NAME_LEN]


def _build_session_policy(department: str) -> str:
    """Inline session policy scoped to a single department.

    Effective permissions = intersection(role permissions, this policy).
    Ensures the session can only call Bedrock as the user's own department,
    regardless of what other departments the role would normally allow.
    """
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": [
                "bedrock:RetrieveAndGenerate",
                "bedrock:Retrieve",
                "bedrock:InvokeModel",
            ],
            "Resource": "*",
            "Condition": {
                "StringEquals": {
                    "aws:PrincipalTag/department": department,
                }
            },
        }],
    }
    return json.dumps(policy, separators=(",", ":"))


def _get_credentials(user_id: str, cl: int, department: str) -> dict:
    """Return STS credentials from cache or a fresh assume_role call."""
    cached = _get_cached(user_id, cl)
    if cached:
        return cached

    role_arn = os.environ["BEDROCK_ROLE_ARN"]
    sts_response = _get_sts().assume_role(
        RoleArn=role_arn,
        RoleSessionName=_sanitize_session_name(user_id),
        Tags=[
            {"Key": "department", "Value": department},
            {"Key": "clearance_level", "Value": str(cl)},
        ],
        Policy=_build_session_policy(department),
        DurationSeconds=900,
    )
    raw = sts_response["Credentials"]
    formatted = {
        "AccessKeyId": raw["AccessKeyId"],
        "SecretAccessKey": raw["SecretAccessKey"],
        "SessionToken": raw["SessionToken"],
        "Expiration": raw["Expiration"].isoformat(),
    }
    _put_cached(user_id, cl, formatted, raw["Expiration"])
    return formatted


# ─── Bedrock ─────────────────────────────────────────────────────────────────


def _invoke_bedrock(message: str, credentials: dict, policy: ClearancePolicy) -> str:
    """Call Bedrock InvokeModel with scoped STS credentials. Returns generated text.

    Credentials are used directly here and never returned to the caller.
    Generation parameters (max_tokens, temperature, top_p) come from the
    caller's ClearancePolicy so they scale with clearance level.
    """
    model_id = os.environ["BEDROCK_MODEL_ID"]
    region = os.environ.get("AWS_REGION", "eu-central-1")

    bedrock = boto3.client(
        "bedrock-runtime",
        region_name=region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    request_body = json.dumps({
        "inputText": message,
        "textGenerationConfig": {
            "maxTokenCount": policy.max_tokens,
            "temperature": policy.temperature,
            "topP": policy.top_p,
            "stopSequences": [],
        },
    })

    response = bedrock.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=request_body,
    )
    result = json.loads(response["body"].read())
    return result["results"][0]["outputText"]


# ─── Response helper ──────────────────────────────────────────────────────────


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


# ─── Handler ──────────────────────────────────────────────────────────────────


def lambda_handler(event: dict, _context: Any) -> dict:
    # 1. Parse and validate request body
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _response(400, {"error": "Invalid JSON body"})

    message = (body.get("message") or "").strip()
    if not message:
        return _response(400, {"error": "message is required"})

    # 1b. Server-side 1st sanitize – client is untrusted
    scan = scan_input(message)
    if not scan.is_clean:
        blocked = scan.injection_findings + scan.jailbreak_findings
        return _response(400, {"error": "Request blocked by input security policy", "details": blocked})
    message = scan.redacted_text  # PII stripped; safe to forward downstream

    # 2. Read ABAC context set by the JWT authorizer
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    user_id = authorizer.get("user_id")
    department = authorizer.get("department")
    clearance_raw = authorizer.get("clearance_level")
    jti = authorizer.get("jti")

    if not all([user_id, department, clearance_raw, jti]):
        return _response(403, {"error": "Missing authorizer context"})

    try:
        cl = int(clearance_raw)
        if not (0 <= cl <= _MAX_CLEARANCE):
            raise ValueError(f"out of range: {cl}")
    except (ValueError, TypeError) as exc:
        return _response(403, {"error": f"Invalid clearance_level: {exc}"})

    # 3. Clearance policy: topic gate + generation parameters
    policy = get_policy(cl)
    if not policy.is_topic_allowed(message):
        return _response(403, {"error": "Message topic not permitted at your clearance level"})

    # 4. Sandwich method: wrap sanitized message with department/clearance context
    message = build_sandwich_prompt(message, department, cl)

    # 5. Obtain scoped STS credentials (cached or fresh)
    credentials = _get_credentials(user_id, cl, department)

    # 6. Call Bedrock — credentials stay inside this Lambda
    try:
        output = _invoke_bedrock(message, credentials, policy)
    except ClientError as exc:
        return _response(502, {"error": f"Bedrock error: {exc.response['Error']['Code']}"})

    # 7. Return model response only — never expose credentials to the caller
    return _response(200, {
        "user_id": user_id,
        "department": department,
        "clearance_level": cl,
        "response": output,
    })
