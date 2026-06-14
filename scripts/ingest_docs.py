"""Upload test documents to S3 and trigger Bedrock KB sync.

Uploads 15 documents (3 departments × 5 clearance levels) plus their sidecar
.metadata.json files, then starts a Bedrock Knowledge Base ingestion job.

Departments align with the Cognito test users (scripts/create_test_users.py):
engineering, legal, security — so a logged-in user's metadataFilter actually
matches real documents in the Knowledge Base.

Usage:
    uv run python scripts/ingest_docs.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import boto3

REGION    = "eu-central-1"
REPO_ROOT = Path(__file__).resolve().parent.parent
TF_DIR    = REPO_ROOT / "terraform"
TF_BIN    = (
    "C:/Users/Michal/AppData/Local/Microsoft/WinGet/Packages/"
    "Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe/terraform.exe"
)

DEPARTMENTS     = ["engineering", "legal", "security"]
CLEARANCE_LEVELS = [0, 1, 2, 3, 4]
CL_LABELS       = {
    0: "unclassified",
    1: "classified",
    2: "restricted",
    3: "secret",
    4: "top_secret",
}
DOC_PREFIX      = "docs"


# ─── Pure helpers (testable without AWS) ─────────────────────────────────────


def s3_key_for_doc(department: str, clearance_level: int) -> str:
    """Return S3 key for the document text file."""
    return f"{DOC_PREFIX}/{department}/cl_{clearance_level}/{department}_cl{clearance_level}.txt"


def s3_key_for_metadata(doc_key: str) -> str:
    """Return S3 key for the Bedrock sidecar (Bedrock convention: {doc}.metadata.json)."""
    return f"{doc_key}.metadata.json"


def build_metadata_payload(department: str, clearance_level: int) -> dict:
    """Build the Bedrock sidecar metadata payload."""
    return {
        "metadataAttributes": {
            "department": department,
            "clearance_level": clearance_level,
        }
    }


def doc_content(department: str, clearance_level: int) -> str:
    """Generate realistic test document text for the given department/clearance."""
    label = CL_LABELS[clearance_level]
    return (
        f"Department: {department.upper()}\n"
        f"Clearance Level: {clearance_level} ({label})\n\n"
        f"This is a test document for the {department.upper()} department "
        f"at clearance level {clearance_level} ({label}).\n\n"
        f"Authorized personnel with {department} department membership and "
        f"clearance level {clearance_level} or higher may access this content.\n\n"
        f"Procedural guidelines for {department.upper()} operations classified at "
        f"{label} level. Distribution restricted per ABAC policy enforced by "
        f"Bedrock Knowledge Base metadataFilter.\n"
    )


def all_document_pairs() -> list[tuple[str, int]]:
    """Return all (department, clearance_level) combinations."""
    return [(dept, cl) for dept in DEPARTMENTS for cl in CLEARANCE_LEVELS]


# ─── AWS operations ───────────────────────────────────────────────────────────


def _tf_output(name: str) -> str:
    result = subprocess.run(
        [TF_BIN, "output", "-raw", name],
        cwd=TF_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR reading terraform output '{name}':\n{result.stderr}")
        sys.exit(1)
    return result.stdout.strip()


def purge_docs_prefix(s3_client, bucket_name: str) -> None:
    """Delete all objects under docs/ prefix to remove stale data from prior sessions."""
    paginator = s3_client.get_paginator("list_objects_v2")
    keys_to_delete = []
    for page in paginator.paginate(Bucket=bucket_name, Prefix=f"{DOC_PREFIX}/"):
        for obj in page.get("Contents", []):
            keys_to_delete.append({"Key": obj["Key"]})
    if keys_to_delete:
        s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": keys_to_delete})
        print(f"  purged {len(keys_to_delete)} stale objects from {DOC_PREFIX}/")
    else:
        print(f"  no stale objects under {DOC_PREFIX}/")


def upload_documents(s3_client, bucket_name: str) -> list[str]:
    """Upload all test documents and sidecar .metadata.json files to S3.

    Returns list of uploaded document keys (not sidecar keys).
    """
    uploaded: list[str] = []
    for dept, cl in all_document_pairs():
        doc_key  = s3_key_for_doc(dept, cl)
        meta_key = s3_key_for_metadata(doc_key)

        s3_client.put_object(
            Bucket=bucket_name,
            Key=doc_key,
            Body=doc_content(dept, cl).encode("utf-8"),
            ContentType="text/plain",
        )
        s3_client.put_object(
            Bucket=bucket_name,
            Key=meta_key,
            Body=json.dumps(build_metadata_payload(dept, cl)).encode("utf-8"),
            ContentType="application/json",
        )

        print(f"  uploaded: {doc_key}")
        uploaded.append(doc_key)

    return uploaded


def start_ingestion(bedrock_agent, kb_id: str, data_source_id: str) -> str:
    """Start a KB ingestion job and return the job ID."""
    resp   = bedrock_agent.start_ingestion_job(
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )
    job_id = resp["ingestionJob"]["ingestionJobId"]
    status = resp["ingestionJob"]["status"]
    print(f"Ingestion job started: {job_id}  status={status}")
    return job_id


def wait_for_ingestion(
    bedrock_agent,
    kb_id: str,
    data_source_id: str,
    job_id: str,
    timeout: int = 300,
) -> str:
    """Poll until the ingestion job reaches a terminal state. Returns final status."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp   = bedrock_agent.get_ingestion_job(
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            ingestionJobId=job_id,
        )
        status = resp["ingestionJob"]["status"]
        print(f"  ingestion status: {status}")
        if status in ("COMPLETE", "FAILED", "STOPPED"):
            return status
        time.sleep(10)

    return "TIMEOUT"


# ─── Entry point ──────────────────────────────────────────────────────────────


def main() -> None:
    print("=== Bedrock KB Ingest Pipeline ===\n")

    bucket_name    = _tf_output("knowledge_base_bucket_name")
    kb_id          = _tf_output("knowledge_base_id")
    data_source_id = _tf_output("data_source_id")

    print(f"Bucket : {bucket_name}")
    print(f"KB ID  : {kb_id}")
    print(f"DS ID  : {data_source_id}")

    session       = boto3.Session(region_name=REGION)
    s3_client     = session.client("s3")
    bedrock_agent = session.client("bedrock-agent")

    print("\nPurging stale docs from S3...")
    purge_docs_prefix(s3_client, bucket_name)

    print("\nUploading documents...")
    upload_documents(s3_client, bucket_name)

    print("\nStarting ingestion job...")
    job_id = start_ingestion(bedrock_agent, kb_id, data_source_id)

    print("\nWaiting for ingestion to complete (timeout=300s)...")
    final_status = wait_for_ingestion(bedrock_agent, kb_id, data_source_id, job_id)

    if final_status == "COMPLETE":
        print("\nIngestion complete.")
    else:
        print(f"\nERROR: Ingestion ended with status: {final_status}")
        sys.exit(1)


if __name__ == "__main__":
    main()
