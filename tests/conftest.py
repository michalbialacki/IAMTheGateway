"""Shared pytest configuration – adds tool binaries to PATH if missing."""

import os
from pathlib import Path

_TOOL_DIRS = [
    # Terraform installed via WinGet
    Path(r"C:\Users\Michal\AppData\Local\Microsoft\WinGet\Packages")
    / "Hashicorp.Terraform_Microsoft.Winget.Source_8wekyb3d8bbwe",
]


def pytest_configure(config):
    extra = [str(d) for d in _TOOL_DIRS if d.exists()]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra) + os.pathsep + os.environ.get("PATH", "")
