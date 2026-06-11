"""Tests for Phase 03 Step 03 – STS credentials cache.

All tests are local (no AWS). STS is fully mocked.
Key behaviours:
  - Cache hit:  same user + clearance → assume_role called only once
  - Cache miss: different user or clearance → assume_role called each time
  - TTL:        expired entry or entry within 60s buffer → refreshed
"""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HANDLER_PATH = Path(__file__).resolve().parents[2] / "lambda" / "sts" / "handler.py"

FAKE_ROLE_ARN = "arn:aws:iam::123456789012:role/iam-gateway-dev-bedrock-scoped"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _import_handler():
    """Load a fresh handler module – resets module-level _sts and _sts_cache."""
    spec = importlib.util.spec_from_file_location("sts_handler", _HANDLER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sts_response(expiry: datetime) -> dict:
    return {
        "Credentials": {
            "AccessKeyId": "ASIAIOSFODNN7EXAMPLE",
            "SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            "SessionToken": "AQoXnyc4lcK4w//example/token==",
            "Expiration": expiry,
        }
    }


def _fresh_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=900)


def _expired_expiry() -> datetime:
    return datetime.now(timezone.utc) - timedelta(seconds=1)


def _near_expiry() -> datetime:
    """Within the 60s buffer – should be treated as expired."""
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _event(
    user_id: str = "user-abc",
    department: str = "engineering",
    clearance_level: str = "2",
    jti: str = "jti-001",
) -> dict:
    return {
        "body": json.dumps({"message": "test query"}),
        "requestContext": {
            "authorizer": {
                "user_id": user_id,
                "department": department,
                "clearance_level": clearance_level,
                "jti": jti,
            }
        },
    }


# ─── Unit: _cache_key ────────────────────────────────────────────────────────


class TestCacheKey:
    def test_key_contains_user_id(self):
        key = _import_handler()._cache_key("u-123", 2)
        assert "u-123" in key

    def test_key_contains_clearance(self):
        key = _import_handler()._cache_key("u-123", 3)
        assert "3" in key

    def test_different_clearance_different_key(self):
        mod = _import_handler()
        assert mod._cache_key("u-1", 1) != mod._cache_key("u-1", 2)

    def test_different_user_different_key(self):
        mod = _import_handler()
        assert mod._cache_key("u-1", 1) != mod._cache_key("u-2", 1)


# ─── Unit: _get_cached / _put_cached ─────────────────────────────────────────


class TestCacheHelpers:
    def test_miss_on_empty_cache(self):
        mod = _import_handler()
        assert mod._get_cached("u-1", 2) is None

    def test_hit_after_put(self):
        mod = _import_handler()
        creds = {"AccessKeyId": "AK1", "SecretAccessKey": "SK1",
                 "SessionToken": "ST1", "Expiration": "2099-01-01T00:00:00+00:00"}
        mod._put_cached("u-1", 2, creds, _fresh_expiry())
        assert mod._get_cached("u-1", 2) == creds

    def test_miss_after_expiry(self):
        mod = _import_handler()
        creds = {"AccessKeyId": "AK1", "SecretAccessKey": "SK1",
                 "SessionToken": "ST1", "Expiration": "2000-01-01T00:00:00+00:00"}
        mod._put_cached("u-1", 2, creds, _expired_expiry())
        assert mod._get_cached("u-1", 2) is None

    def test_miss_within_buffer(self):
        """Credentials expiring in <60s are treated as expired."""
        mod = _import_handler()
        creds = {"AccessKeyId": "AK1", "SecretAccessKey": "SK1",
                 "SessionToken": "ST1", "Expiration": "..."}
        mod._put_cached("u-1", 2, creds, _near_expiry())
        assert mod._get_cached("u-1", 2) is None

    def test_expired_entry_removed_from_cache(self):
        mod = _import_handler()
        creds = {"AccessKeyId": "AK1", "SecretAccessKey": "SK1",
                 "SessionToken": "ST1", "Expiration": "2000-01-01T00:00:00+00:00"}
        mod._put_cached("u-1", 2, creds, _expired_expiry())
        mod._get_cached("u-1", 2)  # should delete
        assert mod._cache_key("u-1", 2) not in mod._sts_cache

    def test_isolation_between_clearance_levels(self):
        mod = _import_handler()
        creds_cl1 = {"AccessKeyId": "AK1", "SecretAccessKey": "SK",
                     "SessionToken": "ST", "Expiration": "..."}
        creds_cl3 = {"AccessKeyId": "AK3", "SecretAccessKey": "SK",
                     "SessionToken": "ST", "Expiration": "..."}
        mod._put_cached("u-1", 1, creds_cl1, _fresh_expiry())
        mod._put_cached("u-1", 3, creds_cl3, _fresh_expiry())
        assert mod._get_cached("u-1", 1) == creds_cl1
        assert mod._get_cached("u-1", 3) == creds_cl3


