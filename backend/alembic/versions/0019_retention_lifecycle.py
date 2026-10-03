"""P5: retention policy versions and legal-hold history.

* retention_policy_versions (new, append-only): one row per version of a
  record type's retention period. At most one ACTIVE and one PROPOSED
  version per record type (partial unique indexes, SQLite and Postgres).
* legal_hold_events (new, append-only): SET / RELEASED history.
* assessment_retention.retention_policy_version_id (the version a soft
  delete was made under) and legal_hold_set_by_id (who set the current
  hold), both nullable.

Backfill (idempotent):
* ASSESSMENT v1 ACTIVE from the active retention_policies row (or the
  provisional 2,555-day default if there is none), change_reason
  "Migrated existing default; not compliance-approved", system_seeded.
* For each assessment currently on hold, one SET event from the existing
  columns, reason "Backfilled from pre-P5 state: <original reason>",
  system_seeded.

retention_policies is kept (read-through, no longer edited).

Downgrade refuses while any lifecycle history beyond the backfill exists
(a proposed / decided version, a hold event, or a soft delete that recorded
its policy version), rather than lose it.

Revision ID: 0019_retention_lifecycle
Revises: 0018_sod_governance
Create Date: 2026-10-02
"""
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019_retention_lifecycle"
down_revision: Union[str, None] = "0018_sod_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POLICY_STATUS = "PROVISIONAL_PENDING_GOVERNANCE_APPROVAL"
SEED_REASON = "Migrated existing default; not compliance-approved"
DEFAULT_DAYS = 2555

_PARTIAL = {
    "uq_retention_policy_one_active": "status = 'ACTIVE'",
    "uq_retention_policy_one_proposed": "status = 'PROPOSED'",
}


