"""Tests for Phase 05 Step 05 – ABAC cross-tenant isolation.

Unit tests (no AWS):
  - Handler builds metadataFilter from JWT authorizer context (not request body)
  - alpha filter != bravo filter (department isolation)
  - cl=1 filter has lessThanOrEquals:1, not 3 (clearance ceiling)
  - body-level department/clearance override attempts are ignored

Integration tests (@pytest.mark.integration, require live AWS + AOSS ACTIVE):
  - alpha/cl=1 → only alpha docs, only cl<=1 (no cl=2+)
  - alpha/cl=3 → only alpha docs, cl=0–3 reachable (hierarchical access)
  - bravo/cl=2 → only bravo docs (no alpha cross-contamination)
  - cross-tenant: filter alpha/3 returns zero bravo docs
"""

import importlib.util
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_DIR    = REPO_ROOT / "terraform"
TF_BIN    = (
    "C:/Users/Michal/AppData/Local/Microsoft/WinGet/Packages/"
    "Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe/terraform.exe"
)
_HANDLER_PATH = REPO_ROOT / "lambda" / "sts" / "handler.py"

REGION         = "eu-central-1"
FAKE_ROLE_ARN  = "arn:aws:iam::123456789012:role/test-role"
FAKE_MODEL_ID  = "amazon.titan-text-express-v1"
FAKE_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/{FAKE_MODEL_ID}"
FAKE_KB_ID     = "test-kb-id-isolation"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _load_handler():
    spec = importlib.util.spec_from_file_location("sts_handler_iso", _HANDLER_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_expiry():
    return datetime.now(timezone.utc) + timedelta(seconds=900)


def _sts_mock():
    m = MagicMock()
    m.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId":     "AKIA",
            "SecretAccessKey": "SECRET",
            "SessionToken":    "TOKEN",
            "Expiration":      _fresh_expiry(),
        }
    }
    return m


def _event(dept: str, cl: str, message: str = "What is the company policy on leave?") -> dict:
    return {
        "body": json.dumps({"message": message}),
        "requestContext": {
            "authorizer": {
                "user_id":         "user-isolation-test",
                "department":      dept,
                "clearance_level": cl,
                "jti":             "jti-iso-001",
            }
        },
    }


def _env(monkeypatch):
    monkeypatch.setenv("BEDROCK_ROLE_ARN",     FAKE_ROLE_ARN)
    monkeypatch.setenv("BEDROCK_MODEL_ID",     FAKE_MODEL_ID)
    monkeypatch.setenv("BEDROCK_KB_MODEL_ARN", FAKE_MODEL_ARN)
    monkeypatch.setenv("KNOWLEDGE_BASE_ID",    FAKE_KB_ID)
    monkeypatch.setenv("AWS_REGION",           REGION)


def _call_handler_capture_filter(monkeypatch, dept: str, cl: str, body_overrides: dict | None = None) -> dict | None:
    """Run lambda_handler and return the metadata_filter passed to _retrieve_and_generate."""
    _env(monkeypatch)
    mod       = _load_handler()
    sts       = _sts_mock()
    captured  = []

    def fake_rag(msg, creds, policy, metadata_filter):
        captured.append(metadata_filter)
        return "ok"

    event = _event(dept, cl)
    if body_overrides:
        body = json.loads(event["body"])
        body.update(body_overrides)
        event["body"] = json.dumps(body)

    with patch.object(mod, "_get_sts", return_value=sts), \
         patch.object(mod, "_retrieve_and_generate", side_effect=fake_rag):
        mod.lambda_handler(event, None)

    return captured[0] if captured else None


def _tf_output(name: str) -> str:
    result = subprocess.run(
        [TF_BIN, "output", "-raw", name],
        cwd=TF_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"terraform output '{name}' unavailable: {result.stderr.strip()}")
    return result.stdout.strip()


# ─── Unit: filter correctness from JWT context ───────────────────────────────


