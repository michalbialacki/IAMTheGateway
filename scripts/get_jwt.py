"""
Authenticates a test user against Cognito and prints tokens.
Reused as a library by tests (see tests/auth/conftest.py).

CLI usage:
    uv run python scripts/get_jwt.py alice@test.local
    uv run python scripts/get_jwt.py alice@test.local DevTest1234
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import boto3

REGION = "eu-central-1"
POOL_NAME = "iam-gateway-dev-user-pool"
CLIENT_NAME = "iam-gateway-dev-cli-client"
DEFAULT_PASSWORD = "DevTest1234"


@dataclass
class Tokens:
    access_token: str
    id_token: str
    refresh_token: str


def get_pool_id(idp) -> str:
    paginator = idp.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for pool in page["UserPools"]:
            if pool["Name"] == POOL_NAME:
                return pool["Id"]
    raise RuntimeError(f"User pool '{POOL_NAME}' not found")


def get_client_id(idp, pool_id: str) -> str:
    paginator = idp.get_paginator("list_user_pool_clients")
    for page in paginator.paginate(UserPoolId=pool_id, MaxResults=60):
        for client in page["UserPoolClients"]:
            if client["ClientName"] == CLIENT_NAME:
                return client["ClientId"]
    raise RuntimeError(f"App client '{CLIENT_NAME}' not found")


def login(username: str, password: str = DEFAULT_PASSWORD) -> Tokens:
    idp = boto3.client("cognito-idp", region_name=REGION)
    pool_id = get_pool_id(idp)
    client_id = get_client_id(idp, pool_id)

    resp = idp.initiate_auth(
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": username, "PASSWORD": password},
        ClientId=client_id,
    )
    auth = resp["AuthenticationResult"]
    return Tokens(
        access_token=auth["AccessToken"],
        id_token=auth["IdToken"],
        refresh_token=auth["RefreshToken"],
    )


if __name__ == "__main__":
    username = sys.argv[1] if len(sys.argv) > 1 else "alice@test.local"
    password = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PASSWORD
    tokens = login(username, password)
    print(f"AccessToken:\n{tokens.access_token}\n")
    print(f"IdToken:\n{tokens.id_token}")
