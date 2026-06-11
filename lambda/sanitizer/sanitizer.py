"""Input security scanner.

Usage:
    result = scan_input(user_text)
    if not result.is_clean:
        return 400  # blocked
    text_for_bedrock = result.redacted_text  # PII stripped
"""

from dataclasses import dataclass, field

from .patterns import INJECTION_PATTERNS, JAILBREAK_PATTERNS, PII_PATTERNS, Pattern


@dataclass
class ScanResult:
    has_pii: bool = False
    has_injection: bool = False
    has_jailbreak: bool = False
    pii_findings: list[str] = field(default_factory=list)
    injection_findings: list[str] = field(default_factory=list)
    jailbreak_findings: list[str] = field(default_factory=list)
    redacted_text: str = ""

    @property
    def is_clean(self) -> bool:
        return not self.has_injection and not self.has_jailbreak

    @property
    def has_sensitive_data(self) -> bool:
        return self.has_pii


def _apply_patterns(text: str, patterns: list[Pattern]) -> tuple[str, list[str]]:
    findings: list[str] = []
    redacted = text
    for pattern in patterns:
        if pattern.regex.search(redacted):
            findings.append(pattern.name)
            redacted = pattern.regex.sub(pattern.redact_label, redacted)
    return redacted, findings


def redact_pii(text: str) -> tuple[str, list[str]]:
    """Redact PII from text. Returns (redacted_text, list_of_pii_types_found)."""
    return _apply_patterns(text, PII_PATTERNS)


def scan_input(text: str) -> ScanResult:
    """Full security scan of user input.

    PII is redacted in result.redacted_text (safe to forward to Bedrock).
    Injection and jailbreak are scanned on the original text.
    Caller must check result.is_clean before proceeding.
    """
    result = ScanResult()

    redacted, pii_findings = _apply_patterns(text, PII_PATTERNS)
    result.pii_findings = pii_findings
    result.has_pii = bool(pii_findings)
    result.redacted_text = redacted

    _, injection_findings = _apply_patterns(text, INJECTION_PATTERNS)
    result.injection_findings = injection_findings
    result.has_injection = bool(injection_findings)

    _, jailbreak_findings = _apply_patterns(text, JAILBREAK_PATTERNS)
    result.jailbreak_findings = jailbreak_findings
    result.has_jailbreak = bool(jailbreak_findings)

    return result
