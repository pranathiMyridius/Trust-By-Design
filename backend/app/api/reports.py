"""
Stage 17: Reporting and Monitoring endpoints. Report content is built
in app/services/reporting.py; this module handles the date range and
who may see what.

Access:
  * Operational, risk, governance reports and the high-risk portfolio:
    FCRM Analyst, Manager, Committee Member, Admin -- each over the
    assessments they can already see (a Manager their team's, a
    Committee Member committee-visible ones).
  * AI evaluation: FCRM Analyst, Manager, Admin.
  * Business Users have no reporting access.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.dependencies import require_role
from app.database import get_db
from app.models.user import User, UserRole
from app.services import reporting

router = APIRouter(prefix="/api/reports", tags=["Reports"])

# R15.1: the Read-only Executive and the Auditor read reports too.
require_report_access = require_role(
    UserRole.FCRM_ANALYST,
    UserRole.MANAGER,
    UserRole.COMMITTEE_MEMBER,
    UserRole.ADMIN,
    UserRole.AUDITOR,
    UserRole.EXECUTIVE,
)
require_ai_report_access = require_role(UserRole.FCRM_ANALYST, UserRole.MANAGER, UserRole.ADMIN)


def get_period(
    date_from: date | None = Query(None, description="Inclusive start date (YYYY-MM-DD)"),
    date_to: date | None = Query(None, description="Inclusive end date (YYYY-MM-DD)"),
) -> reporting.Period:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to.")
    return reporting.Period(date_from=date_from, date_to=date_to)


@router.get("/operational")
def operational(
    period: reporting.Period = Depends(get_period),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_report_access),
):
    report = reporting.operational_report(db, current_user, period)
    db.commit()  # persist any lazily-initialised workflow state
    return report


@router.get("/risk")
def risk(
    period: reporting.Period = Depends(get_period),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_report_access),
):
    report = reporting.risk_report(db, current_user, period)
    db.commit()
    return report


@router.get("/governance")
def governance(
    period: reporting.Period = Depends(get_period),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_report_access),
):
    report = reporting.governance_report(db, current_user, period)
    db.commit()
    return report


@router.get("/ai-evaluation")
def ai_evaluation(
    period: reporting.Period = Depends(get_period),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_ai_report_access),
):
    return reporting.ai_evaluation_report(db, current_user, period)


@router.get("/high-risk-portfolio")
def high_risk_portfolio(
    period: reporting.Period = Depends(get_period),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_report_access),
):
    report = reporting.high_risk_portfolio(db, current_user, period)
    db.commit()
    return report