# ─── Unit: lambda_handler cache behaviour ────────────────────────────────────


class TestHandlerCache:
    # Handler now calls _invoke_bedrock after obtaining credentials.
    # All handler tests patch _invoke_bedrock to focus on cache behaviour only.

    def _env(self, monkeypatch):
        monkeypatch.setenv("BEDROCK_ROLE_ARN", FAKE_ROLE_ARN)
        monkeypatch.setenv("BEDROCK_MODEL_ID", "amazon.titan-text-express-v1")
        monkeypatch.setenv("AWS_REGION", "eu-central-1")

    def test_second_call_hits_cache(self, monkeypatch):
        """Same user + clearance called twice → assume_role called only once."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())

        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="ok"):
            mod.lambda_handler(_event(), None)
            mod.lambda_handler(_event(), None)

        assert sts_mock.assume_role.call_count == 1

    def test_different_users_each_call_assume_role(self, monkeypatch):
        """Different user_ids → assume_role called for each."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())

        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="ok"):
            mod.lambda_handler(_event(user_id="alice"), None)
            mod.lambda_handler(_event(user_id="bob"), None)

        assert sts_mock.assume_role.call_count == 2

    def test_different_clearance_each_call_assume_role(self, monkeypatch):
        """Same user, different clearance_level → assume_role called for each."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())

        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="ok"):
            mod.lambda_handler(_event(clearance_level="1"), None)
            mod.lambda_handler(_event(clearance_level="3"), None)

        assert sts_mock.assume_role.call_count == 2

    def test_expired_entry_triggers_new_assume_role(self, monkeypatch):
        """Expired cached entry → fresh assume_role on next call."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        mod = _import_handler()
        stale = {"AccessKeyId": "OLD", "SecretAccessKey": "SK",
                 "SessionToken": "ST", "Expiration": "2000-01-01T00:00:00+00:00"}
        mod._put_cached("user-abc", 2, stale, _expired_expiry())

        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="ok"):
            mod.lambda_handler(_event(), None)

        assert sts_mock.assume_role.call_count == 1

    def test_near_expiry_entry_triggers_new_assume_role(self, monkeypatch):
        """Entry expiring within 60s buffer → fresh assume_role."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        mod = _import_handler()
        stale = {"AccessKeyId": "OLD", "SecretAccessKey": "SK",
                 "SessionToken": "ST", "Expiration": "..."}
        mod._put_cached("user-abc", 2, stale, _near_expiry())

        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="ok"):
            mod.lambda_handler(_event(), None)

        assert sts_mock.assume_role.call_count == 1

    def test_same_bedrock_response_on_cache_hit(self, monkeypatch):
        """On cache hit, both calls get the same response (assume_role called once)."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())

        import json as _json
        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="hello"):
            r1 = _json.loads(mod.lambda_handler(_event(), None)["body"])
            r2 = _json.loads(mod.lambda_handler(_event(), None)["body"])

        assert r1["response"] == r2["response"] == "hello"
        assert sts_mock.assume_role.call_count == 1

    def test_cache_populated_after_first_call(self, monkeypatch):
        """After first assume_role, cache contains the entry."""
        self._env(monkeypatch)
        sts_mock = MagicMock()
        sts_mock.assume_role.return_value = _sts_response(_fresh_expiry())

        mod = _import_handler()
        with patch.object(mod, "_get_sts", return_value=sts_mock), \
             patch.object(mod, "_invoke_bedrock", return_value="ok"):
            mod.lambda_handler(_event(user_id="u-xyz", clearance_level="1"), None)

        assert mod._get_cached("u-xyz", 1) is not None
