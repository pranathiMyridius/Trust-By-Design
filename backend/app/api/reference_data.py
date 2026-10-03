"""
Reference-data snapshots (FATF, EU high-risk third countries): what is
loaded, which snapshot is current, and reviewer attestation.

Only an attested snapshot feeds scoring rules (see
app/services/country_risk_service.py::scoring_jurisdiction_context). A
snapshot file's own "verified": true is recorded but never trusted:
attestation must come from a named reviewer who checked it against the
primary publication.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from app.database import get_db
from app.models.reference_data_snapshot import ReferenceDataSnapshot
from app.models.user import User, UserRole
from app.services.audit_service import AuditAction, log_audit_event
from app.services.country_risk_service import ALL_SNAPSHOTS, load_snapshot_data

router = APIRouter(prefix="/api/reference-data", tags=["Reference Data"])


def _response(snapshot: ReferenceDataSnapshot) -> dict:
    return {
        "id": snapshot.id,
        "source": snapshot.source,
        "as_of": snapshot.as_of,
        "source_url": snapshot.source_url,
        "statement": snapshot.statement,
        "checksum": snapshot.checksum,
        "entry_count": snapshot.entry_count,
        "source_claims_verified": snapshot.source_claims_verified,
        "source_verification_note": snapshot.source_verification_note,
        "attested": snapshot.attested,
        "attested_by": snapshot.attested_by,
        "attested_at": snapshot.attested_at,
        "attestation_note": snapshot.attestation_note,
        "used_in_scoring": snapshot.is_current and snapshot.attested,
        "loaded_by": snapshot.loaded_by,
        "loaded_at": snapshot.loaded_at,
        "is_current": snapshot.is_current,
        "superseded_at": snapshot.superseded_at,
    }


class AttestationRequest(BaseModel):
    # What the reviewer checked, against which primary publication.
    note: str

    @field_validator("note")
    @classmethod
    def note_required(cls, value: str) -> str:
        if not value or len(value.strip()) < 20:
            raise ValueError(
                "Say what was checked and against which primary source "
                "(at least 20 characters)."
            )
        return value.strip()


@router.get("/snapshots")
def list_snapshots(
    include_history: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ReferenceDataSnapshot)
    if not include_history:
        query = query.filter(ReferenceDataSnapshot.is_current.is_(True))
    return [
        _response(snapshot)
        for snapshot in query.order_by(
            ReferenceDataSnapshot.source, ReferenceDataSnapshot.loaded_at.desc()
        ).all()
    ]


@router.post("/snapshots/{snapshot_id}/attest")
def attest_snapshot(
    snapshot_id: int,
    payload: AttestationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    snapshot = db.get(ReferenceDataSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    if not snapshot.is_current:
        raise HTTPException(
            status_code=400,
            detail="Only the current snapshot for a source can be attested.",
        )
    if snapshot.attested:
        raise HTTPException(
            status_code=409,
            detail=f"Already attested by {snapshot.attested_by} on {snapshot.attested_at}.",
        )

    actor = current_user.full_name or current_user.email
    snapshot.attested_by = actor
    snapshot.attested_at = datetime.now(timezone.utc)
    snapshot.attestation_note = payload.note

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.REFERENCE_DATA_CHANGED,
        actor=actor,
        actor_id=current_user.id,
        details=(
            f"{snapshot.source} snapshot as of {snapshot.as_of} ({snapshot.checksum[:19]}...) "
            f"attested by {actor}; it now feeds scoring rules. Note: {payload.note}"
        ),
    )
    db.commit()
    return _response(snapshot)


@router.post("/reload")
def reload_bundled_snapshots(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Loads the snapshot files bundled under app/reference_data/. Unchanged
    content is a no-op (attestation kept); changed content becomes a new,
    unattested snapshot that scoring ignores until someone attests it.
    """

    import json

    actor = current_user.full_name or current_user.email
    results = []
    for path in ALL_SNAPSHOTS:
        with open(path, encoding="utf-8") as handle:
            results.append(load_snapshot_data(db, json.load(handle), loaded_by=actor))

    changed = [r for r in results if not r["unchanged"]]
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.REFERENCE_DATA_CHANGED,
        actor=actor,
        actor_id=current_user.id,
        details=(
            "Reference data reloaded: "
            + (
                "; ".join(
                    f"{r['source']} as of {r['as_of']} loaded as new unattested snapshot {r['snapshot_id']}"
                    for r in changed
                )
                or "no changes"
            )
            + "."
        ),
    )
    db.commit()
    return results
