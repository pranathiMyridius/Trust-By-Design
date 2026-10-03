import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from app.database import get_db
from app.models.user import User, UserRole
from app.models.risk_methodology import RiskMethodology
from app.risk_engine.methodology import config_from_row, get_methodology_config
from app.risk_engine.scoring import normalize_rule, validate_residual_grid, validate_rule
from app.schemas.risk_methodology import (
    RiskMethodologyCreate,
    RiskMethodologyResponse,
    RiskMethodologyUpdate,
)
from app.services.audit_service import AuditAction, log_audit_event

router = APIRouter(
    prefix="/api/risk-methodologies",
    tags=["Risk Methodology"],
)


def _to_response(methodology: RiskMethodology) -> dict:
    return {
        "id": methodology.id,
        "name": methodology.name,
        "is_active": methodology.is_active,
        "weights": json.loads(methodology.weights),
        "thresholds": json.loads(methodology.thresholds),
        "created_at": methodology.created_at,
        "updated_at": methodology.updated_at,
        "version": methodology.version or 1,
        "parent_id": methodology.parent_id,
        "change_reason": methodology.change_reason,
        "locked": methodology.locked_at is not None,
        "locked_at": methodology.locked_at,
        "approved_by": methodology.approved_by,
        "approved_at": methodology.approved_at,
        "approval_reason": methodology.approval_reason,
        "effective_from": methodology.effective_from,
        "retired_at": methodology.retired_at,
        "fingerprint": config_from_row(methodology)["methodology_fingerprint"],
    }


def _get_or_404(db: Session, methodology_id: int) -> RiskMethodology:
    methodology = db.get(RiskMethodology, methodology_id)
    if not methodology:
        raise HTTPException(status_code=404, detail="Methodology not found")
    return methodology


def _ensure_editable(methodology: RiskMethodology) -> None:
    """
    A methodology that has produced any result is immutable, so every
    past result stays reproducible from the row it names. Revise it by
    cloning (POST /{id}/clone) and activating the clone.
    """

    if methodology.locked_at is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Methodology '{methodology.name}' v{methodology.version or 1} has been "
                "used by risk calculations and can no longer be edited. Clone it "
                f"(POST /api/risk-methodologies/{methodology.id}/clone), change the "
                "clone, and activate that."
            ),
        )


class ReasonRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required.")
        return value.strip()


class CloneRequest(ReasonRequest):
    name: str | None = None


class ResidualGridUpdate(ReasonRequest):
    residual_grid: dict[str, Any]


@router.get("", response_model=list[RiskMethodologyResponse])
def list_methodologies(db: Session = Depends(get_db)):
    methodologies = (
        db.query(RiskMethodology)
        .order_by(RiskMethodology.created_at.desc())
        .all()
    )
    return [_to_response(m) for m in methodologies]


@router.post("", response_model=RiskMethodologyResponse, status_code=201)
def create_methodology(
    payload: RiskMethodologyCreate,
    db: Session = Depends(get_db),
    # R6.1: "authorized administrators" configure the methodology.
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    if payload.is_active:
        db.query(RiskMethodology).filter(
            RiskMethodology.is_active.is_(True)
        ).update({"is_active": False})

    now = datetime.now(timezone.utc)
    actor = current_user.full_name or current_user.email
    methodology = RiskMethodology(
        name=payload.name,
        is_active=payload.is_active,
        weights=json.dumps(payload.weights),
        thresholds=json.dumps(payload.thresholds),
        version=1,
        change_reason="Created.",
    )
    if payload.is_active:
        # Creating straight into use is the approval, and is recorded as one.
        methodology.approved_by = actor
        methodology.approved_at = now
        methodology.approval_reason = "Activated on creation."
        methodology.effective_from = now
    db.add(methodology)

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Risk methodology '{payload.name}' created"
            + (" and activated." if payload.is_active else ".")
        ),
    )

    db.commit()
    db.refresh(methodology)
    return _to_response(methodology)


@router.patch("/{methodology_id}", response_model=RiskMethodologyResponse)
def update_methodology(
    methodology_id: int,
    payload: RiskMethodologyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    methodology = _get_or_404(db, methodology_id)
    _ensure_editable(methodology)

    if payload.name is not None:
        methodology.name = payload.name
    if payload.weights is not None:
        methodology.weights = json.dumps(payload.weights)
    if payload.thresholds is not None:
        methodology.thresholds = json.dumps(payload.thresholds)

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=f"Risk methodology '{methodology.name}' updated.",
    )

    db.commit()
    db.refresh(methodology)
    return _to_response(methodology)


