"""Clearance-level generation policies.

Maps each clearance level (0–4) to Bedrock generation parameters and,
for the lowest level, a set of allowed topic keywords.

Topic restriction applies only to clearance 0 (Unclassified) — higher
clearance levels are unrestricted in topic but gain more tokens and higher
temperature, reflecting the wider scope of their permitted access.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClearancePolicy:
    max_tokens: int
    temperature: float
    top_p: float
    allowed_topics: frozenset[str]

    def is_topic_allowed(self, message: str) -> bool:
        """Return True if the message is on an allowed topic (case-insensitive).

        An empty allowed_topics set means all topics are permitted.
        """
        if not self.allowed_topics:
            return True
        msg_lower = message.lower()
        return any(topic in msg_lower for topic in self.allowed_topics)


_POLICIES: dict[int, ClearancePolicy] = {
    0: ClearancePolicy(
        max_tokens=256,
        temperature=0.3,
        top_p=0.70,
        # Unclassified: only general HR / company-policy questions are in scope.
        allowed_topics=frozenset({
            "policy", "policies", "procedure", "procedures",
            "guideline", "guidelines", "faq", "handbook",
            "onboarding", "company", "hr", "benefit", "benefits",
            "vacation", "leave", "training", "general", "office",
        }),
    ),
    1: ClearancePolicy(
        max_tokens=512,
        temperature=0.5,
        top_p=0.85,
        allowed_topics=frozenset(),
    ),
    2: ClearancePolicy(
        max_tokens=1024,
        temperature=0.7,
        top_p=0.90,
        allowed_topics=frozenset(),
    ),
    3: ClearancePolicy(
        max_tokens=2048,
        temperature=0.8,
        top_p=0.95,
        allowed_topics=frozenset(),
    ),
    4: ClearancePolicy(
        max_tokens=4096,
        temperature=0.9,
        top_p=0.99,
        allowed_topics=frozenset(),
    ),
}


def get_policy(clearance_level: int) -> ClearancePolicy:
    """Return the policy for the given clearance level (0–4).

    Falls back to clearance-2 (Restricted) if an unknown level is given —
    this should never happen since the handler validates clearance before calling.
    """
    return _POLICIES.get(clearance_level, _POLICIES[2])
