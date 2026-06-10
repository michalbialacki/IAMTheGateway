"""Admin endpoint: revoke a JWT by recording its jti in DynamoDB revoked_tokens.

ABAC guard: caller must have clearance_level >= 3 (secret or above).
The JWT authorizer sets clearance_level in requestContext.authorizer,
so this check trusts already-verified context – no re-validation needed.
"""

import json
import os
from typing import Any

import boto3

_dynamodb = None
_MIN_CLEARANCE = 3  # secret (3) or top_secret (4) may revoke tokens


def _get_table():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb.Table(os.environ["REVOKED_TOKENS_TABLE"])


def _resp(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def lambda_handler(event: dict, _context: Any) -> dict:
    authorizer = event.get("requestContext", {}).get("authorizer", {})

    try:
        clearance = int(authorizer.get("clearance_level", -1))
    except (ValueError, TypeError):
        clearance = -1

    if clearance < _MIN_CLEARANCE:
        return _resp(403, {"error": "Insufficient clearance to revoke tokens"})

    try:
        body: dict = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _resp(400, {"error": "Invalid JSON body"})

    jti = str(body.get("jti", "")).strip()
    if not jti:
        return _resp(400, {"error": "jti is required"})

    expires_at = body.get("expires_at")
    if expires_at is not None and not isinstance(expires_at, int):
        return _resp(400, {"error": "expires_at must be an integer Unix timestamp"})

    item: dict[str, Any] = {
        "jti": jti,
        "revoked_by": authorizer.get("user_id", "unknown"),
    }
    if expires_at is not None:
        item["expires_at"] = expires_at

    _get_table().put_item(Item=item)
    return _resp(200, {"revoked": True, "jti": jti})