@router.patch(
    "/{methodology_id}/activate",
    response_model=RiskMethodologyResponse,
)
def activate_methodology(
    methodology_id: int,
    payload: ReasonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    Activation is the approval: it records who approved this version, why
    and from when, and retires the previously active one. The whole
    configuration is validated first, so a broken methodology can never
    go live.
    """

    methodology = _get_or_404(db, methodology_id)

    config = config_from_row(methodology)
    errors = [
        error
        for rule in config["escalation_rules"]
        for error in validate_rule(rule, config["risk_bands"])
    ] + validate_residual_grid(config["residual_grid"], config["risk_bands"])
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    now = datetime.now(timezone.utc)
    actor = current_user.full_name or current_user.email

    for previous in db.query(RiskMethodology).filter(RiskMethodology.is_active.is_(True)).all():
        if previous.id != methodology.id:
            previous.is_active = False
            previous.retired_at = now

    methodology.is_active = True
    methodology.retired_at = None
    methodology.approved_by = actor
    methodology.approved_at = now
    methodology.approval_reason = payload.reason
    methodology.effective_from = now

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=actor,
        actor_id=current_user.id,
        details=(
            f"Risk methodology '{methodology.name}' v{methodology.version or 1} "
            f"({config['methodology_fingerprint'][:19]}...) approved and activated "
            f"by {actor}; future calculations use it. Reason: {payload.reason}"
        ),
    )

    db.commit()
    db.refresh(methodology)
    return _to_response(methodology)


_CONFIG_COLUMNS = [
    "weights",
    "thresholds",
    "factor_weights",
    "likelihood_scale",
    "impact_scale",
    "risk_bands",
    "escalation_rules",
    "required_approvals",
    "mitigant_categories",
    "residual_grid",
]


def _lineage_root(db: Session, methodology: RiskMethodology) -> int:
    seen = set()
    current = methodology
    while current.parent_id and current.parent_id not in seen:
        seen.add(current.id)
        parent = db.get(RiskMethodology, current.parent_id)
        if parent is None:
            break
        current = parent
    return current.id


@router.post("/{methodology_id}/clone", response_model=RiskMethodologyResponse, status_code=201)
def clone_methodology(
    methodology_id: int,
    payload: CloneRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    """
    The only way to change a methodology that is in use: an editable,
    inactive copy one version up, linked to its parent. The parent and
    every result it produced are untouched.
    """

    source = _get_or_404(db, methodology_id)
    root = _lineage_root(db, source)
    lineage_versions = [
        row.version or 1
        for row in db.query(RiskMethodology).all()
        if _lineage_root(db, row) == root
    ]

    # Materialise defaults into the clone, so it carries its full
    # configuration explicitly rather than depending on code defaults
    # that could change under it.
    config = config_from_row(source)
    clone = RiskMethodology(
        name=payload.name or source.name,
        is_active=False,
        version=max(lineage_versions) + 1,
        parent_id=source.id,
        change_reason=payload.reason,
    )
    for column in _CONFIG_COLUMNS:
        if column in {"weights", "thresholds"}:
            setattr(clone, column, getattr(source, column))
        elif column == "factor_weights":
            clone.factor_weights = json.dumps(config["factor_weights"])
        else:
            setattr(clone, column, json.dumps(config[column]))
    db.add(clone)
    db.flush()

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Risk methodology '{source.name}' v{source.version or 1} cloned to "
            f"v{clone.version} (id {clone.id}) for revision. Reason: {payload.reason}"
        ),
    )

    db.commit()
    db.refresh(clone)
    return _to_response(clone)


@router.get("/{methodology_id}/config")
def get_methodology_full_config(
    methodology_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Every setting of one version, with its fingerprint."""

    methodology = _get_or_404(db, methodology_id)
    return {**_to_response(methodology), "config": config_from_row(methodology)}


@router.put("/{methodology_id}/residual-grid")
def replace_residual_grid(
    methodology_id: int,
    payload: ResidualGridUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    methodology = _get_or_404(db, methodology_id)
    _ensure_editable(methodology)

    errors = validate_residual_grid(payload.residual_grid, config_from_row(methodology)["risk_bands"])
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    methodology.residual_grid = json.dumps(payload.residual_grid)
    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Residual grid on methodology '{methodology.name}' v{methodology.version or 1} "
            f"set to grid version {payload.residual_grid.get('version')}. Reason: {payload.reason}"
        ),
    )
    db.commit()
    return {"methodology_id": methodology.id, "residual_grid": payload.residual_grid}


