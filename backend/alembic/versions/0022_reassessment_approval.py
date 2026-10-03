"""Correct the 0021 backfill: only a previously approved assessment is
"under reassessment".

0021 marked a parent UNDER_REASSESSMENT whenever it had a reassessment
(child) without a final decision, without checking that the parent had ever
been approved. Historical data has reassessment records created on
never-approved assessments (before the reassessment rules existed), so those
parents were wrongly flagged.

Rule (R18.4): an assessment is UNDER_REASSESSMENT only when

  * it has a reassessment (child) without a final decision, and
  * it was approved before that reassessment began:
      (parent.committee_decision IN ('APPROVED', 'APPROVED_WITH_CONDITIONS')
       OR parent.status IN ('APPROVED', 'APPROVED_WITH_CONDITIONS'))
      AND parent.committee_decided_at IS NOT NULL
      AND parent.committee_decided_at <= child.created_at

`committee_decision` / `committee_decided_at` are the binding decision of
record; they stay set even when an approved assessment is later reopened
(amendment), so they -- not only the current pipeline status -- are the
evidence of a prior approval. An approved status is accepted too (older
rows may carry the status without the decision value), but the decision
time is always required: without it, "approved before the reassessment
began" can't be shown, and the flag is cleared.

What this migration does: for every assessment flagged UNDER_REASSESSMENT
that fails the rule, it clears the flag (NULL = "in force", the state every
other non-reassessed assessment has). Nothing else changes: no assessment or
reassessment row is added, deleted or otherwise modified; pipeline and
lifecycle statuses, SUPERSEDED parents, triggers and children are untouched.
It never sets a flag. Idempotent: a re-run finds nothing to clear.

Downgrade is a deliberate no-op: the 0021 schema accepts the corrected
state, and restoring the wrong flags would re-introduce the defect.

Revision ID: 0022_reassessment_approval
Revises: 0021_reassessment_lifecycle
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022_reassessment_approval"
down_revision: Union[str, None] = "0021_reassessment_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same final-decision list as 0021: a child in one of these has ended.
FINAL = ("APPROVED", "APPROVED_WITH_CONDITIONS", "REJECTED", "MANAGER_REJECTED", "CLOSED")
APPROVED = ("APPROVED", "APPROVED_WITH_CONDITIONS")

_final = ", ".join(f"'{s}'" for s in FINAL)
_approved = ", ".join(f"'{s}'" for s in APPROVED)

# Parents flagged UNDER_REASSESSMENT with no open reassessment that began
# after a recorded approval of the parent.
WRONGLY_FLAGGED = sa.text(
    "SELECT p.id FROM assessments p "
    "WHERE p.reassessment_state = 'UNDER_REASSESSMENT' AND NOT EXISTS ("
    "  SELECT 1 FROM assessments c "
    "  WHERE c.parent_assessment_id = p.id "
    f"   AND c.status NOT IN ({_final}) "
    f"   AND (p.committee_decision IN ({_approved}) OR p.status IN ({_approved})) "
    "    AND p.committee_decided_at IS NOT NULL "
    "    AND p.committee_decided_at <= c.created_at"
    ") ORDER BY p.id"
)


def upgrade() -> None:
    bind = op.get_bind()
    ids = [row[0] for row in bind.execute(WRONGLY_FLAGGED)]
    for assessment_id in ids:
        bind.execute(
            sa.text(
                "UPDATE assessments SET reassessment_state = NULL "
                "WHERE id = :id AND reassessment_state = 'UNDER_REASSESSMENT'"
            ),
            {"id": assessment_id},
        )


def downgrade() -> None:
    # No-op by design (see the module docstring).
    pass