def _added() -> list[sa.Column]:
    # Plain integers (no FK constraint): SQLite can't add one in place.
    return [
        sa.Column("legal_hold_set_by_id", sa.Integer(), nullable=True),
        sa.Column("retention_policy_version_id", sa.Integer(), nullable=True),
    ]


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("retention_policy_versions"):
        op.create_table(
            "retention_policy_versions",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("record_type", sa.String(length=40), nullable=False, index=True),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("retention_days", sa.Integer(), nullable=False),
            sa.Column("basis", sa.String(length=40), nullable=False),
            sa.Column("effective_from", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, index=True),
            sa.Column("policy_status", sa.String(length=60), nullable=False),
            sa.Column("change_reason", sa.Text(), nullable=False),
            sa.Column("proposed_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("proposed_at", sa.DateTime(), nullable=False),
            sa.Column("decided_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("decided_at", sa.DateTime(), nullable=True),
            sa.Column("decision_reason", sa.Text(), nullable=True),
            sa.Column("supersedes_id", sa.Integer(), sa.ForeignKey("retention_policy_versions.id"), nullable=True),
            sa.Column("previous_retention_days", sa.Integer(), nullable=True),
            sa.Column("superseded_at", sa.DateTime(), nullable=True),
            sa.Column("superseded_by_id", sa.Integer(), sa.ForeignKey("retention_policy_versions.id"), nullable=True),
            sa.Column("system_seeded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("governance_approval_reference", sa.String(length=100), nullable=True),
            sa.Column("governance_approved_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("row_version", sa.Integer(), nullable=False, server_default="1"),
            sa.UniqueConstraint("record_type", "version", name="uq_retention_policy_version"),
        )
    existing_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("retention_policy_versions")}
    for name, where in _PARTIAL.items():
        if name not in existing_indexes:
            op.create_index(
                name, "retention_policy_versions", ["record_type"], unique=True,
                sqlite_where=sa.text(where), postgresql_where=sa.text(where),
            )

    if not inspector.has_table("legal_hold_events"):
        op.create_table(
            "legal_hold_events",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column("assessment_id", sa.Integer(), sa.ForeignKey("assessments.id"), nullable=False, index=True),
            sa.Column("action", sa.String(length=20), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("matter_reference", sa.String(length=100), nullable=True),
            sa.Column("actor_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("actor_name", sa.String(length=255), nullable=False),
            sa.Column("system_seeded", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )

    existing = _columns(bind, "assessment_retention")
    for column in _added():
        if column.name not in existing:
            op.add_column("assessment_retention", column)

    _backfill(bind)


def _backfill(bind) -> None:
    now = _now()
    has_assessment_version = bind.execute(
        sa.text("SELECT COUNT(*) FROM retention_policy_versions WHERE record_type = 'ASSESSMENT'")
    ).scalar()
    if not has_assessment_version:
        legacy = None
        if sa.inspect(bind).has_table("retention_policies"):
            legacy = bind.execute(
                sa.text(
                    "SELECT default_retention_days, created_at FROM retention_policies "
                    "WHERE is_active = :yes ORDER BY id LIMIT 1"
                ),
                {"yes": True},
            ).first()
        bind.execute(
            sa.text(
                "INSERT INTO retention_policy_versions (record_type, version, retention_days, basis, effective_from, "
                "status, policy_status, change_reason, proposed_at, decided_at, decision_reason, system_seeded, "
                "created_at, row_version) VALUES ('ASSESSMENT', 1, :days, 'FINAL_DECISION_DATE', :effective, 'ACTIVE', "
                ":policy_status, :reason, :now, :now, :reason, :yes, :now, 1)"
            ),
            {
                "days": legacy[0] if legacy else DEFAULT_DAYS,
                "effective": (legacy[1] if legacy and legacy[1] else now),
                "policy_status": POLICY_STATUS,
                "reason": SEED_REASON,
                "now": now,
                "yes": True,
            },
        )

    held = bind.execute(
        sa.text(
            "SELECT r.assessment_id, r.legal_hold_reason, r.legal_hold_set_by, r.legal_hold_set_at "
            "FROM assessment_retention r WHERE r.legal_hold = :yes AND NOT EXISTS "
            "(SELECT 1 FROM legal_hold_events e WHERE e.assessment_id = r.assessment_id)"
        ),
        {"yes": True},
    ).fetchall()
    for assessment_id, reason, set_by, set_at in held:
        bind.execute(
            sa.text(
                "INSERT INTO legal_hold_events (assessment_id, action, reason, actor_name, system_seeded, created_at) "
                "VALUES (:aid, 'SET', :reason, :actor, :yes, :at)"
            ),
            {
                "aid": assessment_id,
                "reason": "Backfilled from pre-P5 state: " + (reason or "(no reason recorded)"),
                "actor": set_by or "Unknown (set before P5)",
                "yes": True,
                "at": set_at or now,
            },
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    checks = []
    if inspector.has_table("retention_policy_versions"):
        checks.append(
            ("retention policy version(s) proposed or decided after the migration",
             "SELECT COUNT(*) FROM retention_policy_versions WHERE system_seeded = :no")
        )
    if inspector.has_table("legal_hold_events"):
        checks.append(("legal hold event(s) recorded after the migration", "SELECT COUNT(*) FROM legal_hold_events WHERE system_seeded = :no"))
    if "retention_policy_version_id" in _columns(bind, "assessment_retention"):
        checks.append(
            ("soft delete(s) that recorded their retention policy version",
             "SELECT COUNT(*) FROM assessment_retention WHERE retention_policy_version_id IS NOT NULL")
        )
    for what, sql in checks:
        count = bind.execute(sa.text(sql), {"no": False}).scalar()
        if count:
            raise RuntimeError(f"{count} {what} exist; downgrading 0019 would discard retention lifecycle history.")

    # Columns first: one may reference retention_policy_versions.
    existing = _columns(bind, "assessment_retention")
    with op.batch_alter_table("assessment_retention") as batch:
        for column in reversed(_added()):
            if column.name in existing:
                batch.drop_column(column.name)
    for table in ("legal_hold_events", "retention_policy_versions"):
        if inspector.has_table(table):
            op.drop_table(table)
