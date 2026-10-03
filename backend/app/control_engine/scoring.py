"""
Deterministic translation of a risk's mapped controls into a residual-risk
reduction contribution. Mirrors the shape of app/risk_engine/scoring.py:
pure functions over plain data, no DB access.
"""

# Points contributed per applicable, non-excluded risk factor, based on the
# best (most protective) operating effectiveness among its current,
# evidenced controls. An ineffective or unverified control contributes
# nothing -- R7.6/AC4: such a control never lets its risk be treated as
# fully mitigated.
_EFFECTIVENESS_POINTS = {
    "EFFECTIVE": 4,
    "PARTIALLY_EFFECTIVE": 2,
    "INEFFECTIVE": 0,
    "UNVERIFIED": 0,
}

# R7.4: the API and UI record design as ADEQUATE / INADEQUATE; the AI
# design assessment uses DESIGN_ADEQUATE / DESIGN_INADEQUATE. Both mean the
# same, and both are stored in existing records, so both are recognised.
# (Before this, only DESIGN_INADEQUATE was checked, so a design marked
# INADEQUATE in the UI raised no gap and still earned a reduction.)
DESIGN_INADEQUATE_VALUES = {"INADEQUATE", "DESIGN_INADEQUATE"}


def is_design_inadequate(value) -> bool:
    return str(value or "").upper() in DESIGN_INADEQUATE_VALUES


# Same overall cap the legacy dimension-keyed heuristic used
# (app/api/assessments.py's old _get_control_reduction_for_challenge), kept
# so residual risk stays on the same 0-100 scale/thresholds.
MAX_CONTROL_REDUCTION = 30


def points_for_risk(control_assessments: list[dict]) -> int:
    """
    control_assessments: the current ControlAssessment (as a dict) for
    every control mapped to one risk factor. Returns the best point value
    among them; 0 if the list is empty or none qualify as EFFECTIVE (an
    unevidenced or incomplete-coverage/unavailable-data-dependent control
    never counts as EFFECTIVE regardless of its stated effectiveness).
    """

    best = 0

    for assessment in control_assessments:
        if not assessment:
            continue

        effectiveness = assessment.get("operating_effectiveness", "UNVERIFIED")
        operating_status = assessment.get("operating_status") or "ACTIVE"

        # R7.4/R7.3: an inadequately designed or inactive control earns no
        # reduction; a planned or partially implemented one at most
        # partial credit.
        if is_design_inadequate(assessment.get("design_adequacy")) or operating_status == "INACTIVE":
            continue
        if operating_status in {"PARTIALLY_IMPLEMENTED", "PLANNED"} and effectiveness == "EFFECTIVE":
            effectiveness = "PARTIALLY_EFFECTIVE"

        # R7.6: no evidence, incomplete coverage, or an unavailable-data
        # dependency all cap this control's contribution at
        # PARTIALLY_EFFECTIVE points at most, however it was rated.
        if (
            not assessment.get("has_evidence")
            or not assessment.get("coverage_complete", True)
            or assessment.get("depends_on_unavailable_data")
        ):
            effectiveness = (
                "PARTIALLY_EFFECTIVE"
                if effectiveness == "EFFECTIVE"
                else effectiveness
            )

        best = max(best, _EFFECTIVENESS_POINTS.get(effectiveness, 0))

    return best


def rating_for_risk(control_assessments: list[dict]) -> str:
    """
    One risk's control-effectiveness rating for the residual grid (see
    app/risk_engine/scoring.py::CONTROL_RATINGS), from the same points and
    downgrades as points_for_risk: EFFECTIVE only for an evidenced,
    complete-coverage, effective control; PARTIAL for partial protection;
    WEAK for ineffective, unverified or no controls at all.
    """

    points = points_for_risk(control_assessments)
    if points >= _EFFECTIVENESS_POINTS["EFFECTIVE"]:
        return "EFFECTIVE"
    if points >= _EFFECTIVENESS_POINTS["PARTIALLY_EFFECTIVE"]:
        return "PARTIAL"
    return "WEAK"


_RATING_ORDER = ["WEAK", "PARTIAL", "EFFECTIVE"]


def overall_control_rating(ratings: list[str]) -> str | None:
    """
    The assessment's control rating is its weakest risk's rating: one
    uncontrolled risk is not offset by others being well controlled.
    None when there are no risks to rate.
    """

    if not ratings:
        return None
    return min(ratings, key=_RATING_ORDER.index)


def calculate_control_reduction(points_per_risk: list[int]) -> float:
    total = sum(points_per_risk)
    return float(min(total, MAX_CONTROL_REDUCTION))
