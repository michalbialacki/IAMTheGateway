"""Tests for Phase 05 Step 01 – Terraform Bedrock Knowledge Base.

Validates that bedrock_kb.tf is syntactically correct and references
all required resources without AWS credentials (local validation only).
"""

import shutil
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_DIR    = REPO_ROOT / "terraform"

_WIN_TF = (
    "C:/Users/Michal/AppData/Local/Microsoft/WinGet/Packages/"
    "Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe/terraform.exe"
)
TF_BIN = _WIN_TF if Path(_WIN_TF).exists() else (shutil.which("terraform") or "terraform")


def _tf(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [TF_BIN, *args],
        cwd=TF_DIR,
        capture_output=True,
        text=True,
    )


# ─── terraform validate ───────────────────────────────────────────────────────


def test_terraform_validate():
    """bedrock_kb.tf passes terraform validate (syntax + reference check)."""
    result = _tf("validate", "-no-color")
    assert result.returncode == 0, (
        f"terraform validate failed:\n{result.stdout}\n{result.stderr}"
    )


# ─── bedrock_kb.tf structure checks ──────────────────────────────────────────


KB_TF = TF_DIR / "bedrock_kb.tf"


class TestBedrocKbTfStructure:
    """File-level checks that all required resource blocks are present."""

    def _content(self) -> str:
        return KB_TF.read_text(encoding="utf-8")

    def test_file_exists(self):
        assert KB_TF.exists(), "bedrock_kb.tf not found"

    def test_iam_role_bedrock_kb(self):
        assert 'resource "aws_iam_role" "bedrock_kb"' in self._content()

    def test_aoss_encryption_policy(self):
        assert 'resource "aws_opensearchserverless_security_policy" "kb_encryption"' in self._content()

    def test_aoss_network_policy(self):
        assert 'resource "aws_opensearchserverless_security_policy" "kb_network"' in self._content()

    def test_aoss_access_policy(self):
        assert 'resource "aws_opensearchserverless_access_policy" "kb"' in self._content()

    def test_aoss_collection(self):
        assert 'resource "aws_opensearchserverless_collection" "kb"' in self._content()

    def test_bedrock_knowledge_base(self):
        assert 'resource "aws_bedrockagent_knowledge_base" "main"' in self._content()

    def test_bedrock_data_source(self):
        assert 'resource "aws_bedrockagent_data_source" "kb"' in self._content()

    def test_embedding_model_titan_v2(self):
        assert "amazon.titan-embed-text-v2:0" in self._content()

    def test_vector_index_name_set(self):
        assert "bedrock-kb-index" in self._content()

    def test_metadata_field_mapping_present(self):
        assert "AMAZON_BEDROCK_METADATA" in self._content()

    def test_fixed_size_chunking(self):
        assert "FIXED_SIZE" in self._content()

    def test_aoss_collection_type_vectorsearch(self):
        assert "VECTORSEARCH" in self._content()


# ─── lambda_sts.tf: KNOWLEDGE_BASE_ID env var ────────────────────────────────


def test_knowledge_base_id_in_lambda_env():
    """KNOWLEDGE_BASE_ID must be wired into the Lambda environment."""
    content = (TF_DIR / "lambda_sts.tf").read_text(encoding="utf-8")
    assert "KNOWLEDGE_BASE_ID" in content


# ─── outputs.tf: KB outputs ──────────────────────────────────────────────────


class TestOutputs:
    def _content(self) -> str:
        return (TF_DIR / "outputs.tf").read_text(encoding="utf-8")

    def test_knowledge_base_id_output(self):
        assert '"knowledge_base_id"' in self._content()

    def test_knowledge_base_arn_output(self):
        assert '"knowledge_base_arn"' in self._content()

    def test_data_source_id_output(self):
        assert '"data_source_id"' in self._content()

    def test_aoss_collection_arn_output(self):
        assert '"aoss_collection_arn"' in self._content()
