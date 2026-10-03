"""
Stage 16 (R16.3): assembles a complete, exportable audit package for one
assessment.

Rather than hand-listing every table that might reference an assessment
(which would silently go stale as new stages add more child tables --
overrides, controls, challenge findings, action items, committee votes,
workflow assignment, ...), this walks every mapped SQLAlchemy model that
has an `assessment_id` column and includes every matching row, keyed by
table name. New tables are picked up automatically without editing this
file. Every row is included exactly as stored -- no filtering by
is_current/version -- so superseded versions remain part of the export
(R16.1: the full decision path, not just the latest state).
"""

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from app.database import Base
from app.models.assessment import Assessment

# Columns that name a user. Their users are listed in the package's
# "people" section (name, email, role -- never credentials) so the
# decision path can be read without a database.
_USER_ID_COLUMNS = {
    "actor_id",
    "owner_id",
    "manager_id",
    "manager_decided_by_id",
    "committee_decided_by_id",
    "committee_decided_on_behalf_of_id",
    "current_assignee_id",
    "escalated_to_id",
    "user_id",
    "author_id",
    "member_id",
    "delegate_id",
    "overridden_by_id",
    "closure_requested_by_id",
}


def _serialize_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _serialize_row(row) -> dict:
    return {column.name: _serialize_value(getattr(row, column.name)) for column in row.__table__.columns}


def _models_with_assessment_id() -> list[type]:
    models = []
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls is Assessment:
            continue
        if any(column.name == "assessment_id" for column in cls.__table__.columns):
            models.append(cls)
    return models


def build_audit_package(db: Session, assessment_id: int) -> dict | None:
    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        return None

    related: dict[str, list[dict]] = {}

    for model in _models_with_assessment_id():
        rows = db.query(model).filter(model.assessment_id == assessment_id).all()
        if not rows:
            continue
        table_name = model.__tablename__
        try:
            rows = sorted(rows, key=lambda row: getattr(row, "id", 0))
        except TypeError:
            pass
        related[table_name] = [_serialize_row(row) for row in rows]

    assessment_row = _serialize_row(assessment)

    package = {
        "assessment_id": assessment_id,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "assessment": assessment_row,
        "related": related,
        "configuration": _configuration(db, related),
        "people": _people(db, [assessment_row, *(row for rows in related.values() for row in rows)]),
        "explanation": _explanation(db, assessment_id),
    }

    # R16.3: lets a recipient check the file hasn't been altered since
    # export -- recompute the SHA-256 of the package without "integrity",
    # serialised with sorted keys and no extra whitespace.
    package["integrity"] = {
        "algorithm": "sha256",
        "canonicalisation": "json, sort_keys, separators=(',', ':'), integrity key removed",
        "digest": package_digest(package),
    }
    return package


def package_digest(package: dict) -> str:
    body = {key: value for key, value in package.items() if key != "integrity"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _configuration(db: Session, related: dict[str, list[dict]]) -> dict:
    """The configuration versions the calculations were made against
    (R16.1 "configuration versions"): each methodology a calculation
    used, each reference-data snapshot it consulted, and the challenge
    trigger configuration in force at export."""

    from app.models.challenge_review import ChallengeTriggerConfig
    from app.models.reference_data_snapshot import ReferenceDataSnapshot
    from app.models.risk_methodology import RiskMethodology

    calculations = related.get("inherent_risk_calculations", []) + related.get("residual_risk_calculations", [])

    methodology_ids = sorted({row["methodology_id"] for row in calculations if row.get("methodology_id")})
    methodologies = (
        db.query(RiskMethodology).filter(RiskMethodology.id.in_(methodology_ids)).all() if methodology_ids else []
    )

    snapshot_ids = set()
    for row in calculations:
        try:
            reference = json.loads(row.get("reference_data") or "{}")
        except (TypeError, ValueError):
            continue
        snapshot_ids.update(
            used["snapshot_id"] for used in reference.get("snapshots_used", []) if used.get("snapshot_id")
        )
    snapshots = (
        db.query(ReferenceDataSnapshot).filter(ReferenceDataSnapshot.id.in_(sorted(snapshot_ids))).all()
        if snapshot_ids
        else []
    )

    challenge = db.query(ChallengeTriggerConfig).filter(ChallengeTriggerConfig.is_active.is_(True)).first()

    return {
        "risk_methodologies": [_serialize_row(row) for row in sorted(methodologies, key=lambda r: r.id)],
        "reference_data_snapshots": [_serialize_row(row) for row in sorted(snapshots, key=lambda r: r.id)],
        "challenge_trigger_config_at_export": _serialize_row(challenge) if challenge else None,
    }


def _people(db: Session, rows: list[dict]) -> list[dict]:
    from app.models.user import User

    ids = {
        value
        for row in rows
        for column, value in row.items()
        if column in _USER_ID_COLUMNS and isinstance(value, int)
    }
    if not ids:
        return []
    users = db.query(User).filter(User.id.in_(sorted(ids))).order_by(User.id).all()
    return [
        {"id": user.id, "full_name": user.full_name, "email": user.email, "role": user.role, "is_active": user.is_active}
        for user in users
    ]


def _explanation(db: Session, assessment_id: int) -> dict | None:
    """R16.2: how each rating was determined, as of export."""

    from app.services.explainability import explain_assessment

    try:
        return explain_assessment(db, assessment_id)
    except Exception:  # noqa: BLE001 -- the export must not fail on this
        return None
