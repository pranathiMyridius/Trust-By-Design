from datetime import datetime, timezone


def generate_reference_id(assessment_id: int) -> str:
    """
    Generates the case reference id for an assessment, e.g.
    "RAW-2026-00007" (RiskAssessmentWorkbench). Derived directly from the
    assessment's own (already-unique, sequential) primary key rather than
    a separate counter query, so it can never collide or race under
    concurrent creates — the same pattern app/api/assessments.py already
    uses for AssessmentChallenge.challenge_id (f"CHL-{assessment_id:04d}").
    The year reflects when the id was generated (creation time), purely
    as a human-readable grouping — the sequence number itself does not
    reset each year.
    """

    year = datetime.now(timezone.utc).year
    return f"RAW-{year}-{assessment_id:05d}"
