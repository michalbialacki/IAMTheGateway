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
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError
from sanitizer.policy import ClearancePolicy, get_policy
from sanitizer.sandwich import build_sandwich_prompt
from sanitizer.sanitizer import scan_input

_sts = None
_dynamodb = None

# Cache: keyed "{user_id}:{department}:{clearance_level}" → {"credentials": {...}, "expiry": datetime}
# Lives for the lifetime of the Lambda container (warm invocations reuse it).
_sts_cache: dict[str, dict] = {}
# Tracks last seen department per user; mismatch triggers full eviction of that user's entries.
_user_last_dept: dict[str, str] = {}

_HISTORY_TTL_SECONDS = 86_400  # 24 h

_SESSION_NAME_INVALID = re.compile(r"[^a-zA-Z0-9=,+@._-]")
_MAX_SESSION_NAME_LEN = 64
_MAX_CLEARANCE = 4
_CACHE_BUFFER_SECONDS = 60


# ─── DynamoDB helpers ────────────────────────────────────────────────────────


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.client("dynamodb", region_name=os.environ.get("AWS_REGION", "eu-central-1"))
    return _dynamodb


def _save_exchange(session_id: str, user_id: str, user_msg: str, assistant_msg: str) -> None:
    table = os.environ["CONVERSATION_TABLE"]
    now = datetime.now(timezone.utc)
    turn_index = int(now.timestamp() * 1000)
    expires_at = int(now.timestamp()) + _HISTORY_TTL_SECONDS
    _get_dynamodb().put_item(
        TableName=table,
        Item={
            "session_id":    {"S": session_id},
            "turn_index":    {"N": str(turn_index)},
            "user_id":       {"S": user_id},
            "user_msg":      {"S": user_msg},
            "assistant_msg": {"S": assistant_msg},
            "expires_at":    {"N": str(expires_at)},
        },
    )


def _load_history(session_id: str, limit: int = 5) -> list[dict]:
    """Return up to `limit` most recent turns in chronological order."""
    table = os.environ.get("CONVERSATION_TABLE", "")
    if not table:
        return []
    response = _get_dynamodb().query(
        TableName=table,
        KeyConditionExpression="session_id = :sid",
        ExpressionAttributeValues={":sid": {"S": session_id}},
        ScanIndexForward=False,
        Limit=limit,
    )
    items = response.get("Items", [])
    return [
        {
            "user_msg":      item["user_msg"]["S"],
            "assistant_msg": item["assistant_msg"]["S"],
        }
        for item in reversed(items)
    ]


# ─── STS helpers ─────────────────────────────────────────────────────────────


def _get_sts():
    global _sts
    if _sts is None:
        _sts = boto3.client("sts")
    return _sts


def _cache_key(user_id: str, department: str, clearance_level: int) -> str:
    return f"{user_id}:{department}:{clearance_level}"


def _get_cached(user_id: str, department: str, clearance_level: int) -> dict | None:
    key = _cache_key(user_id, department, clearance_level)
    entry = _sts_cache.get(key)
    if entry is None:
        return None
    if datetime.now(timezone.utc) >= entry["expiry"] - timedelta(seconds=_CACHE_BUFFER_SECONDS):
        del _sts_cache[key]
        return None
    return entry["credentials"]


def _put_cached(user_id: str, department: str, clearance_level: int, credentials: dict, expiry: datetime) -> None:
    _sts_cache[_cache_key(user_id, department, clearance_level)] = {
        "credentials": credentials,
        "expiry": expiry,
    }


def _evict_user(user_id: str) -> None:
    prefix = f"{user_id}:"
    stale = [k for k in _sts_cache if k.startswith(prefix)]
    for k in stale:
        del _sts_cache[k]


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
    if _user_last_dept.get(user_id) != department:
        _evict_user(user_id)
    _user_last_dept[user_id] = department

    cached = _get_cached(user_id, department, cl)
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
    _put_cached(user_id, department, cl, formatted, raw["Expiration"])
    return formatted


# ─── Bedrock ─────────────────────────────────────────────────────────────────


def build_metadata_filter(department: str, clearance_level: int) -> dict:
    """Build a Bedrock KB metadataFilter that enforces ABAC on retrieval.

    The filter restricts retrieved chunks to documents where:
      - department == caller's department  (exact match, single-tenant isolation)
      - clearance_level <= caller's level  (hierarchical access: cl=2 sees cl=0,1,2)

    Both conditions must hold simultaneously (andAll).
    Value types must match the sidecar metadata types: department→str, clearance_level→int.
    """
    return {
        "andAll": [
            {
                "equals": {
                    "key": "department",
                    "value": department,
                }
            },
            {
                "lessThanOrEquals": {
                    "key": "clearance_level",
                    "value": clearance_level,
                }
            },
        ]
    }


def _retrieve_and_generate(
    user_message: str,
    credentials: dict,
    policy: ClearancePolicy,
    metadata_filter: dict,
) -> str:
    """Call Bedrock RetrieveAndGenerate with ABAC metadataFilter.

    Uses bedrock-agent-runtime (not bedrock-runtime) — R&G retrieves relevant
    chunks from the Knowledge Base and generates a grounded response in one call.
    Credentials are used directly and never returned to the caller.
    """
    kb_id     = os.environ["KNOWLEDGE_BASE_ID"]
    model_arn = os.environ["BEDROCK_KB_MODEL_ARN"]
    region    = os.environ.get("AWS_REGION", "eu-central-1")

    client = boto3.client(
        "bedrock-agent-runtime",
        region_name=region,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )

    response = client.retrieve_and_generate(
        input={"text": user_message},
        retrieveAndGenerateConfiguration={
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {
                "knowledgeBaseId": kb_id,
                "modelArn": model_arn,
                "retrievalConfiguration": {
                    "vectorSearchConfiguration": {
                        "numberOfResults": 5,
                        "filter": metadata_filter,
                    }
                },
                "generationConfiguration": {
                    "inferenceConfig": {
                        "textInferenceConfig": {
                            "temperature": policy.temperature,
                            "topP": policy.top_p,
                            "maxTokens": policy.max_tokens,
                            "stopSequences": [],
                        }
                    }
                },
            },
        },
    )
    return response["output"]["text"]


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

    # Accept session_id from client to continue an existing conversation; generate if absent.
    incoming_session_id = (body.get("session_id") or "").strip()

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

    # 4. Capture sanitized message before sandwich wrapping (stored verbatim in history)
    user_msg_clean = message

    # 5. Resolve session_id: reuse from client or generate new UUID v4
    session_id = incoming_session_id if incoming_session_id else str(uuid.uuid4())

    # 6. Load prior conversation turns and wrap with sandwich + history context
    history = _load_history(session_id)
    message = build_sandwich_prompt(message, department, cl, history=history)

    # 7. Obtain scoped STS credentials (cached or fresh)
    credentials = _get_credentials(user_id, cl, department)

    # 8. Call Bedrock R&G with ABAC metadataFilter — credentials stay inside this Lambda
    metadata_filter = build_metadata_filter(department, cl)
    try:
        output = _retrieve_and_generate(message, credentials, policy, metadata_filter)
    except ClientError as exc:
        return _response(502, {"error": f"Bedrock error: {exc.response['Error']['Code']}"})

    # 9. Persist exchange to DynamoDB for conversation continuity (history injected in Step 03)
    _save_exchange(session_id, user_id, user_msg_clean, output)

    # 10. Return model response — session_id included for client-side conversation continuity
    return _response(200, {
        "session_id": session_id,
        "user_id": user_id,
        "department": department,
        "clearance_level": cl,
        "response": output,
    })
