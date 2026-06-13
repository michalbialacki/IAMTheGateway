"""Cognito authentication for the IAM Gateway CLI.

Handles USER_PASSWORD_AUTH and REFRESH_TOKEN_AUTH flows.
The AccessToken is the JWT sent as Authorization: Bearer to API Gateway —
the authorizer requires token_use=='access' and reads the Cognito groups
(department + clearance_level) from its cognito:groups claim.
"""

import os
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError


@dataclass(frozen=True)
class CognitoConfig:
    user_pool_id: str
    client_id: str
    region: str

    @staticmethod
    def from_env() -> "CognitoConfig":
        """Build config from environment variables.

        Required: COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID
        Optional: AWS_REGION (default: eu-central-1)
        """
        pool_id = os.environ.get("COGNITO_USER_POOL_ID", "").strip()
        client_id = os.environ.get("COGNITO_CLIENT_ID", "").strip()
        region = os.environ.get("AWS_REGION", "").strip() or "eu-central-1"

        missing = [k for k, v in [
            ("COGNITO_USER_POOL_ID", pool_id),
            ("COGNITO_CLIENT_ID", client_id),
        ] if not v]
        if missing:
            raise EnvironmentError(f"Missing required env vars: {', '.join(missing)}")

        return CognitoConfig(user_pool_id=pool_id, client_id=client_id, region=region)


@dataclass(frozen=True)
class AuthTokens:
    id_token: str      # Cognito identity token (user profile claims; not sent to API GW)
    access_token: str  # JWT sent to API Gateway (token_use='access', carries cognito:groups)
    refresh_token: str # Used to obtain new tokens without re-entering password
    expires_in: int    # Seconds until id_token and access_token expire


class AuthError(Exception):
    """Raised on authentication failure with a user-facing message."""


def login(username: str, password: str, config: CognitoConfig) -> AuthTokens:
    """Authenticate with Cognito and return JWT tokens.

    Uses USER_PASSWORD_AUTH — requires the App Client to have this flow enabled
    (no secret, SRP not required for CLI usage).

    Raises AuthError on invalid credentials or disabled account.
    """
    client = boto3.client("cognito-idp", region_name=config.region)
    try:
        response = client.initiate_auth(
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={
                "USERNAME": username,
                "PASSWORD": password,
            },
            ClientId=config.client_id,
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            raise AuthError("Invalid username or password.") from exc
        if code == "UserNotConfirmedException":
            raise AuthError("Account not confirmed. Check your email.") from exc
        if code == "PasswordResetRequiredException":
            raise AuthError("Password reset required.") from exc
        raise AuthError(f"Authentication failed: {code}") from exc

    result = response["AuthenticationResult"]
    return AuthTokens(
        id_token=result["IdToken"],
        access_token=result["AccessToken"],
        refresh_token=result["RefreshToken"],
        expires_in=result["ExpiresIn"],
    )


def refresh(refresh_token: str, config: CognitoConfig) -> AuthTokens:
    """Exchange a refresh token for a new set of tokens.

    Note: Cognito does not return a new RefreshToken on refresh —
    the original refresh token is reused until it expires (default: 30 days).

    Raises AuthError if the refresh token is expired or revoked.
    """
    client = boto3.client("cognito-idp", region_name=config.region)
    try:
        response = client.initiate_auth(
            AuthFlow="REFRESH_TOKEN_AUTH",
            AuthParameters={"REFRESH_TOKEN": refresh_token},
            ClientId=config.client_id,
        )
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code == "NotAuthorizedException":
            raise AuthError("Session expired. Please log in again.") from exc
        raise AuthError(f"Token refresh failed: {code}") from exc

    result = response["AuthenticationResult"]
    return AuthTokens(
        id_token=result["IdToken"],
        access_token=result["AccessToken"],
        refresh_token=refresh_token,   # Cognito does not rotate refresh tokens
        expires_in=result["ExpiresIn"],
    )
