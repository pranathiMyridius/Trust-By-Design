"""
R7.6: deterministic control-gap detection.

detect_gaps() is given, for one assessment, the applicable/non-excluded
RiskFactor rows and (for each) their current Control rows plus each
control's current ControlAssessment (if any), and returns a flat list of
gap dicts ready to persist as ControlGap rows. Pure/stateless so it's
easy to test and reuse from both the API layer and the advance-stage
integration in api/assessments.py.
"""

from dataclasses import dataclass

from app.control_engine.scoring import is_design_inadequate


# R7.3/R7.6: operating statuses that mean the control isn't fully in place.
NOT_FULLY_IMPLEMENTED = {"PARTIALLY_IMPLEMENTED", "PLANNED"}


@dataclass
class ControlWithAssessment:
    control_id: int
    current_assessment: dict | None  # {"design_adequacy", "operating_effectiveness",
    # "has_evidence", "coverage_complete", "depends_on_unavailable_data"}


@dataclass
class GapCandidate:
    gap_type: str
    description: str
    risk_factor_id: int | None
    control_id: int | None


def detect_gaps_for_risk(
    risk_factor_id: int,
    controls: list[ControlWithAssessment],
) -> list[GapCandidate]:
    gaps: list[GapCandidate] = []

    # R7.6: a risk with no mapped control at all.
    if not controls:
        gaps.append(
            GapCandidate(
                gap_type="NO_CONTROL",
                description="No control has been mapped to this risk.",
                risk_factor_id=risk_factor_id,
                control_id=None,
            )
        )
        return gaps

    for control in controls:
        assessment = control.current_assessment

        # R7.4: a control that isn't designed to address the risk doesn't
        # mitigate it -- whatever its operating record, and whether or not
        # there is operating evidence yet (design is assessed on its own).
        if assessment is not None and is_design_inadequate(assessment.get("design_adequacy")):
            gaps.append(
                GapCandidate(
                    gap_type="INEFFECTIVE",
                    description="This control's design is inadequate for the risk it is mapped to.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )

        # R7.6/AC3: no supporting evidence -> unverified, not a free pass.
        if assessment is None or not assessment.get("has_evidence"):
            gaps.append(
                GapCandidate(
                    gap_type="NO_EVIDENCE",
                    description="This control has no supporting evidence; its "
                    "effectiveness is unverified.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )
            continue

        effectiveness = assessment.get("operating_effectiveness")
        operating_status = assessment.get("operating_status") or "ACTIVE"

        # R7.3/R7.6: a control that isn't running doesn't mitigate either.
        if operating_status == "INACTIVE":
            gaps.append(
                GapCandidate(
                    gap_type="INEFFECTIVE",
                    description="This control is inactive.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )
        elif operating_status in NOT_FULLY_IMPLEMENTED:
            gaps.append(
                GapCandidate(
                    gap_type="PARTIAL",
                    description=(
                        "This control is only partially implemented."
                        if operating_status == "PARTIALLY_IMPLEMENTED"
                        else "This control is planned and not yet in operation."
                    ),
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )

        if effectiveness == "INEFFECTIVE":
            gaps.append(
                GapCandidate(
                    gap_type="INEFFECTIVE",
                    description="This control was assessed as ineffective.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )
        elif effectiveness == "PARTIALLY_EFFECTIVE":
            gaps.append(
                GapCandidate(
                    gap_type="PARTIAL",
                    description="This control is only partially effective.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )

        if not assessment.get("coverage_complete", True):
            gaps.append(
                GapCandidate(
                    gap_type="INCOMPLETE_COVERAGE",
                    description="This control does not cover the full risk.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )

        if assessment.get("depends_on_unavailable_data"):
            gaps.append(
                GapCandidate(
                    gap_type="UNAVAILABLE_DATA_DEPENDENCY",
                    description="This control depends on data that is not "
                    "currently available.",
                    risk_factor_id=risk_factor_id,
                    control_id=control.control_id,
                )
            )

    return gaps
