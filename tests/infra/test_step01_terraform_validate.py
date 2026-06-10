"""
Infrastructure tests for Phase 01 Step 01 – Terraform project setup.
Requires terraform CLI. No AWS credentials needed (uses -backend=false).
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TF_BOOTSTRAP = REPO_ROOT / "terraform" / "bootstrap"
TF_MAIN = REPO_ROOT / "terraform"


def _tf(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["terraform"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def terraform_available() -> bool:
    try:
        result = subprocess.run(["terraform", "version"], capture_output=True)
        return result.returncode == 0
    except FileNotFoundError:
        return False


skip_no_terraform = pytest.mark.skipif(
    not terraform_available(),
    reason="terraform CLI not found in PATH",
)


@skip_no_terraform
def test_bootstrap_validates():
    init = _tf(["init", "-backend=false", "-no-color"], TF_BOOTSTRAP)
    assert init.returncode == 0, f"bootstrap init failed:\n{init.stderr}"

    validate = _tf(["validate", "-no-color"], TF_BOOTSTRAP)
    assert validate.returncode == 0, f"bootstrap validate failed:\n{validate.stderr}"


@skip_no_terraform
def test_main_validates():
    init = _tf(["init", "-backend=false", "-no-color"], TF_MAIN)
    assert init.returncode == 0, f"main init failed:\n{init.stderr}"

    validate = _tf(["validate", "-no-color"], TF_MAIN)
    assert validate.returncode == 0, f"main validate failed:\n{validate.stderr}"


@skip_no_terraform
def test_bootstrap_fmt_clean():
    fmt = _tf(["fmt", "-check", "-no-color", "-recursive"], TF_BOOTSTRAP)
    assert fmt.returncode == 0, (
        f"terraform fmt check failed (run 'terraform fmt' to fix):\n{fmt.stdout}"
    )


@skip_no_terraform
def test_main_fmt_clean():
    fmt = _tf(["fmt", "-check", "-no-color", "-recursive"], TF_MAIN)
    assert fmt.returncode == 0, (
        f"terraform fmt check failed (run 'terraform fmt' to fix):\n{fmt.stdout}"
    )
