"""Client-side input scanner (defense-in-depth layer).

Runs the same regex patterns as the server-side Lambda sanitizer BEFORE
the request is sent over the network. This catches obvious injection/jailbreak
attempts locally and gives the user an immediate rejection instead of a
round-trip to API Gateway.

The server always runs its own scan regardless — this is defense-in-depth,
not a replacement.
"""

import sys
from pathlib import Path

# Make lambda/sanitizer importable when the CLI is run directly (outside pytest).
# pytest's conftest.py already handles this for the test suite.
_lambda_dir = str(Path(__file__).resolve().parent.parent / "lambda")
if _lambda_dir not in sys.path:
    sys.path.insert(0, _lambda_dir)

from sanitizer.sanitizer import ScanResult, scan_input  # noqa: E402

__all__ = ["ScanResult", "client_scan", "format_scan_warning"]


def client_scan(message: str) -> ScanResult:
    """Run a full security scan on the user's message.

    Returns a ScanResult; caller must check `.is_clean` before sending.
    If not clean, `.injection_findings` and `.jailbreak_findings` name the
    triggered patterns. `.redacted_text` has PII stripped (used as the
    actual payload sent to the API).
    """
    return scan_input(message)


def format_scan_warning(result: ScanResult) -> str:
    """Return a concise human-readable explanation of why a message was blocked."""
    reasons: list[str] = []
    if result.injection_findings:
        reasons.append(f"prompt injection ({', '.join(result.injection_findings)})")
    if result.jailbreak_findings:
        reasons.append(f"jailbreak attempt ({', '.join(result.jailbreak_findings)})")
    if result.pii_findings and not reasons:
        # PII alone does not block — but if redaction failed we surface it
        reasons.append(f"PII detected ({', '.join(result.pii_findings)})")
    return "Blocked: " + "; ".join(reasons) if reasons else "Blocked: unknown reason"
