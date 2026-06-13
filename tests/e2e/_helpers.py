"""Importable helpers shared by Phase 08 e2e conftest and test modules.

Kept separate from conftest.py so test files can `from tests.e2e._helpers import ...`
without re-importing the conftest module.
"""

import importlib.util
import os
import re
from dataclasses import dataclass
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
_GROUP_RE = re.compile(r"^dept_([a-z]+)_cl_(\d+)$")
DEFAULT_REGION = "eu-central-1"
HTTP_TIMEOUT = 30


@dataclass(frozen=True)
class E2EUser:
    key: str          # short label, e.g. "alice"
    username: str
    password: str
    department: str
    clearance: int


def _load_test_user_defs() -> tuple[list[dict], str]:
    spec = importlib.util.spec_from_file_location(
        "create_test_users", REPO_ROOT / "scripts" / "create_test_users.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TEST_USERS, mod.TEST_PASSWORD


def build_users() -> list[E2EUser]:
    """Resolve dev test users from create_test_users.py, with per-user env overrides."""
    defs, default_pw = _load_test_user_defs()
    users: list[E2EUser] = []
    for d in defs:
        m = _GROUP_RE.match(d["group"])
        if not m:
            continue
        dept, cl = m.group(1), int(m.group(2))
        key = d["username"].split("@")[0]
        users.append(
            E2EUser(
                key=key,
                username=os.environ.get(f"E2E_USER_{key.upper()}") or d["username"],
                password=os.environ.get(f"E2E_PASS_{key.upper()}") or default_pw,
                department=dept,
                clearance=cl,
            )
        )
    return users


# Resolved once at import (no AWS calls).
E2E_USERS: list[E2EUser] = build_users()
E2E_USERS_BY_KEY: dict[str, E2EUser] = {u.key: u for u in E2E_USERS}


def raw_post(url: str, bearer: str | None, body: dict) -> requests.Response:
    """POST JSON, optionally with a Bearer token. Returns the raw response."""
    headers = {"Content-Type": "application/json"}
    if bearer is not None:
        headers["Authorization"] = f"Bearer {bearer}"
    return requests.post(url, json=body, headers=headers, timeout=HTTP_TIMEOUT)
