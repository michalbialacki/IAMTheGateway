"""Shared pytest configuration – adds tool binaries to PATH and lambda/ to sys.path."""

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

_TOOL_DIRS = [
    # Terraform installed via WinGet
    Path(r"C:\Users\Michal\AppData\Local\Microsoft\WinGet\Packages")
    / "Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe",
]


def pytest_configure(config):
    extra = [str(d) for d in _TOOL_DIRS if d.exists()]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra) + os.pathsep + os.environ.get("PATH", "")

    # Make shared Lambda packages (e.g. sanitizer/) importable in all test files.
    lambda_dir = str(_REPO_ROOT / "lambda")
    if lambda_dir not in sys.path:
        sys.path.insert(0, lambda_dir)
