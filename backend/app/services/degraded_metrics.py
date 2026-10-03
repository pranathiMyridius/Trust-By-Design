"""
Observability for degraded risk analysis.

A rising rules-only rate is rarely a risk-management fact and almost
always an operations one -- an expired API key, an exhausted quota, a
provider incident, a model that started returning unparseable output. It
needs to be visible as a number, not discovered when an analyst mentions
that the banner has been up for a week.

Everything here is derived from rows the system already writes:
AIUsageLog (one row per outbound provider call, see app/ai/metering.py)
and the analysis-mode columns on Assessment. Nothing new is counted at
request time, so this cannot itself fail an analysis.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_metrics import AIUsageLog
from app.models.assessment import Assessment
from app.risk_engine.degraded import AssessmentMode

PURPOSE = "RISK_FACTOR_IDENTIFICATION"

# Share of recent analyses running rules-only above which this is an
# incident rather than noise. Deliberately a default, not a constant:
# what counts as alarming depends on volume.
RULES_ONLY_ALERT_THRESHOLD = float(
    os.getenv("RULES_ONLY_ALERT_THRESHOLD") or 0.2
)


def _rate(part: int, whole: int) -> float | None:
    if not whole:
        return None
    return round(part / whole, 4)


def degraded_summary(db: Session, window_hours: int = 24) -> dict[str, Any]:
    """
    AI reliability and fallback usage over the last `window_hours`.

    `alert` is True when the rules-only share of analyses in the window
    crosses RULES_ONLY_ALERT_THRESHOLD -- a prompt to check the API key,
    the quota and the provider's status page, in that order.
    """

    since = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    # --- provider call outcomes -------------------------------------
    calls = (
        db.query(AIUsageLog)
        .filter(
            AIUsageLog.purpose == PURPOSE,
            AIUsageLog.created_at >= since,
        )
        .all()
    )

    total_calls = len(calls)
    successful_calls = sum(1 for call in calls if call.success)
    rate_limited = sum(1 for call in calls if call.http_status == 429)
    # A transport-level failure (timeout, DNS, TLS) has no HTTP status.
    transport_failures = sum(
        1 for call in calls if not call.success and call.http_status is None
    )
    durations = [call.duration_ms for call in calls if call.duration_ms]

    # --- analysis outcomes ------------------------------------------
    mode_counts = dict(
        db.query(Assessment.assessment_mode, func.count(Assessment.id))
        .filter(
            Assessment.assessment_mode.isnot(None),
            Assessment.analysis_mode_at >= since,
        )
        .group_by(Assessment.assessment_mode)
        .all()
    )

    ai_assisted = mode_counts.get(AssessmentMode.AI_ASSISTED, 0)
    rules_only = mode_counts.get(AssessmentMode.RULES_ONLY, 0)
    unavailable = mode_counts.get(AssessmentMode.UNAVAILABLE, 0)
    analysed = ai_assisted + rules_only + unavailable

    # Of the runs where the AI failed, how often did the deterministic
    # fallback actually rescue the analysis? This is the number that says
    # whether the fallback is doing its job.
    degraded_runs = rules_only + unavailable
    fallback_success_rate = _rate(rules_only, degraded_runs)

    rules_only_rate = _rate(rules_only, analysed)

    # --- how much degraded work is still sitting unreviewed ----------
    awaiting_acknowledgement = (
        db.query(func.count(Assessment.id))
        .filter(
            Assessment.requires_human_review.is_(True),
            Assessment.degraded_acknowledged_at.is_(None),
            Assessment.assessment_mode == AssessmentMode.RULES_ONLY,
        )
        .scalar()
    ) or 0

    acknowledged = (
        db.query(func.count(Assessment.id))
        .filter(
            Assessment.requires_human_review.is_(True),
            Assessment.degraded_acknowledged_at.isnot(None),
        )
        .scalar()
    ) or 0

    return {
        "window_hours": window_hours,
        "ai_calls": {
            "total": total_calls,
            "successful": successful_calls,
            "success_rate": _rate(successful_calls, total_calls),
            "rate_limited": rate_limited,
            "transport_failures": transport_failures,
            "mean_duration_ms": (
                int(sum(durations) / len(durations)) if durations else None
            ),
        },
        "analyses": {
            "total": analysed,
            "ai_assisted": ai_assisted,
            "rules_only": rules_only,
            "unavailable": unavailable,
            "rules_only_rate": rules_only_rate,
            "fallback_success_rate": fallback_success_rate,
        },
        "review_queue": {
            "awaiting_acknowledgement": awaiting_acknowledgement,
            "acknowledged": acknowledged,
        },
        "alert": bool(
            rules_only_rate is not None
            and rules_only_rate > RULES_ONLY_ALERT_THRESHOLD
        ),
        "alert_threshold": RULES_ONLY_ALERT_THRESHOLD,
    }
