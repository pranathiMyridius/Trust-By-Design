"""
Stage 17: Reporting and Monitoring.

Builds the operational (R17.1), risk (R17.2), governance (R17.3) and AI
evaluation (R17.4) reports, plus the high-risk portfolio view, as plain
JSON-serialisable dicts. app/api/reports.py handles auth and HTTP.

Period semantics (acceptance: "given a selected date range, authorized
users can generate reports for that period"):
  * The assessment population is every assessment the user may see
    (same scoping as the assessment list) that was *created* in the
    period. Point-in-time views (open conditions, unresolved findings,
    overdue items, ...) are over that population as it stands now.
  * Event-style lists (overrides, exceptions, methodology changes, AI
    usage) are filtered on the event's own timestamp instead.
  * No date_from / date_to means unbounded on that side.
"""

from __future__ import annotations

import calendar
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.user import User
from app.risk_engine.scoring import residual_exceeds_tolerance
from app.services import workflow
from app.services.reassessment_service import review_interval_months

HIGH_BANDS = {"HIGH", "CRITICAL"}
BAND_ORDER = ["NOT_ASSESSED", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

REASSESSMENT_DUE_SOON_DAYS = 60
DEFAULT_RESIDUAL_TOLERANCE = 60.0

APPROVED_DECISIONS = {"APPROVED", "APPROVED_WITH_CONDITIONS"}


# ---------------------------------------------------------------------------
# Period + population
# ---------------------------------------------------------------------------


@dataclass
class Period:
    date_from: date | None
    date_to: date | None

    @property
    def start(self) -> datetime | None:
        return datetime.combine(self.date_from, time.min, tzinfo=timezone.utc) if self.date_from else None

    @property
    def end(self) -> datetime | None:  # exclusive
        return (
            datetime.combine(self.date_to + timedelta(days=1), time.min, tzinfo=timezone.utc)
            if self.date_to
            else None
        )

    def contains(self, value: datetime | date | None) -> bool:
        if value is None:
            return False
        if not isinstance(value, datetime):
            value = datetime.combine(value, time.min, tzinfo=timezone.utc)
        value = workflow.as_utc(value)
        if self.start and value < self.start:
            return False
        if self.end and value >= self.end:
            return False
        return True

    def describe(self) -> dict:
        return {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
        }


def _naive(value: datetime | None) -> datetime | None:
    # Columns are stored as naive UTC; compare in SQL with naive values.
    return value.replace(tzinfo=None) if value else None


def population(db: Session, user: User, period: Period) -> list[Assessment]:
    from app.api.assessments import _scope_assessments_for_user
    from app.models.audit_trail import AssessmentRetention

    query = _scope_assessments_for_user(db.query(Assessment), user)
    # R16.1: soft-deleted assessments are kept but hidden -- same rule as
    # the assessments list, so reports don't count them.
    deleted_ids = [
        row.assessment_id
        for row in db.query(AssessmentRetention.assessment_id).filter(
            AssessmentRetention.is_deleted.is_(True)
        )
    ]
    if deleted_ids:
        query = query.filter(Assessment.id.notin_(deleted_ids))
    if period.start:
        query = query.filter(Assessment.created_at >= _naive(period.start))
    if period.end:
        query = query.filter(Assessment.created_at < _naive(period.end))
    assessments = query.order_by(Assessment.created_at.asc()).all()
    for assessment in assessments:
        workflow.ensure_workflow_state(db, assessment)
    return assessments


def _in_period_filter(query, column, period: Period):
    if period.start:
        query = query.filter(column >= _naive(period.start))
    if period.end:
        query = query.filter(column < _naive(period.end))
    return query


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def business_unit_of(assessment: Assessment) -> str:
    """R17.1: the business unit, falling back to the legal entity for
    older records that captured only "legal entity or business unit"."""

    return (assessment.business_unit or assessment.legal_entity or "Unspecified").strip() or "Unspecified"


def band_of(assessment: Assessment) -> str:
    band = assessment.residual_risk_level or assessment.inherent_risk_level or assessment.risk_level
    return (band or "NOT_ASSESSED").upper()


def score_of(assessment: Assessment) -> float | None:
    for value in (assessment.residual_score, assessment.inherent_score, assessment.overall_score):
        if value is not None:
            return value
    return None


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    parts = re.split(r"[,;\n/|]+|\band\b", value)
    return [part.strip() for part in parts if part and part.strip()]


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return workflow.as_utc(value).isoformat()
    return value.isoformat()


def _ref(assessment: Assessment) -> dict:
    return {
        "assessment_id": assessment.id,
        "reference_id": assessment.reference_id or f"ASSESSMENT-{assessment.id}",
        "title": assessment.title,
    }


def _rate(numerator: int | float, denominator: int | float) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _days_between(start: datetime | None, end: datetime | None) -> float | None:
    if not start or not end:
        return None
    return round((workflow.as_utc(end) - workflow.as_utc(start)).total_seconds() / 86400, 2)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[index]


def _max_band(bands: Iterable[str]) -> str:
    ranked = [b for b in bands if b in BAND_ORDER]
    return max(ranked, key=BAND_ORDER.index) if ranked else "NOT_ASSESSED"


def _counts(values: Iterable[str], labels: dict[str, str] | None = None) -> list[dict]:
    counter = Counter(values)
    return [
        {"key": key, "label": (labels or {}).get(key, key), "count": count}
        for key, count in counter.most_common()
    ]


def _completion(assessment: Assessment) -> tuple[datetime | None, str | None]:
    """When (and how) the assessment reached a final outcome, if it has."""

    if assessment.committee_decided_at and assessment.committee_decision in {
        "APPROVED",
        "APPROVED_WITH_CONDITIONS",
        "REJECTED",
    }:
        return assessment.committee_decided_at, assessment.committee_decision
    if assessment.manager_decision == "REJECT" and assessment.manager_decided_at:
        return assessment.manager_decided_at, "MANAGER_REJECTED"
    if assessment.closed_at:
        return assessment.closed_at, "CLOSED"
    return None, None


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def _residual_tolerance(db: Session) -> float:
    from app.models.challenge_review import ChallengeTriggerConfig

    config = (
        db.query(ChallengeTriggerConfig)
        .filter(ChallengeTriggerConfig.is_active.is_(True))
        .order_by(ChallengeTriggerConfig.updated_at.desc())
        .first()
    )
    return float(config.residual_risk_tolerance) if config else DEFAULT_RESIDUAL_TOLERANCE


def _current_factors(db: Session, ids: list[int]):
    from app.models.risk_factor import RiskFactor

    if not ids:
        return []
    return (
        db.query(RiskFactor)
        .filter(RiskFactor.assessment_id.in_(ids), RiskFactor.is_current.is_(True))
        .all()
    )


def _factor_band(factor, config: dict) -> str:
    """Human-rated band when rated, else the AI's severity."""

    from app.risk_engine.scoring import compute_factor_score, determine_risk_band

    if factor.likelihood and factor.impact:
        return determine_risk_band(
            compute_factor_score(
                factor.likelihood, factor.impact, config.get("likelihood_scale"), config.get("impact_scale")
            ),
            config.get("risk_bands"),
        ).upper()
    return (factor.severity or "LOW").upper()


def _open_actions(db: Session, ids: list[int]) -> dict[int, list[dict]]:
    from app.models.action_item import ActionItem, OPEN_STATUSES
    from app.models.committee_condition import CommitteeCondition

    result: dict[int, list[dict]] = defaultdict(list)
    if not ids:
        return result
    today = date.today()
    linked_conditions = set()
    for item in db.query(ActionItem).filter(ActionItem.assessment_id.in_(ids), ActionItem.status.in_(OPEN_STATUSES)):
        if item.committee_condition_id:
            linked_conditions.add(item.committee_condition_id)
        result[item.assessment_id].append(
            {
                "kind": "ACTION_ITEM",
                "id": item.id,
                "title": item.title,
                "source": item.source_type,
                "owner": item.owner,
                "priority": item.priority,
                "status": item.status,
                "due_date": _iso(item.due_date),
                "overdue": bool(item.due_date and item.due_date < today),
                "escalated": bool(item.escalated),
            }
        )
    for condition in db.query(CommitteeCondition).filter(
        CommitteeCondition.assessment_id.in_(ids),
        CommitteeCondition.status.notin_(["COMPLETED", "CANCELLED"]),
    ):
        if condition.id in linked_conditions:
            continue  # already listed via its action item
        result[condition.assessment_id].append(
            {
                "kind": "COMMITTEE_CONDITION",
                "id": condition.id,
                "title": condition.description,
                "source": "COMMITTEE_CONDITION",
                "owner": condition.owner,
                "priority": condition.priority,
                "status": condition.status,
                "due_date": _iso(condition.due_date),
                "overdue": bool(condition.due_date and condition.due_date < today),
                "escalated": False,
            }
        )
    return result


def _meta(user: User, period: Period, population_size: int) -> dict:
    return {
        "period": period.describe(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": user.full_name or user.email,
        "population": population_size,
    }


# ---------------------------------------------------------------------------
# R17.1 Operational
# ---------------------------------------------------------------------------


def operational_report(db: Session, user: User, period: Period) -> dict:
    from app.models.committee_condition import CommitteeCondition
    from app.models.workflow_transition import WorkflowTransition

    assessments = population(db, user, period)
    ids = [a.id for a in assessments]
    labels = {s.value: label for s, label in workflow.WORKFLOW_STATUS_LABELS.items()}

    # Completion time: submission -> final outcome, for outcomes in period.
    completion_rows = []
    for assessment in assessments:
        completed_at, outcome = _completion(assessment)
        if completed_at is None:
            continue
        days = _days_between(assessment.submitted_at or assessment.created_at, completed_at)
        completion_rows.append({**_ref(assessment), "outcome": outcome, "completed_at": _iso(completed_at), "days": days})
    durations = [row["days"] for row in completion_rows if row["days"] is not None]

    # Average time spent in each lifecycle status, from workflow history.
    time_in_status: dict[str, list[float]] = defaultdict(list)
    if ids:
        history = (
            db.query(WorkflowTransition)
            .filter(WorkflowTransition.assessment_id.in_(ids))
            .order_by(WorkflowTransition.assessment_id, WorkflowTransition.created_at, WorkflowTransition.id)
            .all()
        )
        by_assessment: dict[int, list] = defaultdict(list)
        for entry in history:
            by_assessment[entry.assessment_id].append(entry)
        for entries in by_assessment.values():
            for current, following in zip(entries, entries[1:]):
                if current.to_workflow_status != following.to_workflow_status:
                    days = _days_between(current.created_at, following.created_at)
                    if days is not None:
                        time_in_status[current.to_workflow_status].append(days)

    overdue = []
    for assessment in assessments:
        if workflow.compute_sla_state(assessment) != "OVERDUE":
            continue
        owner = workflow.describe_owner(db, assessment)
        overdue.append(
            {
                **_ref(assessment),
                "workflow_status": labels.get(assessment.workflow_status, assessment.workflow_status),
                "owner": owner["owner_name"],
                "team": owner["team"],
                "status_due_at": _iso(assessment.status_due_at),
                "target_date": _iso(assessment.target_date),
                "escalation_level": assessment.escalation_level or 0,
            }
        )

    open_actions = _open_actions(db, ids)
    open_conditions = []
    for assessment in assessments:
        for action in open_actions.get(assessment.id, []):
            open_conditions.append({**_ref(assessment), **{k: v for k, v in action.items() if k != "id"}})
    open_conditions.sort(key=lambda row: (not row["overdue"], row["due_date"] or "9999"))

    deferred_rejected = []
    for assessment in assessments:
        outcome = None
        if assessment.committee_decision in {"DEFERRED", "REJECTED"}:
            outcome, decided_at, rationale = (
                assessment.committee_decision,
                assessment.committee_decided_at,
                assessment.committee_rationale,
            )
        elif assessment.manager_decision == "REJECT":
            outcome, decided_at, rationale = "MANAGER_REJECTED", assessment.manager_decided_at, assessment.manager_comment
        if outcome:
            deferred_rejected.append(
                {
                    **_ref(assessment),
                    "outcome": outcome,
                    "decided_at": _iso(decided_at),
                    "rationale": rationale,
                    "current_status": labels.get(assessment.workflow_status, assessment.workflow_status),
                }
            )

    condition_total = (
        db.query(CommitteeCondition).filter(CommitteeCondition.assessment_id.in_(ids)).count() if ids else 0
    )

    return {
        **_meta(user, period, len(assessments)),
        "summary": {
            "total_assessments": len(assessments),
            "active": sum(1 for a in assessments if a.workflow_status != "CLOSED"),
            "completed": len(completion_rows),
            "overdue": len(overdue),
            "open_conditions": len(open_conditions),
            "deferred": sum(1 for r in deferred_rejected if r["outcome"] == "DEFERRED"),
            "rejected": sum(1 for r in deferred_rejected if r["outcome"] != "DEFERRED"),
            "avg_completion_days": round(statistics.mean(durations), 2) if durations else None,
            "median_completion_days": round(statistics.median(durations), 2) if durations else None,
            "committee_conditions_total": condition_total,
        },
        "by_status": _counts((a.workflow_status for a in assessments), labels),
        "by_business_unit": _counts(business_unit_of(a) for a in assessments),
        "by_legal_entity": _counts((a.legal_entity or "Unspecified" for a in assessments)),
        "by_risk_band": sorted(
            _counts(band_of(a) for a in assessments),
            key=lambda row: -BAND_ORDER.index(row["key"]) if row["key"] in BAND_ORDER else 0,
        ),
        "time_in_status": sorted(
            [
                {
                    "key": status,
                    "label": labels.get(status, status),
                    "avg_days": round(statistics.mean(values), 2),
                    "transitions": len(values),
                }
                for status, values in time_in_status.items()
            ],
            key=lambda row: -row["avg_days"],
        ),
        "completions": completion_rows,
        "overdue": overdue,
        "open_conditions": open_conditions,
        "deferred_and_rejected": deferred_rejected,
    }


# ---------------------------------------------------------------------------
# R17.2 Risk
# ---------------------------------------------------------------------------


def _dimension(assessments: list[Assessment], values_for) -> list[dict]:
    groups: dict[str, dict] = {}
    for assessment in assessments:
        values = values_for(assessment) or ["Unspecified"]
        for value in values:
            key = value.strip().lower()
            group = groups.setdefault(key, {"label": value.strip(), "assessments": [], "bands": [], "scores": []})
            group["assessments"].append(assessment.id)
            group["bands"].append(band_of(assessment))
            score = score_of(assessment)
            if score is not None:
                group["scores"].append(score)
    rows = [
        {
            "key": group["label"],
            "label": group["label"],
            "assessments": len(group["assessments"]),
            "high_or_critical": sum(1 for b in group["bands"] if b in HIGH_BANDS),
            "avg_score": round(statistics.mean(group["scores"]), 1) if group["scores"] else None,
            "max_band": _max_band(group["bands"]),
        }
        for group in groups.values()
    ]
    return sorted(rows, key=lambda r: (-r["high_or_critical"], -(r["avg_score"] or 0), -r["assessments"]))


def risk_report(db: Session, user: User, period: Period) -> dict:
    from app.models.control import ControlGap
    from app.risk_engine.methodology import get_methodology_config
    from app.risk_engine.scoring import calculate_occ_risk_profile

    assessments = population(db, user, period)
    by_id = {a.id: a for a in assessments}
    ids = list(by_id)
    config = get_methodology_config(db)

    factors = [f for f in _current_factors(db, ids) if f.applicable and not f.excluded]

    high_risks = []
    category_stats: dict[str, dict] = defaultdict(lambda: {"applicable": 0, "high": 0, "scores": [], "bands": Counter()})
    indicator_counts: Counter = Counter()
    for factor in factors:
        band = _factor_band(factor, config)
        stats = category_stats[factor.category]
        stats["applicable"] += 1
        stats["scores"].append(factor.score or 0)
        stats["bands"][band] += 1
        indicators = factor.get_indicators() if hasattr(factor, "get_indicators") else []
        indicator_counts.update(indicators)
        if band in HIGH_BANDS:
            stats["high"] += 1
            assessment = by_id[factor.assessment_id]
            high_risks.append(
                {
                    **_ref(assessment),
                    "product": assessment.product_or_service_name,
                    "geographies": assessment.countries_jurisdictions,
                    "category": factor.category,
                    "band": band,
                    "score": factor.score,
                    "rated_by_human": bool(factor.likelihood and factor.impact),
                    "source": factor.source,
                    "rationale": factor.rationale,
                    "indicators": indicators,
                }
            )
    high_risks.sort(key=lambda r: (-BAND_ORDER.index(r["band"]), -(r["score"] or 0)))

    drivers = sorted(
        [
            {
                "key": category,
                "label": category.replace("_", " ").title(),
                "applicable": stats["applicable"],
                "high_or_critical": stats["high"],
                "avg_score": round(statistics.mean(stats["scores"]), 1) if stats["scores"] else None,
            }
            for category, stats in category_stats.items()
        ],
        key=lambda r: (-r["high_or_critical"], -(r["avg_score"] or 0)),
    )
    typology = sorted(
        [
            {
                "key": category,
                "label": category.replace("_", " ").title(),
                "applicable": stats["applicable"],
                **{band.lower(): stats["bands"].get(band, 0) for band in ("LOW", "MEDIUM", "HIGH", "CRITICAL")},
            }
            for category, stats in category_stats.items()
        ],
        key=lambda r: (-(r["critical"] + r["high"]), -r["applicable"]),
    )

    gaps = []
    if ids:
        for gap in (
            db.query(ControlGap)
            .filter(ControlGap.assessment_id.in_(ids), ControlGap.resolved.is_(False))
            .order_by(ControlGap.detected_at.desc())
        ):
            gaps.append(
                {
                    **_ref(by_id[gap.assessment_id]),
                    "gap_type": gap.gap_type,
                    "description": gap.description,
                    "detected_at": _iso(gap.detected_at),
                }
            )

    tolerance = _residual_tolerance(db)
    above_tolerance = [
        {
            **_ref(a),
            "residual_score": a.residual_score,
            "residual_band": a.residual_risk_level,
            "tolerance": tolerance,
            # None when a policy rule set the residual band (no score).
            "excess": round(a.residual_score - tolerance, 1) if a.residual_score is not None else None,
            "workflow_status": workflow.label_for(a.workflow_status),
        }
        for a in assessments
        if residual_exceeds_tolerance(a.residual_score, a.residual_risk_level, tolerance)
    ]
    # Policy-set residuals (no score) are the most severe; list them first.
    above_tolerance.sort(key=lambda r: -(r["excess"] if r["excess"] is not None else float("inf")))

    # The same OCC NR 96-2a lens the per-assessment endpoint exposes, applied
    # across the whole reporting population. `factors` is already filtered to
    # applicable, non-excluded rows. Purely derived -- it neither reads nor
    # changes any stored score. Per-category contributing_factors is dropped
    # here: at portfolio scale it would be thousands of rows, and the
    # high_and_critical_risks list above already carries factor-level detail.
    occ_profile = calculate_occ_risk_profile(
        [
            {
                "id": factor.id,
                "category": factor.category,
                "applicable": True,
                "excluded": False,
                "score": factor.score,
                "severity": factor.severity,
                "rated": factor.likelihood is not None and factor.impact is not None,
            }
            for factor in factors
        ],
        risk_bands=config["risk_bands"],
    )
    by_occ_category = [
        {key: value for key, value in entry.items() if key != "contributing_factors"}
        for entry in occ_profile["categories"]
    ]

    return {
        **_meta(user, period, len(assessments)),
        "summary": {
            "high_or_critical_risks": len(high_risks),
            "high_risk_assessments": sum(1 for a in assessments if band_of(a) in HIGH_BANDS),
            "open_control_gaps": len(gaps),
            "above_tolerance": len(above_tolerance),
            "residual_tolerance": tolerance,
        },
        "high_and_critical_risks": high_risks,
        "main_risk_drivers": drivers[:10],
        "top_indicators": [{"key": k, "label": k.replace("_", " ").title(), "count": c} for k, c in indicator_counts.most_common(10)],
        "by_geography": _dimension(assessments, lambda a: _split(a.countries_jurisdictions)),
        "by_product": _dimension(assessments, lambda a: [a.product_or_service_name] if a.product_or_service_name else []),
        "by_customer_segment": _dimension(assessments, lambda a: _split(a.customer_segment)),
        "by_channel": _dimension(assessments, lambda a: _split(a.delivery_channels)),
        "by_typology": typology,
        "by_occ_category": by_occ_category,
        "occ_uncovered_categories": occ_profile["uncovered_categories"],
        "control_gaps_by_type": _counts(g["gap_type"] for g in gaps),
        "control_gaps": gaps,
        "residual_above_tolerance": above_tolerance,
    }


# ---------------------------------------------------------------------------
# R17.3 Governance
# ---------------------------------------------------------------------------


def governance_report(db: Session, user: User, period: Period) -> dict:
    from app.models.action_item import ActionItem
    from app.models.assessment_comment import AssessmentComment
    from app.models.assessment_override import AssessmentOverride
    from app.models.audit_event import AuditEvent
    from app.models.challenge_review import ChallengeFinding
    from app.models.committee_condition import CommitteeCondition
    from app.models.inherent_risk_calculation import InherentRiskCalculation
    from app.models.risk_factor import RiskFactor

    assessments = population(db, user, period)
    by_id = {a.id: a for a in assessments}
    ids = list(by_id)

    # --- Human overrides: original value -> final value, with reason. ---
    overrides = []
    if ids:
        for row in _in_period_filter(
            db.query(AssessmentOverride).filter(AssessmentOverride.assessment_id.in_(ids)),
            AssessmentOverride.created_at,
            period,
        ):
            if row.section == "INHERENT_RISK":
                # Reported from the calculation rows below (which also
                # cover overrides made before the ledger recorded them).
                continue
            overrides.append(
                {
                    **_ref(by_id[row.assessment_id]),
                    "kind": "AI_VALUE_OVERRIDE",
                    "review_status": row.review_status,
                    "field": f"{row.section}: {row.field_name}",
                    "original_value": row.ai_value,
                    "final_value": row.human_value,
                    "reason": row.reason,
                    "by": row.overridden_by,
                    "at": _iso(row.created_at),
                }
            )
        for calc in _in_period_filter(
            db.query(InherentRiskCalculation).filter(
                InherentRiskCalculation.assessment_id.in_(ids),
                InherentRiskCalculation.overridden.is_(True),
            ),
            InherentRiskCalculation.override_at,
            period,
        ):
            overrides.append(
                {
                    **_ref(by_id[calc.assessment_id]),
                    "kind": "INHERENT_RISK_OVERRIDE",
                    "field": "Inherent risk score",
                    "original_value": f"{calc.calculated_score:g} ({calc.calculated_band})"
                    if calc.calculated_score is not None
                    else calc.calculated_band,
                    "final_value": f"{calc.override_value:g} ({calc.override_band})"
                    if calc.override_value is not None
                    else calc.override_band,
                    "reason": calc.override_reason,
                    "by": calc.override_by,
                    "at": _iso(calc.override_at),
                }
            )
        for event in _in_period_filter(
            db.query(AuditEvent).filter(
                AuditEvent.assessment_id.in_(ids),
                AuditEvent.action == "MANUAL_SCORE_OVERRIDE",
            ),
            AuditEvent.created_at,
            period,
        ):
            overrides.append(
                {
                    **_ref(by_id[event.assessment_id]),
                    "kind": "MANUAL_SCORE_OVERRIDE",
                    "field": "Overall risk level",
                    "original_value": event.previous_status,
                    "final_value": event.new_status,
                    "reason": event.details,
                    "by": event.actor,
                    "at": _iso(event.created_at),
                }
            )
        for factor in _in_period_filter(
            db.query(RiskFactor).filter(
                RiskFactor.assessment_id.in_(ids),
                RiskFactor.source == "AI",
                RiskFactor.applicable.is_(True),
                RiskFactor.excluded.is_(True),
            ),
            RiskFactor.excluded_at,
            period,
        ):
            overrides.append(
                {
                    **_ref(by_id[factor.assessment_id]),
                    "kind": "AI_RISK_EXCLUDED",
                    "field": f"Risk factor: {factor.category}",
                    "original_value": f"Applicable ({factor.severity})",
                    "final_value": "Excluded",
                    "reason": factor.exclusion_reason,
                    "by": factor.excluded_by,
                    "at": _iso(factor.excluded_at),
                }
            )
    overrides.sort(key=lambda r: r["at"] or "", reverse=True)

    # --- Unresolved findings (point in time). ---
    unresolved = []
    exceptions = []
    if ids:
        for finding in db.query(ChallengeFinding).filter(ChallengeFinding.assessment_id.in_(ids)):
            if finding.resolution_status == "OPEN":
                unresolved.append(
                    {
                        **_ref(by_id[finding.assessment_id]),
                        "kind": "CHALLENGE_FINDING",
                        "category": finding.category,
                        "severity": finding.severity,
                        "description": finding.description,
                        "since": _iso(finding.detected_at),
                    }
                )
            elif finding.resolution_status == "ACCEPTED" and period.contains(finding.accepted_at or finding.detected_at):
                exceptions.append(
                    {
                        **_ref(by_id[finding.assessment_id]),
                        "kind": "ACCEPTED_FINDING",
                        "description": f"{finding.category} ({finding.severity}): {finding.description}",
                        "reason": finding.accepted_reason,
                        "by": finding.accepted_by,
                        "at": _iso(finding.accepted_at),
                    }
                )
        for comment in db.query(AssessmentComment).filter(AssessmentComment.assessment_id.in_(ids)):
            if not comment.resolved and not (comment.exception_reason or "").strip():
                unresolved.append(
                    {
                        **_ref(by_id[comment.assessment_id]),
                        "kind": "REVIEW_COMMENT",
                        "category": comment.section or "GENERAL",
                        "severity": None,
                        "description": comment.body,
                        "since": _iso(comment.created_at),
                    }
                )
            elif (comment.exception_reason or "").strip() and period.contains(comment.resolved_at or comment.created_at):
                exceptions.append(
                    {
                        **_ref(by_id[comment.assessment_id]),
                        "kind": "COMMENT_EXCEPTION",
                        "description": comment.body,
                        "reason": comment.exception_reason,
                        "by": comment.resolved_by,
                        "at": _iso(comment.resolved_at),
                    }
                )
        for item in _in_period_filter(
            db.query(ActionItem).filter(
                ActionItem.assessment_id.in_(ids),
                ActionItem.source_type == "POLICY_EXCEPTION",
            ),
            ActionItem.created_at,
            period,
        ):
            exceptions.append(
                {
                    **_ref(by_id[item.assessment_id]),
                    "kind": "POLICY_EXCEPTION",
                    "description": item.title,
                    "reason": item.description,
                    "by": item.created_by,
                    "at": _iso(item.created_at),
                }
            )
    severity_rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, None: 4}
    unresolved.sort(key=lambda r: severity_rank.get(r["severity"], 4))
    exceptions.sort(key=lambda r: r["at"] or "", reverse=True)

    # --- Approval conditions. ---
    today = date.today()
    conditions = []
    if ids:
        for condition in (
            db.query(CommitteeCondition)
            .filter(CommitteeCondition.assessment_id.in_(ids))
            .order_by(CommitteeCondition.due_date.asc())
        ):
            is_open = condition.status not in {"COMPLETED", "CANCELLED"}
            conditions.append(
                {
                    **_ref(by_id[condition.assessment_id]),
                    "description": condition.description,
                    "owner": condition.owner,
                    "priority": condition.priority,
                    "status": condition.status,
                    "due_date": _iso(condition.due_date),
                    "overdue": bool(is_open and condition.due_date and condition.due_date < today),
                    "completed_at": _iso(condition.completed_at),
                }
            )

    # --- Reassessment due dates. ---
    reassessments = []
    for assessment in assessments:
        if assessment.committee_decision not in APPROVED_DECISIONS or not assessment.committee_decided_at:
            continue
        band = band_of(assessment)
        # One source for due dates (app/services/reassessment_service.py):
        # the date stamped at approval, else the same band rule applied to
        # the decision date for records approved before it was stamped.
        months = review_interval_months(assessment)
        due = assessment.next_review_date or _add_months(
            workflow.as_utc(assessment.committee_decided_at).date(), months
        )
        days_left = (due - today).days
        reassessments.append(
            {
                **_ref(assessment),
                "band": band,
                "decided_at": _iso(assessment.committee_decided_at),
                "interval_months": months,
                "reassessment_due": due.isoformat(),
                "days_until_due": days_left,
                "state": "OVERDUE" if days_left < 0 else "DUE_SOON" if days_left <= REASSESSMENT_DUE_SOON_DAYS else "SCHEDULED",
            }
        )
    reassessments.sort(key=lambda r: r["days_until_due"])

    # --- Policy / methodology changes (global, not assessment-scoped). ---
    change_query = db.query(AuditEvent).filter(
        (AuditEvent.action.in_(["METHODOLOGY_CHANGED", "CHALLENGE_CONFIG_CHANGED"]))
        | (
            (AuditEvent.assessment_id.is_(None))
            & (AuditEvent.action == "STATUS_CHANGE")
            & (
                AuditEvent.details.like("Risk methodology%")
                | AuditEvent.details.like("Challenge trigger%")
            )
        )
    )
    policy_changes = [
        {
            "kind": "CHALLENGE_CONFIG"
            if (event.action == "CHALLENGE_CONFIG_CHANGED" or (event.details or "").startswith("Challenge"))
            else "METHODOLOGY",
            "details": event.details,
            "by": event.actor,
            "at": _iso(event.created_at),
        }
        for event in _in_period_filter(change_query, AuditEvent.created_at, period).order_by(AuditEvent.created_at.desc())
    ]

    return {
        **_meta(user, period, len(assessments)),
        "summary": {
            "human_overrides": len(overrides),
            "unresolved_findings": len(unresolved),
            "exceptions": len(exceptions),
            "approval_conditions": len(conditions),
            "open_approval_conditions": sum(1 for c in conditions if c["status"] not in {"COMPLETED", "CANCELLED"}),
            "overdue_approval_conditions": sum(1 for c in conditions if c["overdue"]),
            "reassessments_due_soon": sum(1 for r in reassessments if r["state"] == "DUE_SOON"),
            "reassessments_overdue": sum(1 for r in reassessments if r["state"] == "OVERDUE"),
            "policy_changes": len(policy_changes),
        },
        "overrides_by_kind": _counts(o["kind"] for o in overrides),
        "human_overrides": overrides,
        "unresolved_findings": unresolved,
        "exceptions": exceptions,
        "approval_conditions": conditions,
        "reassessment_due_dates": reassessments,
        "policy_changes": policy_changes,
    }


# ---------------------------------------------------------------------------
# R17.4 AI evaluation
# ---------------------------------------------------------------------------


def ai_evaluation_report(db: Session, user: User, period: Period) -> dict:
    from app.models.ai_metrics import AIEvaluationRecord, AIUsageLog
    from app.models.user import UserRole
    from app.services.ai_evaluation import refresh_current_evaluations

    assessments = population(db, user, period)
    by_id = {a.id: a for a in assessments}
    ids = list(by_id)

    # Refresh the stored comparison so it reflects the latest human work.
    records = refresh_current_evaluations(db, ids)
    db.commit()
    runs_in_period = (
        _in_period_filter(
            db.query(AIEvaluationRecord).filter(AIEvaluationRecord.assessment_id.in_(ids)),
            AIEvaluationRecord.run_at,
            period,
        ).count()
        if ids
        else 0
    )
    usable = [r for r in records if r.ai_available]

    comparable = [r for r in usable if r.band_agreement is not None]
    diffs = [r.score_difference for r in usable if r.score_difference is not None]
    rated = sum(r.factors_rated or 0 for r in usable)
    agreeing = sum(r.factors_agreeing or 0 for r in usable)
    categories_compared = sum(r.applicability_compared or 0 for r in usable)
    categories_agreeing = sum(r.applicability_agreeing or 0 for r in usable)
    ai_applicable = sum(r.ai_applicable_count for r in usable)
    excluded = sum(r.ai_factors_excluded or 0 for r in usable)
    added = sum(r.human_added_factors or 0 for r in usable)
    grounded = sum(r.grounded_factors or 0 for r in usable)
    extracted = sum(r.controls_ai_extracted or 0 for r in usable)
    matched = sum(r.controls_matched or 0 for r in usable)
    with_override = sum(1 for r in usable if (r.human_overrides or 0) > 0)
    processing = [r.processing_ms for r in records if r.processing_ms is not None]
    groundedness_scores = [r.groundedness_score for r in usable if r.groundedness_score is not None]

    metrics = {
        "risk_band_agreement": _rate(sum(1 for r in comparable if r.band_agreement), len(comparable)),
        "risk_band_comparable": len(comparable),
        "ai_human_agreement": _rate(categories_agreeing, categories_compared),
        "categories_compared": categories_compared,
        "factor_band_agreement": _rate(agreeing, rated),
        "factors_rated": rated,
        "mean_score_difference": round(statistics.mean(diffs), 2) if diffs else None,
        "mean_abs_score_difference": round(statistics.mean(abs(d) for d in diffs), 2) if diffs else None,
        "human_override_rate": _rate(with_override, len(usable)),
        "human_overrides_total": sum(r.human_overrides or 0 for r in usable),
        "missing_risk_rate": _rate(added, ai_applicable - excluded + added),
        "human_added_risks": added,
        "evidence_groundedness": _rate(grounded, ai_applicable),
        "mean_groundedness_score": round(statistics.mean(groundedness_scores), 3) if groundedness_scores else None,
        "control_mapping_accuracy": _rate(matched, extracted),
        "controls_ai_extracted": extracted,
        "unsupported_content_rate": _rate(excluded, ai_applicable),
        "unsupported_findings": sum(r.unsupported_findings or 0 for r in usable),
        "ai_excluded_risks": excluded,
        "avg_processing_ms": round(statistics.mean(processing)) if processing else None,
        "p95_processing_ms": _percentile(processing, 0.95),
        "ai_availability": _rate(len(usable), len(records)),
    }

    # --- Usage and cost, from the metering log. ---
    usage_query = _in_period_filter(db.query(AIUsageLog), AIUsageLog.created_at, period)
    if user.role not in {UserRole.ADMIN.value, UserRole.FCRM_ANALYST.value}:
        usage_query = usage_query.filter(AIUsageLog.assessment_id.in_(ids or [-1]))
    logs = usage_query.all()

    def usage_rows(key_fn) -> list[dict]:
        groups: dict[str, list] = defaultdict(list)
        for log in logs:
            groups[key_fn(log)].append(log)
        rows = []
        for key, items in groups.items():
            durations = [i.duration_ms for i in items]
            costs = [i.cost_usd for i in items if i.cost_usd is not None]
            # A failed call (no response) incurs no cost; only successful
            # calls without cost data make the total incomplete.
            billable = [i for i in items if i.success]
            rows.append(
                {
                    "key": key,
                    "calls": len(items),
                    "failures": sum(1 for i in items if not i.success),
                    "prompt_tokens": sum(i.prompt_tokens or 0 for i in items),
                    "completion_tokens": sum(i.completion_tokens or 0 for i in items),
                    "total_tokens": sum(i.total_tokens or 0 for i in items),
                    "cost_usd": round(sum(costs), 6) if costs else None,
                    # False when some calls had no cost information at all.
                    "cost_complete": all(i.cost_usd is not None for i in billable),
                    "avg_duration_ms": round(statistics.mean(durations)) if durations else None,
                    "p95_duration_ms": _percentile(durations, 0.95),
                }
            )
        return sorted(rows, key=lambda r: -r["calls"])

    all_costs = [log.cost_usd for log in logs if log.cost_usd is not None]

    per_assessment = [
        {
            **_ref(by_id[r.assessment_id]),
            "run_at": _iso(r.run_at),
            "model": r.model,
            "ai_available": r.ai_available,
            "ai_score": r.ai_overall_score,
            "ai_band": r.ai_risk_band,
            "human_score": r.human_score,
            "human_band": r.human_band,
            "band_agreement": r.band_agreement,
            "score_difference": r.score_difference,
            "categories_compared": r.applicability_compared,
            "categories_agreeing": r.applicability_agreeing,
            "human_added": r.human_added_factors,
            "ai_excluded": r.ai_factors_excluded,
            "groundedness": r.groundedness_score,
            "overrides": r.human_overrides,
            "processing_ms": r.processing_ms,
            "backfilled": r.backfilled,
        }
        for r in sorted(records, key=lambda r: r.run_at or datetime.min, reverse=True)
    ]

    not_measurable = {}
    if not comparable and not rated:
        not_measurable["risk_band_agreement"] = not_measurable["score_difference"] = (
            "No AI run in this period has both an AI-assigned score and a finalised "
            "human inherent rating yet (or the runs predate AI scoring)."
        )
    if not categories_compared:
        not_measurable["ai_human_agreement"] = "No AI-assessed assessment in this period has been reviewed by a human yet."
    if not extracted:
        not_measurable["control_mapping_accuracy"] = "No controls were extracted by AI from source documents in this period."

    return {
        **_meta(user, period, len(assessments)),
        "not_measurable": not_measurable,
        "summary": {
            "evaluated_assessments": len(records),
            "ai_runs_in_period": runs_in_period,
            "ai_calls": len(logs),
            "ai_call_failures": sum(1 for log in logs if not log.success),
            "total_tokens": sum(log.total_tokens or 0 for log in logs),
            "total_cost_usd": round(sum(all_costs), 6) if all_costs else None,
            "cost_complete": all(log.cost_usd is not None for log in logs if log.success),
        },
        "metrics": metrics,
        "usage_by_model": usage_rows(lambda log: log.response_model or log.requested_model or "unknown"),
        "usage_by_purpose": usage_rows(lambda log: log.purpose),
        # R16.1: which prompt versions produced the AI output in scope.
        "usage_by_prompt_version": usage_rows(
            lambda log: f"{log.purpose} · {log.prompt_version}" if log.prompt_version else f"{log.purpose} · unversioned"
        ),
        "per_assessment": per_assessment,
        "method_notes": {
            "ai_human_agreement": "Per risk category: the AI's applicable / not-applicable call vs the human-reviewed outcome (kept, excluded or added).",
            "risk_band_agreement": "AI overall band (system average of AI-assigned factor scores) vs the human-finalised inherent band, after any override.",
            "score_difference": "Human-finalised inherent score minus the AI overall score (system average of AI-assigned factor scores).",
            "factor_band_agreement": "Per analyst-rated factor: the AI-assigned severity vs the analyst's likelihood x impact band.",
            "missing_risk_rate": "Human-added risk factors / (AI risks kept + human-added).",
            "evidence_groundedness": "Share of AI-applicable risks whose rationale shares >=50% of its content words with the assessment's own evidence (lexical heuristic).",
            "control_mapping_accuracy": "AI-extracted existing controls that match a control type an analyst recorded.",
            "unsupported_content_rate": "AI-applicable risks subsequently excluded by a human.",
            "cost": "Provider-reported cost where available, else AI_MODEL_PRICING estimate; null where neither exists.",
        },
    }


# ---------------------------------------------------------------------------
# High-risk portfolio (acceptance criterion 2)
# ---------------------------------------------------------------------------


def high_risk_portfolio(db: Session, user: User, period: Period) -> dict:
    from app.models.control import Control, ControlAssessment, ControlGap
    from app.risk_engine.methodology import get_methodology_config

    assessments = [a for a in population(db, user, period) if band_of(a) in HIGH_BANDS]
    ids = [a.id for a in assessments]
    config = get_methodology_config(db)

    factors_by: dict[int, list] = defaultdict(list)
    for factor in _current_factors(db, ids):
        if factor.applicable and not factor.excluded:
            factors_by[factor.assessment_id].append(factor)

    controls_by: dict[int, list] = defaultdict(list)
    evaluations: dict[int, ControlAssessment] = {}
    gaps_by: dict[int, list] = defaultdict(list)
    if ids:
        controls = db.query(Control).filter(Control.assessment_id.in_(ids), Control.is_current.is_(True)).all()
        for control in controls:
            controls_by[control.assessment_id].append(control)
        control_ids = [c.id for c in controls]
        if control_ids:
            for evaluation in db.query(ControlAssessment).filter(
                ControlAssessment.control_id.in_(control_ids), ControlAssessment.is_current.is_(True)
            ):
                evaluations[evaluation.control_id] = evaluation
        for gap in db.query(ControlGap).filter(ControlGap.assessment_id.in_(ids), ControlGap.resolved.is_(False)):
            gaps_by[gap.assessment_id].append(gap)
    actions_by = _open_actions(db, ids)

    items = []
    for assessment in sorted(assessments, key=lambda a: (-BAND_ORDER.index(band_of(a)), -(score_of(a) or 0))):
        risks = sorted(
            (
                {
                    "category": f.category,
                    "band": _factor_band(f, config),
                    "score": f.score,
                    "rationale": f.rationale,
                }
                for f in factors_by[assessment.id]
            ),
            key=lambda r: (-BAND_ORDER.index(r["band"]) if r["band"] in BAND_ORDER else 0, -(r["score"] or 0)),
        )
        items.append(
            {
                **_ref(assessment),
                "product": assessment.product_or_service_name,
                "business_unit": business_unit_of(assessment),
                "legal_entity": assessment.legal_entity,
                "geographies": _split(assessment.countries_jurisdictions),
                "customer_segments": _split(assessment.customer_segment),
                "channels": _split(assessment.delivery_channels),
                "band": band_of(assessment),
                "score": score_of(assessment),
                "workflow_status": workflow.label_for(assessment.workflow_status),
                "risks": risks,
                "controls": [
                    {
                        "control_type": c.control_type,
                        "owner": c.owner,
                        "design_adequacy": evaluations[c.id].design_adequacy if c.id in evaluations else "NOT_ASSESSED",
                        "operating_effectiveness": evaluations[c.id].operating_effectiveness
                        if c.id in evaluations
                        else "UNVERIFIED",
                    }
                    for c in controls_by[assessment.id]
                ],
                "control_gaps": [{"gap_type": g.gap_type, "description": g.description} for g in gaps_by[assessment.id]],
                "open_actions": actions_by.get(assessment.id, []),
            }
        )

    geo_counter: Counter = Counter()
    for item in items:
        geo_counter.update(item["geographies"] or ["Unspecified"])
    risk_counter: Counter = Counter(r["category"] for item in items for r in item["risks"] if r["band"] in HIGH_BANDS)

    return {
        **_meta(user, period, len(items)),
        "summary": {
            "assessments": len(items),
            "critical": sum(1 for i in items if i["band"] == "CRITICAL"),
            "high": sum(1 for i in items if i["band"] == "HIGH"),
            "open_actions": sum(len(i["open_actions"]) for i in items),
            "overdue_actions": sum(1 for i in items for a in i["open_actions"] if a["overdue"]),
            "open_control_gaps": sum(len(i["control_gaps"]) for i in items),
        },
        "products": _counts(i["product"] or "Unspecified" for i in items),
        "geographies": [{"key": k, "label": k, "count": c} for k, c in geo_counter.most_common()],
        "top_risks": [{"key": k, "label": k.replace("_", " ").title(), "count": c} for k, c in risk_counter.most_common()],
        "assessments": items,
    }
