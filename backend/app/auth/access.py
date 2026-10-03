"""
Stage 19 (Security -- least privilege): one access gate applied to every
/api router in app/main.py, so no endpoint can be reached anonymously by
omission. Before this, ~40 read endpoints (documents, audit history,
intelligence, risk results, the raw document file download, ...) took no
current_user at all.

On top of "must be signed in", any route carrying an {assessment_id} or
{document_id} path parameter is checked against the same per-role
visibility rules the assessment list uses (_scope_assessments_for_user),
so a Business User can't read another team's assessment just by guessing
its id. Role checks on individual endpoints (require_role /
require_pipeline_role) still apply on top of this.
"""

import re

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, log_denied_attempt
from app.database import get_db
from app.models.user import READ_ONLY_ROLES, User, UserRole

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# R16.4: who can still open a soft-deleted assessment (by id) -- the
# people who administer retention and those who audit it. Nobody can
# change one.
DELETED_ASSESSMENT_READERS = {UserRole.ADMIN.value, UserRole.AUDITOR.value}


def _parse_id(raw) -> int | None:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def ensure_assessment_visible(
    db: Session,
    assessment_id: int,
    user: User,
    request: Request | None = None,
) -> None:
    # Lazy import: app.api.assessments imports half the app, and this
    # module is imported by app.main before the routers.
    from app.api.assessments import _scope_assessments_for_user
    from app.models.assessment import Assessment

    query = db.query(Assessment.id).filter(Assessment.id == assessment_id)

    if query.first() is None:
        # Let the endpoint produce its own 404 (some return richer detail).
        return

    from app.models.audit_trail import is_soft_deleted

    if is_soft_deleted(db, assessment_id) and user.role not in DELETED_ASSESSMENT_READERS:
        # Indistinguishable from an id that never existed.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assessment not found")

    if _scope_assessments_for_user(query, user).first() is None:
        if request is not None:
            log_denied_attempt(
                db, user, request, "assessment outside the user's visibility or scope", assessment_id=assessment_id
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_delegation_explanation(db, assessment_id, user)
            or "You do not have access to this assessment.",
        )


def _delegation_explanation(db: Session, assessment_id: int, user: User) -> str | None:
    """
    AW.7: if the user once had delegated access to this assessment but
    the delegation is not in force now (not started, expired, revoked),
    say so instead of a bare "no access".
    """

    from app.models.assessment import Assessment
    from app.services import delegation as delegation_rules

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    for authority in delegation_rules.AUTHORITIES:
        reason = delegation_rules.explain_missing_delegation(db, user, assessment, authority)
        if reason:
            return reason
    return None


def enforce_api_access(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    request.state.user = current_user

    # R15.1: an Auditor or Read-only Executive can look at everything they
    # can see, but change nothing.
    if current_user.role in READ_ONLY_ROLES and request.method not in SAFE_METHODS:
        log_denied_attempt(db, current_user, request, "read-only role")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your role has read-only access and cannot make changes.",
        )

    assessment_id = _parse_id(request.path_params.get("assessment_id"))
    if assessment_id is not None:
        ensure_assessment_visible(db, assessment_id, current_user, request)
        _refuse_change_to_deleted(db, assessment_id, current_user, request)

    _refuse_same_case_admin_action(db, current_user, request, assessment_id)

    document_id = _parse_id(request.path_params.get("document_id"))
    if document_id is not None:
        from app.models.assessment_document import AssessmentDocument

        owner = (
            db.query(AssessmentDocument.assessment_id)
            .filter(AssessmentDocument.id == document_id)
            .first()
        )
        if owner is not None:
            ensure_assessment_visible(db, owner[0], current_user, request)
            _refuse_change_to_deleted(db, owner[0], current_user, request)

    return current_user


def _refuse_change_to_deleted(db: Session, assessment_id: int, user: User, request: Request) -> None:
    """R16.4: a soft-deleted assessment is kept for the record only; no
    request may change it."""

    if request.method in SAFE_METHODS:
        return

    from app.models.audit_trail import is_soft_deleted

    if is_soft_deleted(db, assessment_id):
        log_denied_attempt(
            db, user, request, "assessment is soft-deleted under the retention policy", assessment_id=assessment_id
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This assessment has been deleted under the retention policy and can no longer be changed.",
        )


# P3 (R-GOV-02): an Admin acting as a committee member (approved dual-role
# SoD exception) may not also perform privileged actions that could
# influence the case; another administrator must. Global configuration
# affects every case, so it is refused while any dual-role exception is
# in force; case-level administration is refused for the covered case.
_GLOBAL_ADMIN_PATHS = re.compile(
    r"^/api/(users|risk-methodologies|challenge-triggers|reference-data|retention-policy|retention/policies|sod-exceptions/\d+/(decision|revoke))(/|$)"
)
_CASE_ADMIN_PATHS = re.compile(
    r"^/api/assessments/\d+/(workflow/assign|workflow/target-date|retention|soft-delete|documents|amend)(/|$)"
)


def _refuse_same_case_admin_action(db: Session, user: User, request: Request, assessment_id: int | None) -> None:
    if request.method in SAFE_METHODS or user.role != UserRole.ADMIN.value:
        return
    path = request.url.path
    is_global = bool(_GLOBAL_ADMIN_PATHS.match(path))
    is_case = bool(_CASE_ADMIN_PATHS.match(path))
    if not (is_global or is_case):
        return

    from app.governance.sod import active_dual_role_exceptions

    held = active_dual_role_exceptions(db, user)
    if not held:
        return
    covering = [e for e in held if e.assessment_id is None or e.assessment_id == assessment_id]
    if is_global or covering:
        refs = ", ".join(e.reference or str(e.id) for e in (held if is_global else covering))
        log_denied_attempt(
            db, user, request,
            f"same-case administrative action while holding dual-role SoD exception(s) {refs}",
            assessment_id=assessment_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "You hold an active dual-role (Admin + Committee) exception "
                f"({refs}); another administrator must perform this action."
            ),
        )
