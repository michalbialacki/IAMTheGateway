"""Shared fixtures for Phase 02 auth tests."""

import sys
from pathlib import Path

import boto3
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from get_jwt import get_client_id, get_pool_id, login  # noqa: E402

REGION = "eu-central-1"


@pytest.fixture(scope="session")
def idp():
    return boto3.client("cognito-idp", region_name=REGION)


@pytest.fixture(scope="session")
def pool_id(idp) -> str:
    return get_pool_id(idp)


@pytest.fixture(scope="session")
def client_id(idp, pool_id) -> str:
    return get_client_id(idp, pool_id)


@pytest.fixture(scope="session")
def alice_tokens():
    """Engineering / Restricted (cl=2)."""
    return login("alice@test.local")


@pytest.fixture(scope="session")
def bob_tokens():
    """Legal / Classified (cl=1)."""
    return login("bob@test.local")


@pytest.fixture(scope="session")
def eve_tokens():
    """Security / Top Secret (cl=4)."""
    return login("eve@test.local")
