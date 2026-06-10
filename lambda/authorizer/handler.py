"""Lambda Authorizer for IAM Gateway.

Verifies Cognito JWT (RS256 via JWKS), checks token revocation in DynamoDB,
and returns an IAM policy with ABAC context: department, clearance_level, jti.
"""

import json
import os
import re
import urllib.request
from typing import Any

import boto3
import jwt
import jwt.exceptions
from jwt.algorithms import RSAAlgorithm

# Module-level singletons — reused across warm Lambda invocations.
_jwks_cache: dict[str, dict] = {}
_dynamodb = None

_GROUP_RE = re.compile(r"^dept_([a-z]+)_cl_(\d+)$")


def _get_dynamodb():
    global _dynamodb
    if _dynamodb is None:
        _dynamodb = boto3.resource("dynamodb")
    return _dynamodb


def _get_jwks(uri: str) -> dict:
    """Fetch and cache JWKS from Cognito. One HTTP call per Lambda container lifetime."""
    if uri not in _jwks_cache:
        with urllib.request.urlopen(uri, timeout=5) as resp:
            _jwks_cache[uri] = json.loads(resp.read())
    return _jwks_cache[uri]


def _verify_jwt(token: str) -> dict:
    """Verify RS256 signature, expiry, and token_use. Returns decoded payload."""
    uri = os.environ["JWKS_URI"]
    jwks = _get_jwks(uri)

    header = jwt.get_unverified_header(token)
    kid = header["kid"]

    key_data = next((k for k in jwks["keys"] if k["kid"] == kid), None)
    if key_data is None:
        raise ValueError(f"Unknown kid: {kid}")

    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))
    payload = jwt.decode(
        token,
        public_key,
        algorithms=["RS256"],
        options={"verify_exp": True, "verify_aud": False},
    )

    if payload.get("token_use") != "access":
        raise ValueError("token_use must be 'access'")

    return payload


def _is_revoked(jti: str) -> bool:
    """Check DynamoDB revoked_tokens table. True if jti is present (revoked)."""
    table = _get_dynamodb().Table(os.environ["REVOKED_TOKENS_TABLE"])
    return "Item" in table.get_item(Key={"jti": jti})


def _parse_groups(groups: list[str]) -> tuple[str, int]:
    """Extract first matching dept_{name}_cl_{level} group. Raises if none found."""
    for g in groups:
        m = _GROUP_RE.match(g)
        if m:
            return m.group(1), int(m.group(2))
    raise ValueError(f"No valid dept group in: {groups}")


def _policy(
    principal: str,
    effect: str,
    resource: str,
    context: dict[str, str] | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}
            ],
        },
    }
    if context:
        doc["context"] = context
    return doc


def lambda_handler(event: dict, _context: Any) -> dict:
    method_arn = event.get("methodArn", "*")
    try:
        auth: str = event.get("authorizationToken", "")
        if not auth.startswith("Bearer "):
            raise ValueError("Missing or malformed Authorization header")

        payload = _verify_jwt(auth[len("Bearer "):])
        jti: str = payload["jti"]

        if _is_revoked(jti):
            return _policy("revoked", "Deny", method_arn)

        dept, cl = _parse_groups(payload.get("cognito:groups", []))
        return _policy(
            principal=payload["sub"],
            effect="Allow",
            resource=method_arn,
            context={
                "user_id": payload["sub"],
                "department": dept,
                "clearance_level": str(cl),
                "jti": jti,
            },
        )
    except (ValueError, KeyError, jwt.exceptions.PyJWTError) as exc:
        raise Exception("Unauthorized") from exc
