"""Tests for Phase 05 Step 02 – Ingest pipeline.

Unit tests (no AWS):
  - build_metadata_payload: structure and types
  - s3_key_for_doc / s3_key_for_metadata: key format and naming convention
  - all_document_pairs: full matrix coverage, no duplicates

Integration tests (require AOSS ACTIVE + Bedrock KB sync, marked @pytest.mark.aoss):
  - All 8 document .txt keys exist in S3
  - All 8 sidecar .metadata.json keys exist in S3
  - Metadata content matches expected department / clearance_level per document
  - At least one ingestion job reached COMPLETE state
"""

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

# ─── Load script module ───────────────────────────────────────────────────────

REPO_ROOT   = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ingest_docs.py"
TF_DIR      = REPO_ROOT / "terraform"
TF_BIN      = (
    "C:/Users/Michal/AppData/Local/Microsoft/WinGet/Packages/"
    "Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe/terraform.exe"
)


def _load_ingest():
    spec = importlib.util.spec_from_file_location("ingest_docs", SCRIPT_PATH)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ingest = _load_ingest()


# ─── Unit tests: helpers ──────────────────────────────────────────────────────


class TestBuildMetadataPayload:
    def test_top_level_key(self):
        payload = ingest.build_metadata_payload("alpha", 1)
        assert "metadataAttributes" in payload

    def test_department_present(self):
        payload = ingest.build_metadata_payload("alpha", 1)
        assert payload["metadataAttributes"]["department"] == "alpha"

    def test_clearance_level_present(self):
        payload = ingest.build_metadata_payload("alpha", 1)
        assert payload["metadataAttributes"]["clearance_level"] == 1

    def test_department_is_string(self):
        payload = ingest.build_metadata_payload("bravo", 3)
        assert isinstance(payload["metadataAttributes"]["department"], str)

    def test_clearance_level_is_int(self):
        payload = ingest.build_metadata_payload("bravo", 3)
        assert isinstance(payload["metadataAttributes"]["clearance_level"], int)

    def test_no_extra_keys(self):
        payload = ingest.build_metadata_payload("alpha", 0)
        assert set(payload["metadataAttributes"].keys()) == {"department", "clearance_level"}

    def test_serializable_to_json(self):
        payload = ingest.build_metadata_payload("alpha", 2)
        dumped  = json.dumps(payload)
        parsed  = json.loads(dumped)
        assert parsed == payload


class TestS3Keys:
    def test_doc_key_starts_with_prefix(self):
        key = ingest.s3_key_for_doc("alpha", 0)
        assert key.startswith("docs/")

    def test_doc_key_contains_department(self):
        key = ingest.s3_key_for_doc("bravo", 2)
        assert "bravo" in key

    def test_doc_key_contains_clearance_level(self):
        key = ingest.s3_key_for_doc("alpha", 3)
        assert "3" in key

    def test_doc_key_ends_with_txt(self):
        key = ingest.s3_key_for_doc("alpha", 1)
        assert key.endswith(".txt")

    def test_metadata_key_is_doc_plus_suffix(self):
        doc_key  = ingest.s3_key_for_doc("alpha", 1)
        meta_key = ingest.s3_key_for_metadata(doc_key)
        assert meta_key == doc_key + ".metadata.json"

    def test_all_doc_keys_unique(self):
        keys = [ingest.s3_key_for_doc(d, cl) for d, cl in ingest.all_document_pairs()]
        assert len(keys) == len(set(keys))

    def test_all_meta_keys_unique(self):
        meta_keys = [
            ingest.s3_key_for_metadata(ingest.s3_key_for_doc(d, cl))
            for d, cl in ingest.all_document_pairs()
        ]
        assert len(meta_keys) == len(set(meta_keys))


class TestAllDocumentPairs:
    def test_count(self):
        pairs = ingest.all_document_pairs()
        assert len(pairs) == len(ingest.DEPARTMENTS) * len(ingest.CLEARANCE_LEVELS)

    def test_all_departments_represented(self):
        pairs = ingest.all_document_pairs()
        depts = {d for d, _ in pairs}
        assert depts == set(ingest.DEPARTMENTS)

    def test_all_clearance_levels_represented(self):
        pairs = ingest.all_document_pairs()
        levels = {cl for _, cl in pairs}
        assert levels == set(ingest.CLEARANCE_LEVELS)

    def test_no_duplicates(self):
        pairs = ingest.all_document_pairs()
        assert len(pairs) == len(set(pairs))