# ---------------------------------------------------------------------------
# Policy rule library (override / minimum-band rules, see
# app/risk_engine/scoring.py::DEFAULT_ESCALATION_RULES). Rules are data on
# the methodology row; only "approved" ones are applied by the scoring
# engine, so a rule can be staged as "draft" and reviewed before it bites.
# ---------------------------------------------------------------------------

require_admin_role = require_role(UserRole.ADMIN)


class EscalationRulesUpdate(BaseModel):
    rules: list[dict[str, Any]]
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("A reason is required to change policy rules.")
        return value


@router.get("/active/escalation-rules")
def get_active_escalation_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The rule library in force, normalized, with each rule's status."""

    config = get_methodology_config(db)
    return {
        "methodology_id": config["methodology_id"],
        "methodology_name": config["methodology_name"],
        "rules": [normalize_rule(rule) for rule in config["escalation_rules"]],
    }


@router.get("/active/stage4-rules")
def get_stage4_rules(current_user: User = Depends(get_current_user)):
    """P4: the fixed Stage 4 rules in force, with their signal definitions,
    ruleset version and status (PROVISIONAL pending business validation)."""

    from app.risk_engine import stage4_rules

    return stage4_rules.ruleset()


@router.put("/{methodology_id}/escalation-rules")
def replace_escalation_rules(
    methodology_id: int,
    payload: EscalationRulesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_role),
):
    methodology = (
        db.query(RiskMethodology)
        .filter(RiskMethodology.id == methodology_id)
        .first()
    )

    if not methodology:
        raise HTTPException(status_code=404, detail="Methodology not found")

    _ensure_editable(methodology)

    risk_bands = config_from_row(methodology)["risk_bands"]
    rules = [normalize_rule(rule) for rule in payload.rules]
    errors = [error for rule in rules for error in validate_rule(rule, risk_bands)]
    codes = [rule["rule_code"] for rule in rules]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        errors.append(f"Duplicate rule_code(s): {', '.join(duplicates)}")
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    previous = {
        rule["rule_code"]: rule
        for rule in (normalize_rule(r) for r in json.loads(methodology.escalation_rules or "[]"))
    }
    methodology.escalation_rules = json.dumps(rules)

    changes = []
    for rule in rules:
        before = previous.get(rule["rule_code"])
        if before is None:
            changes.append(f"{rule['rule_code']} added ({rule['status']})")
        elif before != rule:
            changes.append(
                f"{rule['rule_code']} changed ({before.get('status')} -> {rule['status']})"
            )
    changes += [f"{code} removed" for code in previous if code not in codes]

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Policy rules on methodology '{methodology.name}' replaced: "
            f"{'; '.join(changes) or 'no changes'}. Reason: {payload.reason.strip()}"
        ),
    )

    db.commit()
    return {"methodology_id": methodology.id, "rules": rules}


# ---------------------------------------------------------------------------
# R6.1: the scoring configuration itself -- factor weights, likelihood and
# impact scales, risk bands and the approvals each band requires. Same
# governance as the grid and rule library: Admin only, a reason is
# required, and a methodology that has produced results is changed on a
# clone so every past result stays reproducible.
# ---------------------------------------------------------------------------

APPROVAL_ROLES = {"ANALYST", "REVIEWER", "COMMITTEE"}
BAND_NAMES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class ScoringConfigUpdate(ReasonRequest):
    factor_weights: dict[str, float] | None = None
    likelihood_scale: list[dict[str, Any]] | None = None
    impact_scale: list[dict[str, Any]] | None = None
    risk_bands: list[dict[str, Any]] | None = None
    required_approvals: dict[str, list[str]] | None = None


