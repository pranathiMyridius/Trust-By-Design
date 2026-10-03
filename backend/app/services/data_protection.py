"""
Stage 19 (Availability and Recovery): protection against accidental
deletion and corruption at the ORM level.

  * Records of record -- assessments, their documents, risk results,
    audit history, workflow transitions, committee decisions -- can't be
    hard-deleted through the application at all, whether via
    session.delete() or a bulk query(...).delete(). Removal from normal
    views is done with the existing controlled soft delete (Stage 16,
    app/api/audit_trail.py), which keeps every row.
  * Audit events and control revisions are append-only: an attempt to
    modify an existing row is rejected, so history can't be silently
    rewritten.
  * Governance records that are versioned by superseding (committee
    votes, challenge-review sign-offs) and the override ledger are
    write-once: after insert, only the fields that retire a row or record
    its review may change, and only once (SUPERSEDE_ONLY_FIELDS).

A genuine, deliberate hard delete (e.g. a data-subject erasure run by a
DBA) must be done outside the application, where it's governed by the
database's own access controls and backups.

Derived data that is safe to rebuild (e.g. document_embeddings) is not
protected.
"""

from sqlalchemy import event
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

PROTECTED_TABLES = {
    "assessments",
    "assessment_documents",
    "audit_events",
    "risk_results",
    "risk_factors",
    "inherent_risk_calculations",
    "workflow_transitions",
    "committee_votes",
    "committee_conditions",
    "assessment_overrides",
    "assessment_retention",
    "action_items",
    "controls",
    "control_revisions",
    "challenge_review_signoffs",
    "sod_exceptions",
    "sod_exception_events",
    "retention_policy_versions",
    "legal_hold_events",
    "intake_snapshots",
    "evidence_acknowledgements",
}

APPEND_ONLY_TABLES = {
    "audit_events",
    "control_revisions",
    "sod_exception_events",
    "legal_hold_events",
    "intake_snapshots",
    "evidence_acknowledgements",
}

# table -> the only columns that may change after insert. Each may be set
# once (from NULL), except is_current, which may only go from true to
# false, and review_status, which may only leave PROPOSED.
SUPERSEDE_ONLY_FIELDS = {
    "committee_votes": {"is_current", "superseded_at", "superseded_by_id"},
    "challenge_review_signoffs": {"is_current", "superseded_at", "superseded_reason"},
    "assessment_overrides": {
        "review_status", "reviewed_by", "reviewed_by_id", "reviewed_at", "review_note",
        "approval_status", "approved_by", "approved_by_id", "approved_at", "approval_rationale",
    },
}

# Allowed moves for the status-like columns above (anything else is refused).
_STATUS_MOVES = {
    # PROPOSED or (P3) an APPLIED material change is reviewed once.
    "review_status": {("PROPOSED", "CONFIRMED"), ("PROPOSED", "REJECTED"), ("APPLIED", "CONFIRMED"), ("APPLIED", "REJECTED")},
    "approval_status": {
        (None, "NOT_REQUIRED"), (None, "PENDING"),
        ("PENDING", "APPROVED"), ("PENDING", "REJECTED"),
    },
}

# P3: what an SoD exception request says can't change once it has left
# DRAFT -- what was approved is what applies.
SOD_LOCKED_FIELDS = {
    "exception_type", "requestor_id", "affected_user_id", "assessment_id",
    "business_justification", "standard_workflow_reason", "risk_level",
    "compensating_controls", "start_at", "end_at", "assigned_approver_id",
    "decision", "decided_by_id", "decided_at", "decision_rationale",
}


# P5: a retention policy version is never edited in place. Only these
# fields may change after insert, each once (from NULL), and status only
# along these moves; row_version is the optimistic lock.
RETENTION_VERSION_MUTABLE = {
    "status", "decided_by_id", "decided_at", "decision_reason", "effective_from",
    "superseded_at", "superseded_by_id", "row_version",
}
_RETENTION_STATUS_MOVES = {("PROPOSED", "ACTIVE"), ("PROPOSED", "REJECTED"), ("ACTIVE", "SUPERSEDED")}


# P6: a superseded approval stays superseded, by the same successor.
_REASSESSMENT_STATE_MOVES = {
    (None, "UNDER_REASSESSMENT"), ("UNDER_REASSESSMENT", None), ("UNDER_REASSESSMENT", "SUPERSEDED"), (None, "SUPERSEDED"),
}


class ProtectedDataError(RuntimeError):
    pass


def _table_name(obj) -> str | None:
    table = getattr(obj, "__table__", None)
    return getattr(table, "name", None)


