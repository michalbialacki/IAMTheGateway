"""Phase 07 / Step 01 – Cognito login flow unit tests.

All tests run without AWS credentials — boto3 is mocked at the client level.
Covers: successful login, successful refresh, all error branches, config loading.
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from cli.auth import AuthError, AuthTokens, CognitoConfig, login, refresh

# ─── Fixtures ─────────────────────────────────────────────────────────────────

TEST_CONFIG = CognitoConfig(
    user_pool_id="eu-central-1_TESTPOOL",
    client_id="test_client_id_123",
    region="eu-central-1",
)

_FAKE_TOKENS = {
    "IdToken": "id.token.jwt",
    "AccessToken": "access.token.jwt",
    "RefreshToken": "refresh.token.opaque",
    "ExpiresIn": 3600,
}


def _make_client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "test"}},
        "InitiateAuth",
    )


def _mock_cognito(auth_result: dict):
    """Return a mock boto3 cognito-idp client that yields auth_result."""
    mock_client = MagicMock()
    mock_client.initiate_auth.return_value = {"AuthenticationResult": auth_result}
    return mock_client


# ─── CognitoConfig.from_env ───────────────────────────────────────────────────


def test_from_env_reads_required_vars():
    env = {
        "COGNITO_USER_POOL_ID": "eu-central-1_ABC",
        "COGNITO_CLIENT_ID": "client456",
        "AWS_REGION": "us-east-1",
    }
    with patch.dict(os.environ, env, clear=False):
        cfg = CognitoConfig.from_env()
    assert cfg.user_pool_id == "eu-central-1_ABC"
    assert cfg.client_id == "client456"
    assert cfg.region == "us-east-1"


def test_from_env_default_region():
    env = {
        "COGNITO_USER_POOL_ID": "eu-central-1_DEF",
        "COGNITO_CLIENT_ID": "clientXYZ",
    }
    with patch.dict(os.environ, env, clear=False):
        with patch.dict(os.environ, {"AWS_REGION": ""}, clear=False):
            cfg = CognitoConfig.from_env()
    assert cfg.region == "eu-central-1"


def test_from_env_missing_pool_id_raises():
    with patch.dict(os.environ, {"COGNITO_USER_POOL_ID": "", "COGNITO_CLIENT_ID": "x"}, clear=False):
        with pytest.raises(EnvironmentError, match="COGNITO_USER_POOL_ID"):
            CognitoConfig.from_env()


def test_from_env_missing_client_id_raises():
    with patch.dict(os.environ, {"COGNITO_USER_POOL_ID": "x", "COGNITO_CLIENT_ID": ""}, clear=False):
        with pytest.raises(EnvironmentError, match="COGNITO_CLIENT_ID"):
            CognitoConfig.from_env()


def test_from_env_missing_both_raises():
    with patch.dict(os.environ, {"COGNITO_USER_POOL_ID": "", "COGNITO_CLIENT_ID": ""}, clear=False):
        with pytest.raises(EnvironmentError):
            CognitoConfig.from_env()


# ─── login() ──────────────────────────────────────────────────────────────────


@patch("cli.auth.boto3.client")
def test_login_success_returns_tokens(mock_boto):
    mock_boto.return_value = _mock_cognito(_FAKE_TOKENS)
    tokens = login("alice", "Password1!", TEST_CONFIG)

    assert isinstance(tokens, AuthTokens)
    assert tokens.id_token == "id.token.jwt"
    assert tokens.access_token == "access.token.jwt"
    assert tokens.refresh_token == "refresh.token.opaque"
    assert tokens.expires_in == 3600


@patch("cli.auth.boto3.client")
def test_login_calls_initiate_auth_with_correct_params(mock_boto):
    mock_client = _mock_cognito(_FAKE_TOKENS)
    mock_boto.return_value = mock_client

    login("alice", "Password1!", TEST_CONFIG)

    mock_client.initiate_auth.assert_called_once_with(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": "alice", "PASSWORD": "Password1!"},
        ClientId=TEST_CONFIG.client_id,
    )


@patch("cli.auth.boto3.client")
def test_login_uses_correct_region(mock_boto):
    mock_boto.return_value = _mock_cognito(_FAKE_TOKENS)
    login("alice", "pass", TEST_CONFIG)
    mock_boto.assert_called_once_with("cognito-idp", region_name="eu-central-1")


@patch("cli.auth.boto3.client")
def test_login_not_authorized_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("NotAuthorizedException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="Invalid username or password"):
        login("alice", "wrong", TEST_CONFIG)


@patch("cli.auth.boto3.client")
def test_login_user_not_found_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("UserNotFoundException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="Invalid username or password"):
        login("nobody", "pass", TEST_CONFIG)


@patch("cli.auth.boto3.client")
def test_login_unconfirmed_user_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("UserNotConfirmedException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="not confirmed"):
        login("alice", "pass", TEST_CONFIG)


@patch("cli.auth.boto3.client")
def test_login_password_reset_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("PasswordResetRequiredException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="Password reset"):
        login("alice", "pass", TEST_CONFIG)


@patch("cli.auth.boto3.client")
def test_login_unknown_error_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("InternalErrorException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="Authentication failed"):
        login("alice", "pass", TEST_CONFIG)


# ─── refresh() ────────────────────────────────────────────────────────────────

_REFRESH_RESULT = {
    "IdToken": "new.id.token",
    "AccessToken": "new.access.token",
    # Cognito omits RefreshToken in the refresh response — we reuse the original
    "ExpiresIn": 3600,
}


@patch("cli.auth.boto3.client")
def test_refresh_success_returns_new_tokens(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.return_value = {"AuthenticationResult": _REFRESH_RESULT}
    mock_boto.return_value = mock_client

    tokens = refresh("old.refresh.token", TEST_CONFIG)

    assert tokens.id_token == "new.id.token"
    assert tokens.access_token == "new.access.token"
    assert tokens.expires_in == 3600


@patch("cli.auth.boto3.client")
def test_refresh_reuses_original_refresh_token(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.return_value = {"AuthenticationResult": _REFRESH_RESULT}
    mock_boto.return_value = mock_client

    tokens = refresh("my.refresh.token", TEST_CONFIG)

    # Cognito does not rotate refresh tokens — original must be preserved
    assert tokens.refresh_token == "my.refresh.token"


@patch("cli.auth.boto3.client")
def test_refresh_calls_correct_auth_flow(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.return_value = {"AuthenticationResult": _REFRESH_RESULT}
    mock_boto.return_value = mock_client

    refresh("rt", TEST_CONFIG)

    mock_client.initiate_auth.assert_called_once_with(
        AuthFlow="REFRESH_TOKEN_AUTH",
        AuthParameters={"REFRESH_TOKEN": "rt"},
        ClientId=TEST_CONFIG.client_id,
    )


@patch("cli.auth.boto3.client")
def test_refresh_expired_token_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("NotAuthorizedException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="Session expired"):
        refresh("expired.token", TEST_CONFIG)


@patch("cli.auth.boto3.client")
def test_refresh_unknown_error_raises_auth_error(mock_boto):
    mock_client = MagicMock()
    mock_client.initiate_auth.side_effect = _make_client_error("ServiceUnavailableException")
    mock_boto.return_value = mock_client

    with pytest.raises(AuthError, match="Token refresh failed"):
        refresh("rt", TEST_CONFIG)


# ─── AuthTokens immutability ──────────────────────────────────────────────────


def test_auth_tokens_are_frozen():
    tokens = AuthTokens(
        id_token="a", access_token="b", refresh_token="c", expires_in=3600
    )
    with pytest.raises((AttributeError, TypeError)):
        tokens.id_token = "modified"  # type: ignore[misc]