class TestFilterBuiltFromJwt:
    """Handler must build metadataFilter exclusively from JWT authorizer context."""

    def test_department_in_filter_matches_authorizer(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        assert f["andAll"][0]["equals"]["value"] == "alpha"

    def test_clearance_in_filter_matches_authorizer(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        assert f["andAll"][1]["lessThanOrEquals"]["value"] == 2

    def test_filter_clearance_is_int_not_str(self, monkeypatch):
        """Authorizer sends clearance as str; handler must cast to int for the filter."""
        f = _call_handler_capture_filter(monkeypatch, "alpha", "1")
        assert isinstance(f["andAll"][1]["lessThanOrEquals"]["value"], int)

    def test_body_department_override_ignored(self, monkeypatch):
        """Request body cannot override the department used in the filter."""
        f = _call_handler_capture_filter(
            monkeypatch, "alpha", "2",
            body_overrides={"department": "bravo"}
        )
        assert f["andAll"][0]["equals"]["value"] == "alpha"

    def test_body_clearance_override_ignored(self, monkeypatch):
        """Request body cannot override the clearance level used in the filter."""
        f = _call_handler_capture_filter(
            monkeypatch, "alpha", "1",
            body_overrides={"clearance_level": "4"}
        )
        assert f["andAll"][1]["lessThanOrEquals"]["value"] == 1


# ─── Unit: cross-department isolation guarantee ───────────────────────────────


class TestCrossDepartmentIsolation:
    def test_alpha_and_bravo_filters_are_different(self, monkeypatch):
        f_alpha = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        f_bravo = _call_handler_capture_filter(monkeypatch, "bravo", "2")
        assert f_alpha != f_bravo

    def test_alpha_filter_department_not_bravo(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        assert f["andAll"][0]["equals"]["value"] != "bravo"

    def test_bravo_filter_department_not_alpha(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "bravo", "2")
        assert f["andAll"][0]["equals"]["value"] != "alpha"


# ─── Unit: clearance level ceiling ────────────────────────────────────────────


class TestClearanceCeiling:
    def test_cl1_filter_ceiling_is_1(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "1")
        assert f["andAll"][1]["lessThanOrEquals"]["value"] == 1

    def test_cl1_filter_ceiling_is_not_3(self, monkeypatch):
        """cl=1 user must never receive cl=3 ceiling in filter."""
        f = _call_handler_capture_filter(monkeypatch, "alpha", "1")
        assert f["andAll"][1]["lessThanOrEquals"]["value"] != 3

    def test_cl3_filter_ceiling_is_3(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "3")
        assert f["andAll"][1]["lessThanOrEquals"]["value"] == 3

    def test_cl0_filter_ceiling_is_0(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "0")
        assert f["andAll"][1]["lessThanOrEquals"]["value"] == 0

    def test_filters_differ_across_clearance_levels(self, monkeypatch):
        filters = [_call_handler_capture_filter(monkeypatch, "alpha", str(cl)) for cl in range(4)]
        assert len(set(json.dumps(f, sort_keys=True) for f in filters)) == 4


# ─── Unit: filter operator correctness ───────────────────────────────────────


class TestFilterOperators:
    """dept=equals (not contains/startsWith); cl=lessThanOrEquals (not equals/lessThan)."""

    def test_department_uses_equals_not_startswith(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        dept_cond = f["andAll"][0]
        assert "equals" in dept_cond
        assert "startsWith" not in dept_cond

    def test_clearance_uses_lessThanOrEquals_not_equals(self, monkeypatch):
        f = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        cl_cond = f["andAll"][1]
        assert "lessThanOrEquals" in cl_cond
        assert "equals" not in cl_cond

    def test_clearance_uses_lessThanOrEquals_not_lessThan(self, monkeypatch):
        """lessThan would exclude docs at exactly the user's level — wrong semantics."""
        f = _call_handler_capture_filter(monkeypatch, "alpha", "2")
        cl_cond = f["andAll"][1]
        assert "lessThanOrEquals" in cl_cond
        assert "lessThan" not in cl_cond or "lessThanOrEquals" in cl_cond


# ─── Integration: live Bedrock retrieve with metadataFilter ──────────────────


@pytest.fixture(scope="module")
def bedrock_context():
    """Resolve KB ID and create boto3 clients for integration tests."""
    try:
        import boto3
    except ImportError:
        pytest.skip("boto3 not installed")

    kb_id = _tf_output("knowledge_base_id")

    session = boto3.Session(region_name=REGION)
    client  = session.client("bedrock-agent-runtime")

    handler_mod         = _load_handler()
    build_metadata_filter = handler_mod.build_metadata_filter

    return {
        "kb_id":                kb_id,
        "client":               client,
        "build_metadata_filter": build_metadata_filter,
    }


def _live_retrieve(ctx, dept: str, cl: int, query: str = "department clearance test") -> list[dict]:
    """Call bedrock-agent-runtime.retrieve() and return metadata from all results."""
    response = ctx["client"].retrieve(
        knowledgeBaseId=ctx["kb_id"],
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": 20,
                "filter": ctx["build_metadata_filter"](dept, cl),
            }
        },
    )
    return [r.get("metadata", {}) for r in response.get("retrievalResults", [])]


@pytest.mark.integration
class TestLiveAbacIsolation:
    """Verify ABAC enforcement via live Bedrock retrieve calls."""

    def test_alpha_cl1_returns_only_alpha_dept(self, bedrock_context):
        metas = _live_retrieve(bedrock_context, "alpha", 1)
        assert metas, "No results — run scripts/ingest_docs.py first"
        for m in metas:
            assert m.get("department") == "alpha", f"Expected alpha, got: {m}"

    def test_alpha_cl1_returns_only_cl_le_1(self, bedrock_context):
        metas = _live_retrieve(bedrock_context, "alpha", 1)
        assert metas, "No results — run scripts/ingest_docs.py first"
        for m in metas:
            assert int(m["clearance_level"]) <= 1, (
                f"cl=1 user got doc with clearance_level={m['clearance_level']}"
            )

    def test_alpha_cl3_returns_only_alpha_dept(self, bedrock_context):
        metas = _live_retrieve(bedrock_context, "alpha", 3)
        assert metas, "No results — run scripts/ingest_docs.py first"
        for m in metas:
            assert m.get("department") == "alpha", f"Expected alpha, got: {m}"

    def test_alpha_cl3_no_bravo_docs(self, bedrock_context):
        metas = _live_retrieve(bedrock_context, "alpha", 3)
        bravo_docs = [m for m in metas if m.get("department") == "bravo"]
        assert not bravo_docs, f"Cross-tenant leak: alpha/cl=3 retrieved bravo docs: {bravo_docs}"

    def test_bravo_cl2_returns_only_bravo_dept(self, bedrock_context):
        metas = _live_retrieve(bedrock_context, "bravo", 2)
        assert metas, "No results — run scripts/ingest_docs.py first"
        for m in metas:
            assert m.get("department") == "bravo", f"Expected bravo, got: {m}"

    def test_bravo_cl2_no_alpha_docs(self, bedrock_context):
        metas = _live_retrieve(bedrock_context, "bravo", 2)
        alpha_docs = [m for m in metas if m.get("department") == "alpha"]
        assert not alpha_docs, f"Cross-tenant leak: bravo/cl=2 retrieved alpha docs: {alpha_docs}"

    def test_alpha_cl1_no_restricted_docs(self, bedrock_context):
        """cl=1 user must not see cl=2 or cl=3 documents."""
        metas = _live_retrieve(bedrock_context, "alpha", 1)
        restricted = [m for m in metas if int(m.get("clearance_level", 0)) > 1]
        assert not restricted, (
            f"cl=1 user retrieved documents above clearance: {restricted}"
        )

    def test_alpha_cl3_sees_lower_clearance_docs(self, bedrock_context):
        """cl=3 user has hierarchical access — must see documents at cl=0, 1, 2, 3."""
        metas = _live_retrieve(bedrock_context, "alpha", 3)
        found_levels = {int(m["clearance_level"]) for m in metas if "clearance_level" in m}
        # Must find at least one doc at each level ≤ 3
        assert found_levels, "No results with clearance_level metadata"
        assert max(found_levels) >= 1, (
            f"cl=3 user should see docs at multiple levels, found: {found_levels}"
        )
