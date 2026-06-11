"""Compiled regex patterns for input security scanning.

Three categories:
  PII_PATTERNS       – personally identifiable information (redact before sending to Bedrock)
  INJECTION_PATTERNS – prompt injection attempts (block the request)
  JAILBREAK_PATTERNS – jailbreak/bypass attempts (block the request)
"""

import re
from typing import NamedTuple


class Pattern(NamedTuple):
    name: str
    regex: re.Pattern
    redact_label: str


# ─── PII ──────────────────────────────────────────────────────────────────────

PII_PATTERNS: list[Pattern] = [
    Pattern(
        name="pesel",
        regex=re.compile(r"\b\d{11}\b"),
        redact_label="[REDACTED_PESEL]",
    ),
    Pattern(
        name="iban_pl",
        # Must precede credit_card: the 4×4 digit groups inside an IBAN would
        # otherwise be consumed by the credit_card pattern first.
        regex=re.compile(
            r"\bPL\d{2}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}[\s]?\d{4}\b"
        ),
        redact_label="[REDACTED_IBAN]",
    ),
    Pattern(
        name="credit_card",
        regex=re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
        redact_label="[REDACTED_CARD]",
    ),
    Pattern(
        name="email",
        regex=re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        redact_label="[REDACTED_EMAIL]",
    ),
    Pattern(
        name="phone",
        # Matches +48 prefix form or standalone 9-digit form (Polish numbers).
        # Alternation prevents matching within longer digit sequences (e.g. PESEL).
        regex=re.compile(
            r"(?:\+48[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}|\b\d{3}[\s\-]?\d{3}[\s\-]?\d{3}\b)"
        ),
        redact_label="[REDACTED_PHONE]",
    ),
    Pattern(
        name="ip_address",
        regex=re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
            r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b"
        ),
        redact_label="[REDACTED_IP]",
    ),
]


# ─── Prompt injection ─────────────────────────────────────────────────────────

INJECTION_PATTERNS: list[Pattern] = [
    Pattern(
        name="ignore_instructions",
        regex=re.compile(
            r"\bignore\s+(previous|above|all|prior|your)\s+"
            r"(instructions?|prompts?|context|rules?|constraints?)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="disregard_instructions",
        regex=re.compile(
            r"\bdisregard\s+(the\s+)?"
            r"(instructions?|prompts?|rules?|context|above|previous|prior)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="forget_instructions",
        regex=re.compile(
            r"\bforget\s+(what|everything|all|the\s+above|your\s+instructions?)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="act_as",
        regex=re.compile(
            r"\bact\s+as\s+(if\s+)?(a\b|an\b|though\b|you\s+(are|were)\b)",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="pretend_to_be",
        regex=re.compile(
            r"\bpretend\s+(to\s+be|you\s+(are|were)|you'?re)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="you_are_now",
        regex=re.compile(
            r"\byou\s+(are|will\s+be)\s+now\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="system_override_tokens",
        # Matches model-specific injection delimiters used in LLM prompt formatting.
        regex=re.compile(
            r"(<\|im_start\|>|\[INST\]|<<SYS>>|<\|system\|>|<\|user\|>|<\|assistant\|>)",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="bypass_filter",
        regex=re.compile(
            r"\bbypass\s+(the\s+|your\s+|all\s+)?(filter|restriction|rule|safety|guardrail)s?\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
    Pattern(
        name="new_instructions",
        regex=re.compile(
            r"\bnew\s+instructions?\s*:|\boverride\s+(the\s+)?system\b"
            r"|\bsystem\s+prompt\s+override\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_INJECTION]",
    ),
]


# ─── Jailbreak ────────────────────────────────────────────────────────────────

JAILBREAK_PATTERNS: list[Pattern] = [
    Pattern(
        name="dan",
        regex=re.compile(
            r"\bDAN\b|\bdo\s+anything\s+now\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
    Pattern(
        name="developer_mode",
        regex=re.compile(
            r"\benable\s+(developer|dev)\s+mode\b"
            r"|\bdeveloper\s+mode\s+(enabled|activated|on)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
    Pattern(
        name="jailbreak",
        regex=re.compile(r"\bjailbreak\b", re.IGNORECASE),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
    Pattern(
        name="uncensored",
        regex=re.compile(
            r"\buncensored\b|\bunrestricted\s+(AI|model|mode|version)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
    Pattern(
        name="simulate_being",
        regex=re.compile(
            r"\bsimulate\s+(being|a|an)\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
    Pattern(
        name="no_restrictions",
        regex=re.compile(
            r"\byou\s+have\s+no\s+restrictions?\b"
            r"|\bwithout\s+any\s+limitations?\b"
            r"|\bno\s+rules\s+apply\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
    Pattern(
        name="base_model",
        regex=re.compile(
            r"\b(base|raw|unaligned)\s+model\b",
            re.IGNORECASE,
        ),
        redact_label="[BLOCKED_JAILBREAK]",
    ),
]
