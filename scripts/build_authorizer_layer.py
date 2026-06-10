#!/usr/bin/env python3
"""Build Lambda Layer ZIP for the authorizer function.

Uses Docker to install dependencies for Linux Python 3.12, then creates
lambda/authorizer/layer.zip with the correct directory structure for Lambda.

Prerequisites:
  - Docker Desktop must be running
  - Run once before `terraform plan` / `terraform apply`

Usage:
  python scripts/build_authorizer_layer.py
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LAMBDA_DIR = PROJECT_ROOT / "lambda" / "authorizer"
OUTPUT_ZIP = LAMBDA_DIR / "layer.zip"
BUILD_DIR = LAMBDA_DIR / "_build"

DOCKER_EXE = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
DOCKER_IMAGE = "python:3.12-slim-bookworm"


def _docker_path(p: Path) -> str:
    """Convert Windows path to Docker-compatible forward-slash format."""
    return str(p).replace("\\", "/")


def build() -> None:
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True)

    requirements_file = LAMBDA_DIR / "requirements.txt"
    if not requirements_file.exists():
        print(f"ERROR: {requirements_file} not found", file=sys.stderr)
        sys.exit(1)

    cmd = [
        DOCKER_EXE, "run", "--rm",
        "-v", f"{_docker_path(LAMBDA_DIR)}:/var/task:ro",
        "-v", f"{_docker_path(BUILD_DIR)}:/opt/python",
        DOCKER_IMAGE,
        "pip", "install",
        "-r", "/var/task/requirements.txt",
        "-t", "/opt/python",
        "--no-cache-dir",
        "--quiet",
    ]

    print(f"Installing deps into layer using Docker ({DOCKER_IMAGE}) ...")
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        print(f"ERROR: Docker not found at {DOCKER_EXE}. Is Docker Desktop running?", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        print(f"ERROR: Docker build failed (exit {e.returncode})", file=sys.stderr)
        sys.exit(1)

    print(f"Creating {OUTPUT_ZIP} ...")
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(BUILD_DIR.rglob("*")):
            if file_path.is_file():
                rel = file_path.relative_to(BUILD_DIR)
                zf.write(file_path, f"python/{rel}")

    shutil.rmtree(BUILD_DIR)
    size_kb = OUTPUT_ZIP.stat().st_size // 1024
    print(f"Done: {OUTPUT_ZIP} ({size_kb} KB)")


if __name__ == "__main__":
    build()