def _before_flush(session: Session, flush_context, instances) -> None:
    for obj in session.deleted:
        name = _table_name(obj)
        if name in PROTECTED_TABLES:
            raise ProtectedDataError(
                f"Rows in '{name}' cannot be deleted by the application. Use the "
                "controlled soft-delete process instead."
            )

    for obj in session.dirty:
        name = _table_name(obj)
        if name in APPEND_ONLY_TABLES and session.is_modified(obj, include_collections=False):
            raise ProtectedDataError(
                f"'{name}' is append-only; existing entries cannot be modified."
            )
        if name in SUPERSEDE_ONLY_FIELDS:
            _check_supersede_only(name, obj)
        if name == "sod_exceptions":
            _check_sod_exception(obj)
        if name == "retention_policy_versions":
            _check_retention_version(obj)
        if name == "assessments":
            _check_reassessment_flags(obj)


def _check_reassessment_flags(obj) -> None:
    state = sa_inspect(obj)
    history = state.attrs["reassessment_state"].history
    if history.has_changes():
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if (before, after) not in _REASSESSMENT_STATE_MOVES and before != after:
            raise ProtectedDataError(f"An assessment's reassessment state cannot move from {before} to {after}.")
    for key in ("superseded_by_id", "superseded_at"):
        history = state.attrs[key].history
        if history.has_changes() and history.deleted and history.deleted[0] is not None:
            raise ProtectedDataError(f"'assessments.{key}' is write-once and is already set.")


def _check_retention_version(obj) -> None:
    state = sa_inspect(obj)
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if attr.key not in RETENTION_VERSION_MUTABLE:
            raise ProtectedDataError(
                f"Retention policy versions are immutable; '{attr.key}' cannot be changed. Propose a new version instead."
            )
        if attr.key == "status":
            if (before, after) not in _RETENTION_STATUS_MOVES:
                raise ProtectedDataError(f"A retention policy version cannot move from {before} to {after}.")
        elif attr.key != "row_version" and before is not None:
            raise ProtectedDataError(f"'retention_policy_versions.{attr.key}' is write-once and is already set.")


def _check_sod_exception(obj) -> None:
    state = sa_inspect(obj)
    status_history = state.attrs["status"].history
    previous_status = status_history.deleted[0] if status_history.deleted else obj.status
    for key in SOD_LOCKED_FIELDS:
        history = state.attrs[key].history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        # The decision is recorded exactly once, on leaving PENDING_APPROVAL.
        if key in {"decision", "decided_by_id", "decided_at", "decision_rationale"} and before is None and previous_status == "PENDING_APPROVAL":
            continue
        if previous_status != "DRAFT":
            raise ProtectedDataError(
                f"SoD exception field '{key}' cannot change once the request has left DRAFT."
            )


def _check_supersede_only(name: str, obj) -> None:
    allowed = SUPERSEDE_ONLY_FIELDS[name]
    state = sa_inspect(obj)
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        before = history.deleted[0] if history.deleted else None
        after = history.added[0] if history.added else None
        if attr.key not in allowed:
            raise ProtectedDataError(
                f"'{name}' records are immutable; '{attr.key}' cannot be changed. "
                "Record a new version instead."
            )
        if attr.key == "is_current":
            if not (before is True and after is False):
                raise ProtectedDataError(f"A superseded '{name}' record cannot be made current again.")
        elif attr.key in _STATUS_MOVES:
            if (before, after) not in _STATUS_MOVES[attr.key]:
                raise ProtectedDataError(f"'{name}.{attr.key}' cannot move from {before} to {after}.")
        elif before is not None:
            raise ProtectedDataError(f"'{name}.{attr.key}' is write-once and is already set.")


def _do_orm_execute(orm_execute_state) -> None:
    if not (orm_execute_state.is_delete or orm_execute_state.is_update):
        return

    mapper = orm_execute_state.bind_mapper
    name = getattr(getattr(mapper, "local_table", None), "name", None) if mapper else None

    if orm_execute_state.is_delete and name in PROTECTED_TABLES:
        raise ProtectedDataError(
            f"Bulk delete on '{name}' is blocked. Use the controlled soft-delete process."
        )
    if orm_execute_state.is_update and (
        name in APPEND_ONLY_TABLES or name in SUPERSEDE_ONLY_FIELDS or name == "retention_policy_versions"
    ):
        raise ProtectedDataError(f"'{name}' is protected; bulk updates are blocked.")


_installed = False


def install_data_protection() -> None:
    global _installed
    if _installed:
        return
    event.listen(Session, "before_flush", _before_flush)
    event.listen(Session, "do_orm_execute", _do_orm_execute)
    _installed = True