def _validate_weights(weights: dict[str, float]) -> list[str]:
    from app.schemas.risk_factor import RISK_CATEGORIES

    errors = []
    unknown = sorted(set(weights) - set(RISK_CATEGORIES))
    if unknown:
        errors.append(f"Unknown risk categories: {', '.join(unknown)}.")
    if any(not isinstance(value, (int, float)) or value < 0 for value in weights.values()):
        errors.append("Factor weights must be zero or positive numbers.")
    elif sum(weights.values()) <= 0:
        errors.append("At least one factor weight must be above zero.")
    return errors


def _validate_scale(name: str, scale: list[dict[str, Any]]) -> list[str]:
    try:
        values = [int(item["value"]) for item in scale]
        labels = [str(item["label"]).strip() for item in scale]
    except (KeyError, TypeError, ValueError):
        return [f"Each {name} step needs an integer 'value' and a 'label'."]
    errors = []
    if len(values) < 2:
        errors.append(f"The {name} scale needs at least two steps.")
    if values != sorted(set(values)) or any(value < 1 for value in values):
        errors.append(f"{name.capitalize()} values must be distinct, ascending and start at 1 or above.")
    if not all(labels):
        errors.append(f"Every {name} step needs a label.")
    return errors


def _validate_bands(bands: list[dict[str, Any]]) -> list[str]:
    try:
        rows = sorted(({"name": b["name"], "min": float(b["min"]), "max": float(b["max"])} for b in bands), key=lambda b: b["min"])
    except (KeyError, TypeError, ValueError):
        return ["Each risk band needs a 'name', 'min' and 'max'."]
    errors = []
    # The residual grid, rules and reports all speak these four bands.
    if [row["name"] for row in rows] != BAND_NAMES:
        errors.append(f"Risk bands must be {', '.join(BAND_NAMES)}, in ascending order of score.")
    if rows and (rows[0]["min"] != 0 or rows[-1]["max"] != 100):
        errors.append("Risk bands must cover scores 0 to 100.")
    for lower, upper in zip(rows, rows[1:]):
        if upper["min"] != lower["max"] + 1:
            errors.append(f"{upper['name']} must start one point after {lower['name']} ends.")
    if any(row["min"] > row["max"] for row in rows):
        errors.append("A band's minimum can't be above its maximum.")
    return errors


def _validate_approvals(approvals: dict[str, list[str]]) -> list[str]:
    errors = []
    if set(approvals) != set(BAND_NAMES):
        errors.append(f"Required approvals must be given for each band: {', '.join(BAND_NAMES)}.")
    for band, roles in approvals.items():
        unknown = sorted(set(roles) - APPROVAL_ROLES)
        if unknown:
            errors.append(f"{band}: unknown approval {', '.join(unknown)} (use {', '.join(sorted(APPROVAL_ROLES))}).")
        if not roles:
            errors.append(f"{band}: at least one approval is required.")
    return errors


@router.put("/{methodology_id}/scoring-config")
def update_scoring_config(
    methodology_id: int,
    payload: ScoringConfigUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin_role),
):
    methodology = _get_or_404(db, methodology_id)
    _ensure_editable(methodology)

    changes = payload.model_dump(exclude_unset=True, exclude={"reason"})
    changes = {key: value for key, value in changes.items() if value is not None}
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing to change.")

    errors: list[str] = []
    if "factor_weights" in changes:
        errors += _validate_weights(changes["factor_weights"])
    if "likelihood_scale" in changes:
        errors += _validate_scale("likelihood", changes["likelihood_scale"])
    if "impact_scale" in changes:
        errors += _validate_scale("impact", changes["impact_scale"])
    if "risk_bands" in changes:
        errors += _validate_bands(changes["risk_bands"])
    if "required_approvals" in changes:
        errors += _validate_approvals(changes["required_approvals"])
    if errors:
        raise HTTPException(status_code=400, detail=errors)

    for field, value in changes.items():
        setattr(methodology, field, json.dumps(value))

    log_audit_event(
        db=db,
        assessment_id=None,
        action=AuditAction.METHODOLOGY_CHANGED,
        actor=current_user.full_name or current_user.email,
        actor_id=current_user.id,
        details=(
            f"Scoring configuration on methodology '{methodology.name}' v{methodology.version or 1} "
            f"changed ({', '.join(sorted(changes))}). Reason: {payload.reason}"
        ),
    )
    db.commit()
    return {"methodology_id": methodology.id, "config": config_from_row(methodology)}
