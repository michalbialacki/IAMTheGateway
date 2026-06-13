"""Phase 07 / Step 04 – API Gateway HTTP client unit tests.

All tests mock requests.post — no network calls are made.
Covers: success path, all error HTTP codes, network failures, malformed responses.
"""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from cli.gateway import ChatResponse, GatewayError, send_message

# ─── Helpers ──────────────────────────────────────────────────────────────────

_API_URL = "https://abc123.execute-api.eu-central-1.amazonaws.com/prod/chat"
_TOKEN = "eyJ.fake.jwt"

_SUCCESS_BODY = {
    "session_id": "sess-uuid-1234",
    "user_id": "alice",
    "department": "engineering",
    "clearance_level": 2,
    "response": "Here is the summary you requested.",
}


def _mock_response(status_code: int, body: dict | str | None = None) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    if isinstance(body, dict):
        mock.json.return_value = body
        mock.text = json.dumps(body)
    elif isinstance(body, str):
        mock.json.side_effect = ValueError("not json")
        mock.text = body
    else:
        mock.json.return_value = {}
        mock.text = ""
    return mock


# ─── Success path ─────────────────────────────────────────────────────────────


@patch("cli.gateway.requests.post")
def test_success_returns_chat_response(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    result = send_message("Hello", _TOKEN, _API_URL)
    assert isinstance(result, ChatResponse)
    assert result.session_id == "sess-uuid-1234"
    assert result.user_id == "alice"
    assert result.department == "engineering"
    assert result.clearance_level == 2
    assert result.response == "Here is the summary you requested."


@patch("cli.gateway.requests.post")
def test_success_sends_bearer_token(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("Hello", _TOKEN, _API_URL)
    headers = mock_post.call_args[1]["headers"]
    assert headers["Authorization"] == f"Bearer {_TOKEN}"


@patch("cli.gateway.requests.post")
def test_success_posts_to_correct_url(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("Hello", _TOKEN, _API_URL)
    assert mock_post.call_args[0][0] == _API_URL


@patch("cli.gateway.requests.post")
def test_success_sends_message_in_body(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("What is the status?", _TOKEN, _API_URL)
    body = mock_post.call_args[1]["json"]
    assert body["message"] == "What is the status?"


@patch("cli.gateway.requests.post")
def test_session_id_included_when_provided(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("Hello", _TOKEN, _API_URL, session_id="my-session")
    body = mock_post.call_args[1]["json"]
    assert body["session_id"] == "my-session"


@patch("cli.gateway.requests.post")
def test_session_id_omitted_when_none(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("Hello", _TOKEN, _API_URL, session_id=None)
    body = mock_post.call_args[1]["json"]
    assert "session_id" not in body


@patch("cli.gateway.requests.post")
def test_default_timeout_applied(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("Hello", _TOKEN, _API_URL)
    assert mock_post.call_args[1]["timeout"] == 30


@patch("cli.gateway.requests.post")
def test_custom_timeout_applied(mock_post):
    mock_post.return_value = _mock_response(200, _SUCCESS_BODY)
    send_message("Hello", _TOKEN, _API_URL, timeout=10)
    assert mock_post.call_args[1]["timeout"] == 10


# ─── HTTP error codes ─────────────────────────────────────────────────────────


@patch("cli.gateway.requests.post")
def test_400_raises_gateway_error(mock_post):
    mock_post.return_value = _mock_response(400, {"error": "Request blocked", "details": ["injection"]})
    with pytest.raises(GatewayError) as exc_info:
        send_message("bad input", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 400
    assert "injection" in str(exc_info.value)


@patch("cli.gateway.requests.post")
def test_401_raises_gateway_error_with_auth_message(mock_post):
    mock_post.return_value = _mock_response(401, {"error": "Unauthorized"})
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 401
    assert "log in" in str(exc_info.value).lower()


@patch("cli.gateway.requests.post")
def test_403_raises_gateway_error_with_reason(mock_post):
    mock_post.return_value = _mock_response(403, {"error": "Topic not permitted"})
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 403
    assert "denied" in str(exc_info.value).lower()


@patch("cli.gateway.requests.post")
def test_502_raises_gateway_error_with_bedrock_message(mock_post):
    mock_post.return_value = _mock_response(502, {"error": "AccessDeniedException"})
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 502
    assert "AI service" in str(exc_info.value)


@patch("cli.gateway.requests.post")
def test_unexpected_status_raises_gateway_error(mock_post):
    mock_post.return_value = _mock_response(500, {"error": "Internal server error"})
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 500


# ─── Network failures ─────────────────────────────────────────────────────────


@patch("cli.gateway.requests.post")
def test_timeout_raises_gateway_error_504(mock_post):
    mock_post.side_effect = requests.exceptions.Timeout()
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 504
    assert "timed out" in str(exc_info.value).lower()


@patch("cli.gateway.requests.post")
def test_connection_error_raises_gateway_error_503(mock_post):
    mock_post.side_effect = requests.exceptions.ConnectionError()
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 503
    assert "reach" in str(exc_info.value).lower()


# ─── Malformed 200 response ───────────────────────────────────────────────────


@patch("cli.gateway.requests.post")
def test_200_missing_field_raises_gateway_error(mock_post):
    incomplete = {"session_id": "x", "user_id": "alice"}  # missing response, department, clearance_level
    mock_post.return_value = _mock_response(200, incomplete)
    with pytest.raises(GatewayError) as exc_info:
        send_message("Hello", _TOKEN, _API_URL)
    assert exc_info.value.status_code == 200
    assert "format" in str(exc_info.value).lower()


# ─── ChatResponse immutability ────────────────────────────────────────────────


def test_chat_response_is_frozen():
    r = ChatResponse(
        session_id="s", user_id="u", department="d", clearance_level=1, response="r"
    )
    with pytest.raises((AttributeError, TypeError)):
        r.response = "modified"  # type: ignore[misc]
