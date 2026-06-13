"""API Gateway HTTP client for the IAM Gateway CLI.

Sends authenticated POST /chat requests and returns structured responses.
The Cognito access token is passed as a Bearer token; API Gateway's Lambda
Authorizer requires token_use=='access', validates it, and extracts ABAC context
(department, clearance_level) from the cognito:groups claim.
"""

from dataclasses import dataclass

import requests

_DEFAULT_TIMEOUT = 30  # seconds


@dataclass(frozen=True)
class ChatResponse:
    session_id: str
    user_id: str
    department: str
    clearance_level: int
    response: str


class GatewayError(Exception):
    """Raised on non-2xx responses or network-level failures.

    `status_code` mirrors HTTP status where applicable:
      400 – blocked by input security or validation
      401 – JWT missing, malformed, or signature invalid
      403 – JWT expired/revoked, or topic not permitted at clearance level
      502 – Bedrock downstream error
      503 – cannot reach the API Gateway endpoint
      504 – request timed out
    """

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def send_message(
    message: str,
    access_token: str,
    api_url: str,
    session_id: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> ChatResponse:
    """POST /chat to API Gateway and return the model's response.

    Args:
        message:      Sanitized user message (PII already redacted by client_scan).
        access_token: Cognito access token (JWT) for the Authorization header.
                      The authorizer rejects id tokens (token_use must be 'access').
        api_url:    Full chat endpoint URL, e.g. https://{id}.execute-api.{region}.amazonaws.com/prod/chat
        session_id: If provided, continues an existing conversation (sent in request body).
        timeout:    HTTP request timeout in seconds.

    Raises:
        GatewayError: on any non-2xx response or network failure.
    """
    body: dict = {"message": message}
    if session_id:
        body["session_id"] = session_id

    try:
        resp = requests.post(
            api_url,
            json=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
    except requests.exceptions.Timeout:
        raise GatewayError(504, "Request timed out. Check your network connection.")
    except requests.exceptions.ConnectionError:
        raise GatewayError(503, f"Cannot reach the API endpoint: {api_url}")

    if resp.status_code == 200:
        try:
            data = resp.json()
            return ChatResponse(
                session_id=data["session_id"],
                user_id=data["user_id"],
                department=data["department"],
                clearance_level=int(data["clearance_level"]),
                response=data["response"],
            )
        except (KeyError, ValueError) as exc:
            raise GatewayError(200, f"Unexpected response format: {exc}") from exc

    # Map known status codes to friendly messages
    try:
        error_body = resp.json()
        server_msg = error_body.get("error", "")
        details = error_body.get("details", "")
        detail_str = f": {details}" if details else ""
    except Exception:
        server_msg = resp.text or "no details"
        detail_str = ""

    if resp.status_code == 400:
        raise GatewayError(400, f"Request rejected{detail_str or (': ' + server_msg if server_msg else '')}")
    if resp.status_code == 401:
        raise GatewayError(401, "Authentication failed. Please log in again.")
    if resp.status_code == 403:
        reason = server_msg or "access denied"
        raise GatewayError(403, f"Access denied: {reason}")
    if resp.status_code == 502:
        raise GatewayError(502, f"AI service error: {server_msg or 'Bedrock unavailable'}")

    raise GatewayError(resp.status_code, f"Unexpected server response ({resp.status_code}): {server_msg}")
