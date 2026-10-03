"""P6: reassessment lifecycle of the approved parent.

* assessments.reassessment_state (NULL = in force | UNDER_REASSESSMENT |
  SUPERSEDED), superseded_by_id, superseded_at. A flag, never a pipeline
  status: existing approved records keep their status.
* reassessment_triggers.detected_by_id, resolved_by_id, resolution_note.

Backfill (idempotent, derived only from existing rows):
* a parent whose latest reassessment is APPROVED / APPROVED_WITH_CONDITIONS
  -> SUPERSEDED by that reassessment (superseded_at = its decision time);
* otherwise a parent with a reassessment that has no final decision
  -> UNDER_REASSESSMENT.
Nothing else changes; pipeline statuses are untouched.

Downgrade refuses once triggers carry P6 identities or notes (recorded
after the migration), rather than lose them. The state columns are derived
and are dropped.

Revision ID: 0021_reassessment_lifecycle
Revises: 0020_evidence_traceability
Create Date: 2026-10-03
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021_reassessment_lifecycle"
down_revision: Union[str, None] = "0020_evidence_traceability"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FINAL = ("APPROVED", "APPROVED_WITH_CONDITIONS", "REJECTED", "MANAGER_REJECTED", "CLOSED")


def _added() -> dict[str, list[sa.Column]]:
    # Plain integers (no FK constraint): SQLite can't add one in place.
    return {
        "assessments": [
            sa.Column("reassessment_state", sa.String(length=30), nullable=True),
            sa.Column("superseded_by_id", sa.Integer(), nullable=True),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
        ],
        "reassessment_triggers": [
            sa.Column("detected_by_id", sa.Integer(), nullable=True),
            sa.Column("resolved_by_id", sa.Integer(), nullable=True),
            sa.Column("resolution_note", sa.Text(), nullable=True),
        ],
    }


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in _added().items():
        existing = _columns(bind, table)
        for column in columns:
            if column.name not in existing:
                op.add_column(table, column)
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("assessments")}
    if "ix_assessments_reassessment_state" not in indexes:
        op.create_index("ix_assessments_reassessment_state", "assessments", ["reassessment_state"])

    final = ", ".join(f"'{s}'" for s in FINAL)
    rows = bind.execute(sa.text(
        "SELECT c.parent_assessment_id, c.id, c.status, COALESCE(c.committee_decided_at, c.manager_decided_at) "
        "FROM assessments c WHERE c.parent_assessment_id IS NOT NULL ORDER BY c.parent_assessment_id, c.id"
    )).fetchall()
    by_parent: dict[int, list] = {}
    for parent_id, child_id, status, decided_at in rows:
        by_parent.setdefault(parent_id, []).append((child_id, status, decided_at))
    for parent_id, children in by_parent.items():
        current = bind.execute(sa.text("SELECT reassessment_state FROM assessments WHERE id = :p"), {"p": parent_id}).scalar()
        if current is not None:
            continue  # already set (re-run)
        approved = [c for c in children if c[1] in ("APPROVED", "APPROVED_WITH_CONDITIONS")]
        if approved:
            child_id, _, decided_at = approved[-1]
            bind.execute(sa.text(
                "UPDATE assessments SET reassessment_state = 'SUPERSEDED', superseded_by_id = :c, superseded_at = :t WHERE id = :p"
            ), {"c": child_id, "t": decided_at, "p": parent_id})
        elif any(c[1] not in FINAL for c in children):
            bind.execute(sa.text("UPDATE assessments SET reassessment_state = 'UNDER_REASSESSMENT' WHERE id = :p"), {"p": parent_id})


def downgrade() -> None:
    bind = op.get_bind()
    if "resolution_note" in _columns(bind, "reassessment_triggers"):
        count = bind.execute(sa.text(
            "SELECT COUNT(*) FROM reassessment_triggers WHERE detected_by_id IS NOT NULL OR resolved_by_id IS NOT NULL "
            "OR resolution_note IS NOT NULL"
        )).scalar()
        if count:
            raise RuntimeError(f"{count} reassessment trigger(s) carry P6 identities or notes; downgrading 0021 would discard them.")
    indexes = {i["name"] for i in sa.inspect(bind).get_indexes("assessments")}
    if "ix_assessments_reassessment_state" in indexes:
        op.drop_index("ix_assessments_reassessment_state", table_name="assessments")
    for table, columns in _added().items():
        existing = _columns(bind, table)
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                if column.name in existing:
                    batch.drop_column(column.name)