class TestDocContent:
    def test_contains_department(self):
        content = ingest.doc_content("alpha", 1)
        assert "alpha" in content.lower()

    def test_contains_clearance_label(self):
        content = ingest.doc_content("alpha", 2)
        assert "restricted" in content.lower()

    def test_non_empty(self):
        content = ingest.doc_content("bravo", 0)
        assert len(content) > 50


# ─── Integration tests (live AWS) ────────────────────────────────────────────


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


@pytest.fixture(scope="module")
def aws_context():
    """Resolve bucket name, KB ID, and data source ID from terraform outputs."""
    try:
        import boto3
    except ImportError:
        pytest.skip("boto3 not installed")

    bucket_name    = _tf_output("knowledge_base_bucket_name")
    kb_id          = _tf_output("knowledge_base_id")
    data_source_id = _tf_output("data_source_id")

    session       = boto3.Session(region_name=ingest.REGION)
    s3_client     = session.client("s3")
    bedrock_agent = session.client("bedrock-agent")

    return {
        "bucket_name":    bucket_name,
        "kb_id":          kb_id,
        "data_source_id": data_source_id,
        "s3":             s3_client,
        "bedrock_agent":  bedrock_agent,
    }


@pytest.mark.aoss
class TestS3State:
    """Verify S3 state after ingest_docs.py has been run."""

    def _head(self, ctx, key: str):
        try:
            ctx["s3"].head_object(Bucket=ctx["bucket_name"], Key=key)
            return True
        except Exception:
            return False

    def test_all_document_txt_files_exist(self, aws_context):
        missing = []
        for dept, cl in ingest.all_document_pairs():
            key = ingest.s3_key_for_doc(dept, cl)
            if not self._head(aws_context, key):
                missing.append(key)
        assert not missing, f"Missing S3 document keys: {missing}"

    def test_all_metadata_sidecar_files_exist(self, aws_context):
        missing = []
        for dept, cl in ingest.all_document_pairs():
            key = ingest.s3_key_for_metadata(ingest.s3_key_for_doc(dept, cl))
            if not self._head(aws_context, key):
                missing.append(key)
        assert not missing, f"Missing S3 metadata keys: {missing}"

    def test_metadata_content_department_correct(self, aws_context):
        """Each sidecar's department attribute must match the file's folder."""
        for dept, cl in ingest.all_document_pairs():
            meta_key = ingest.s3_key_for_metadata(ingest.s3_key_for_doc(dept, cl))
            obj      = aws_context["s3"].get_object(
                Bucket=aws_context["bucket_name"],
                Key=meta_key,
            )
            payload = json.loads(obj["Body"].read())
            assert payload["metadataAttributes"]["department"] == dept, (
                f"Wrong department in {meta_key}"
            )

    def test_metadata_content_clearance_level_correct(self, aws_context):
        """Each sidecar's clearance_level must be an int matching its folder."""
        for dept, cl in ingest.all_document_pairs():
            meta_key = ingest.s3_key_for_metadata(ingest.s3_key_for_doc(dept, cl))
            obj      = aws_context["s3"].get_object(
                Bucket=aws_context["bucket_name"],
                Key=meta_key,
            )
            payload = json.loads(obj["Body"].read())
            assert payload["metadataAttributes"]["clearance_level"] == cl, (
                f"Wrong clearance_level in {meta_key}"
            )
            assert isinstance(payload["metadataAttributes"]["clearance_level"], int), (
                f"clearance_level must be int, not str, in {meta_key}"
            )


@pytest.mark.aoss
class TestIngestionJob:
    """Verify that at least one ingestion job reached COMPLETE state."""

    def test_ingestion_job_completed(self, aws_context):
        resp = aws_context["bedrock_agent"].list_ingestion_jobs(
            knowledgeBaseId=aws_context["kb_id"],
            dataSourceId=aws_context["data_source_id"],
        )
        jobs = resp.get("ingestionJobSummaries", [])
        assert jobs, "No ingestion jobs found — run scripts/ingest_docs.py first"

        completed = [j for j in jobs if j["status"] == "COMPLETE"]
        assert completed, (
            f"No COMPLETE ingestion job found. Statuses: "
            f"{[j['status'] for j in jobs]}"
        )
