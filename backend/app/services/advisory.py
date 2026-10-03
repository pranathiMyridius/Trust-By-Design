"""
Stage 19 (Explainability): "No automated recommendation shall appear to
be a final approval or rejection."

Automated outputs (the AI/template draft recommendation, AI-identified
risk factors, challenge findings) are always labelled ADVISORY and carry
a notice pointing at who actually decides. Final decisions exist only as
the Manager's decision and the Risk Committee's decision/votes, recorded
against a named person.
"""

import re

ADVISORY_NOTICE = (
    "Automated output is a recommendation for human review. It is not an "
    "approval or a rejection. Final decisions are made and recorded only by "
    "the Manager and the Risk Committee."
)

ANALYST_NOTICE = (
    "Analyst recommendation. It is not a final decision; the Manager and the "
    "Risk Committee make and record the decision."
)

# Leading phrasing that would read as an outcome already decided, e.g.
# "Approved.", "Rejected -", "Decision: reject".
_DECISIVE_OPENING = re.compile(
    r"^\s*(?:final\s+)?(?:decision\s*[:\-]\s*)?(approved|rejected|approve|reject|declined|decline)\b[\s.:,\-]*",
    re.IGNORECASE,
)


def ensure_advisory_wording(text: str | None) -> str:
    """Rewrites a recommendation that opens like a verdict so it reads as
    a suggestion: "Approved. Low risk." -> "Suggested outcome: Approve.
    Low risk." Text already phrased as a suggestion is left alone."""

    if not text:
        return text or ""

    stripped = text.strip()
    if stripped.lower().startswith(("suggested outcome", "recommendation", "recommend")):
        return stripped

    match = _DECISIVE_OPENING.match(stripped)
    if not match:
        return stripped

    verb = match.group(1).lower()
    outcome = "Approve" if verb.startswith("approv") else "Reject"
    remainder = stripped[match.end():]
    return f"Suggested outcome: {outcome}." + (f" {remainder}" if remainder else "")


def recommendation_notice(is_edited: bool) -> str:
    return ANALYST_NOTICE if is_edited else ADVISORY_NOTICE
