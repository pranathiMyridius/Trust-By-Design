"""
Stage 19 (Security): data masking.

Two places mask sensitive values rather than showing/sending them raw:

1. Outbound AI calls (app/ai/metering.py). Document text and intake
   answers are sent to an external LLM provider; card numbers, IBANs,
   e-mail addresses, phone numbers and national-id-like numbers are
   replaced with typed placeholders first. On by default; set
   AI_MASK_SENSITIVE_DATA=false to disable.

2. Document text shown to users who don't need the raw content. A
   document classified CONFIDENTIAL or RESTRICTED has its extracted text
   masked for roles outside UNMASKED_ROLES other than the assessment's
   owner, and the raw file of either can't be opened by them (see
   should_mask_document and can_open_original_file). Evidence quotes
   taken from such a document are masked the same way.
   The stored text and the original file are never altered.

Masking is pattern based -- it reduces exposure, it is not a guarantee
that no personal data can ever appear.
"""

import os
import re

from app.models.user import UserRole

MASKED_CONFIDENTIALITY = {"CONFIDENTIAL", "RESTRICTED"}

# Roles that review evidence as part of their job and so see it unmasked.
UNMASKED_ROLES = {
    UserRole.FCRM_ANALYST.value,
    UserRole.ADMIN.value,
}

_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){2,7}(?:[ ]?[A-Z0-9]{1,4})?\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE = re.compile(r"(?<![\w])\+\d{1,3}[ .-]?\(?\d{1,4}\)?(?:[ .-]?\d{2,4}){2,4}(?![\w])")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _mask_card(match: re.Match) -> str:
    raw = match.group(0)
    digits = re.sub(r"\D", "", raw)
    if 13 <= len(digits) <= 19 and _luhn_valid(digits):
        return f"[CARD ****{digits[-4:]}]"
    return raw


def mask_sensitive_text(text: str | None) -> str | None:
    if not text:
        return text

    masked = _CARD_CANDIDATE.sub(_mask_card, text)
    masked = _IBAN.sub(lambda m: f"[IBAN ****{m.group(0).replace(' ', '')[-4:]}]", masked)
    masked = _EMAIL.sub("[EMAIL]", masked)
    masked = _SSN.sub("[NATIONAL ID]", masked)
    masked = _PHONE.sub("[PHONE]", masked)
    return masked


def ai_masking_enabled() -> bool:
    return os.getenv("AI_MASK_SENSITIVE_DATA", "true").strip().lower() not in {"0", "false", "no", "off"}


def mask_ai_payload(payload: dict) -> dict:
    """Masks every string message content in an OpenAI-style chat payload
    (and `input` for embeddings) -- the only fields that carry user data."""

    if not ai_masking_enabled():
        return payload

    masked = dict(payload)

    messages = masked.get("messages")
    if isinstance(messages, list):
        new_messages = []
        for message in messages:
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                message = {**message, "content": mask_sensitive_text(message["content"])}
            new_messages.append(message)
        masked["messages"] = new_messages

    value = masked.get("input")
    if isinstance(value, str):
        masked["input"] = mask_sensitive_text(value)
    elif isinstance(value, list):
        masked["input"] = [mask_sensitive_text(v) if isinstance(v, str) else v for v in value]

    return masked


def should_mask_document(confidentiality: str | None, user, owner_id: int | None = None) -> bool:
    """True when `user` should only see this document in masked form: it
    is CONFIDENTIAL/RESTRICTED, the user isn't a reviewing role, and they
    don't own the assessment it was uploaded to."""

    if (confidentiality or "").upper() not in MASKED_CONFIDENTIALITY:
        return False
    if owner_id is not None and getattr(user, "id", None) == owner_id:
        return False
    return getattr(user, "role", None) not in UNMASKED_ROLES


def can_open_original_file(confidentiality: str | None, user, owner_id: int | None = None) -> bool:
    """R15.3: the original file of a CONFIDENTIAL or RESTRICTED document is
    served only to someone who may also read its text unmasked. Serving it
    to anyone else would undo the masking, since the file holds the same
    content in full."""

    return not should_mask_document(confidentiality, user, owner_id)


def mask_evidence_records(records: list[dict], masked_document_ids: set[int]) -> list[dict]:
    """Masks the verbatim quotes in risk-factor evidence records that came
    from a document the viewer only sees masked. The stored records are
    never altered; the returned copies carry `is_masked`."""

    if not masked_document_ids:
        return records
    masked = []
    for record in records:
        if isinstance(record, dict) and record.get("document_id") in masked_document_ids:
            record = {**record, "verbatim_quote": mask_sensitive_text(record.get("verbatim_quote")), "is_masked": True}
        masked.append(record)
    return masked
