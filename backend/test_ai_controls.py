"""
Tests for the AI-assisted control assessment (Stage 7):

- the evidence check runs once per control type, not once per mapping
- accepting evidence starts a capped, labelled rating and never overrides an
  analyst's own
- AI design suggestions are made once per control type, skip assessed
  controls and earn no credit by themselves
- automatically mapped controls are the category's set plus a capped number of
  AI additions
- scores are labelled from the methodology's bands

Runs on its own in-memory SQLite database; it never touches risk.db.
"""

import pytest
from sqlalchemy import Column, Integer, Table, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.ai import evidence_checker, provider
from app.control_engine import scoring
from app.control_engine.library import (
    MAX_AI_ADDITIONS,
    MAX_CONTROLS_PER_RISK,
    SUGGESTED_CONTROLS_BY_CATEGORY,
    select_controls,
)
from app.database import Base
from app.models.assessment import Assessment
from app.models.assessment_document import AssessmentDocument
from app.models.control import Control, ControlAssessment
from app.models.control_evidence import ControlEvidenceLink
from app.models.risk_factor import RiskFactor
from app.models.user import User  # noqa: F401 -- registers the users table
from app.risk_engine.scoring import determine_risk_band
from app.services import control_ai_assessment as caa
from app.services import control_evidence_service as ces


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(provider, "API_KEY", "test", raising=False)

    # Tables the models point at by foreign key but these tests don't use are
    # stubbed for the test only, then removed so they can't clash with the
    # real models if another test module imports them.
    stubs = []
    for table in list(Base.metadata.tables.values()):
        for fk in table.foreign_keys:
            target, _, column = fk.target_fullname.partition(".")
            if target not in Base.metadata.tables:
                stubs.append(Table(target, Base.metadata, Column(column, Integer, primary_key=True)))

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        for stub in stubs:
            Base.metadata.remove(stub)


def _setup(db):
    assessment = Assessment(title="OmniBaaS", change_type="NEW_PRODUCT", description="d")
    db.add(assessment)
    db.flush()
    db.add(
        AssessmentDocument(
            assessment_id=assessment.id,
            filename="intake.docx",
            file_type="docx",
            extracted_text="Transaction Monitoring: rule-based",
            is_current=True,
            version=1,
        )
    )
    risks = []
    for category in ("PRODUCT_SERVICE_RISK", "GEOGRAPHIC_RISK", "DELIVERY_CHANNEL_RISK"):
        risk = RiskFactor(
            assessment_id=assessment.id,
            category=category,
            applicable=True,
            score=50,
            severity="MEDIUM",
            rationale=f"{category} rationale",
            is_current=True,
        )
        db.add(risk)
        risks.append(risk)
    db.flush()
    controls = []
    for risk, control_type in [
        (risks[0], "TRANSACTION_MONITORING"),
        (risks[1], "TRANSACTION_MONITORING"),
        (risks[2], "TRANSACTION_MONITORING"),
        (risks[0], "ENHANCED_DUE_DILIGENCE"),
    ]:
        control = Control(
            assessment_id=assessment.id, risk_factor_id=risk.id, control_type=control_type, is_current=True
        )
        db.add(control)
        controls.append(control)
    db.commit()
    return assessment, risks, controls


def _outcome(effectiveness="EFFECTIVE"):
    return {
        "results": [
            {
                "document_id": 1,
                "support_level": "PARTIAL",
                "confidence": "HIGH",
                "quote": "Transaction Monitoring: rule-based",
                "rationale": "r",
                "shortfalls": ["x"],
            }
        ],
        "suggested_effectiveness": effectiveness,
        "model": "m",
    }


def _accept(db, control, effectiveness="EFFECTIVE"):
    link = ControlEvidenceLink(
        assessment_id=control.assessment_id,
        control_id=control.id,
        document_id=1,
        document_version=1,
        support_level="PARTIAL",
        confidence="HIGH",
        quote="q",
        rationale="r",
        shortfalls="[]",
        suggested_effectiveness=effectiveness,
        status="SUGGESTED",
    )
    db.add(link)
    db.flush()
    wrote = ces.accept_link(db, control, link, "Analyst", 1)
    db.commit()
    return wrote


def _current(db, control):
    return db.query(ControlAssessment).filter_by(control_id=control.id, is_current=True).one()


def _points(assessment_row):
    return scoring.points_for_risk(
        [
            {
                "design_adequacy": assessment_row.design_adequacy,
                "operating_effectiveness": assessment_row.operating_effectiveness,
                "has_evidence": assessment_row.has_evidence,
                "coverage_complete": True,
                "depends_on_unavailable_data": False,
                "operating_status": "ACTIVE",
            }
        ]
    )


# --- evidence check ---------------------------------------------------------


def test_evidence_check_runs_once_per_control_type(db, monkeypatch):
    assessment, _, controls = _setup(db)
    calls = []

    def fake(control_type, description, rationale, documents):
        calls.append((control_type, rationale))
        return _outcome()

    monkeypatch.setattr(evidence_checker, "check_control_evidence", fake)
    summary = ces.run_evidence_check(db, assessment.id)

    assert len(calls) == 2  # four mappings, two control types
    assert summary["controls_checked"] == 4 and summary["controls_failed"] == 0
    rationale = dict(calls)["TRANSACTION_MONITORING"]
    assert "PRODUCT_SERVICE_RISK rationale" in rationale and "DELIVERY_CHANNEL_RISK rationale" in rationale
    assert {link.control_id for link in db.query(ControlEvidenceLink)} == {c.id for c in controls}


def test_a_failed_type_counts_all_its_mappings(db, monkeypatch):
    assessment, _, _ = _setup(db)
    monkeypatch.setattr(
        evidence_checker,
        "check_control_evidence",
        lambda t, d, r, docs: None if t == "ENHANCED_DUE_DILIGENCE" else _outcome(),
    )
    summary = ces.run_evidence_check(db, assessment.id)
    assert summary["controls_checked"] == 3 and summary["controls_failed"] == 1


# --- rating from accepted evidence -------------------------------------------


def test_accept_starts_a_rating_but_caps_ai_effective(db):
    _, _, controls = _setup(db)
    assert _accept(db, controls[0], "EFFECTIVE")
    row = _current(db, controls[0])
    assert row.has_evidence is True
    assert row.operating_effectiveness == "PARTIALLY_EFFECTIVE"  # the AI never grants EFFECTIVE
    assert row.effectiveness_rationale == ces.AI_RATING_NOTE
    assert _points(row) == 2


def test_accept_with_an_unverified_view_leaves_the_control_unverified(db):
    _, _, controls = _setup(db)
    _accept(db, controls[0], "UNVERIFIED")
    assert _current(db, controls[0]).operating_effectiveness == "UNVERIFIED"


def test_accept_never_overrides_an_analyst_rating(db):
    assessment, _, controls = _setup(db)
    db.add(
        ControlAssessment(
            control_id=controls[0].id,
            assessment_id=assessment.id,
            design_adequacy="ADEQUATE",
            operating_effectiveness="INEFFECTIVE",
            effectiveness_rationale="Failed last test",
            has_evidence=False,
            assessed_by="Analyst",
            version=1,
            is_current=True,
        )
    )
    db.commit()
    _accept(db, controls[0], "EFFECTIVE")
    row = _current(db, controls[0])
    assert row.operating_effectiveness == "INEFFECTIVE" and row.effectiveness_rationale == "Failed last test"
    assert row.has_evidence is True and row.version == 2


def test_accept_respects_an_explained_analyst_unverified(db):
    assessment, _, controls = _setup(db)
    db.add(
        ControlAssessment(
            control_id=controls[0].id,
            assessment_id=assessment.id,
            operating_effectiveness="UNVERIFIED",
            effectiveness_rationale="Waiting for test results",
            has_evidence=False,
            version=1,
            is_current=True,
        )
    )
    db.commit()
    _accept(db, controls[0], "PARTIALLY_EFFECTIVE")
    assert _current(db, controls[0]).operating_effectiveness == "UNVERIFIED"


def test_accept_after_an_ai_design_review_keeps_the_design(db):
    assessment, _, controls = _setup(db)
    db.add(
        ControlAssessment(
            control_id=controls[0].id,
            assessment_id=assessment.id,
            design_adequacy="ADEQUATE",
            design_rationale="AI-suggested design review: ok",
            operating_effectiveness="UNVERIFIED",
            has_evidence=False,
            assessed_by=caa.AI_ASSESSOR,
            version=1,
            is_current=True,
        )
    )
    db.commit()
    _accept(db, controls[0], "PARTIALLY_EFFECTIVE")
    row = _current(db, controls[0])
    assert row.design_adequacy == "ADEQUATE" and row.operating_effectiveness == "PARTIALLY_EFFECTIVE"
    assert row.version == 2


def test_accepting_twice_changes_nothing_the_second_time(db):
    _, _, controls = _setup(db)
    assert _accept(db, controls[0]) is True
    assert _accept(db, controls[0]) is False


# --- AI design suggestions ----------------------------------------------------


def test_design_suggestions_once_per_type_and_earn_no_credit(db, monkeypatch):
    assessment, _, controls = _setup(db)
    db.add(  # a human already assessed one mapping
        ControlAssessment(
            control_id=controls[0].id,
            assessment_id=assessment.id,
            design_adequacy="INADEQUATE",
            operating_effectiveness="UNVERIFIED",
            assessed_by="Analyst",
            version=1,
            is_current=True,
        )
    )
    db.commit()
    calls = []

    def fake(control_type, rationale, context):
        calls.append(control_type)
        return {"design_adequacy": "DESIGN_ADEQUATE", "rationale": "fits"}

    monkeypatch.setattr(caa, "assess_control_design", fake)
    result = caa.suggest_designs(db, assessment.id)

    assert sorted(calls) == ["ENHANCED_DUE_DILIGENCE", "TRANSACTION_MONITORING"]
    assert result["controls_suggested"] == 3  # the human-assessed mapping is left alone
    assert _current(db, controls[0]).design_adequacy == "INADEQUATE"
    assert _current(db, controls[0]).assessed_by == "Analyst"
    row = _current(db, controls[3])
    assert row.design_adequacy == "ADEQUATE"  # stored in the form's vocabulary
    assert row.assessed_by == caa.AI_ASSESSOR and row.has_evidence is False
    assert _points(row) == 0
    assert caa.suggest_designs(db, assessment.id)["status"] == "NOTHING_TO_DO"  # idempotent


def test_an_unusable_design_answer_is_skipped(db, monkeypatch):
    assessment, _, _ = _setup(db)
    monkeypatch.setattr(
        caa, "assess_control_design", lambda t, r, c: {"design_adequacy": "NOT_ASSESSED", "rationale": "fail"}
    )
    result = caa.suggest_designs(db, assessment.id)
    assert result["controls_suggested"] == 0 and result["controls_skipped"] == 4
    assert db.query(ControlAssessment).count() == 0


# --- automatic mapping ---------------------------------------------------------


def test_selection_starts_from_the_category_set_and_caps_ai_additions():
    chosen = select_controls("PRODUCT_SERVICE_RISK", ["VENDOR_DUE_DILIGENCE", "FRAUD_MONITORING", "TRANSACTION_MONITORING"])
    defaults = SUGGESTED_CONTROLS_BY_CATEGORY["PRODUCT_SERVICE_RISK"]
    assert chosen[: len(defaults)] == defaults
    assert chosen == defaults + ["VENDOR_DUE_DILIGENCE"][:MAX_AI_ADDITIONS]
    assert len(chosen) <= MAX_CONTROLS_PER_RISK


def test_selection_never_exceeds_the_cap_or_duplicates():
    for category, defaults in SUGGESTED_CONTROLS_BY_CATEGORY.items():
        chosen = select_controls(category, ["TRANSACTION_MONITORING", "VENDOR_DUE_DILIGENCE", "NOT_A_CONTROL"])
        assert len(chosen) <= MAX_CONTROLS_PER_RISK and len(set(chosen)) == len(chosen)
        assert "NOT_A_CONTROL" not in chosen


def test_selection_without_an_ai_answer_still_gives_a_starting_set():
    assert select_controls("GEOGRAPHIC_RISK", []) == SUGGESTED_CONTROLS_BY_CATEGORY["GEOGRAPHIC_RISK"]


def test_selection_for_an_unknown_category_uses_the_ai_answer():
    assert select_controls("SOMETHING_NEW", ["SANCTIONS_SCREENING", "FRAUD_MONITORING", "TRANSACTION_LIMITS", "KYC_CUSTOMER_DUE_DILIGENCE"]) == [
        "SANCTIONS_SCREENING",
        "FRAUD_MONITORING",
        "TRANSACTION_LIMITS",
    ]
    assert select_controls("SOMETHING_NEW", []) == []


# --- bands ---------------------------------------------------------------------


def test_methodology_bands_label_scores():
    default = [
        {"name": "LOW", "min": 0, "max": 39},
        {"name": "MEDIUM", "min": 40, "max": 59},
        {"name": "HIGH", "min": 60, "max": 79},
        {"name": "CRITICAL", "min": 80, "max": 100},
    ]
    assert determine_risk_band(36.0, default) == "LOW"
    assert determine_risk_band(40, default) == "MEDIUM"
    assert determine_risk_band(80, default) == "CRITICAL"
    custom = [
        {"name": "LOW", "min": 0, "max": 29},
        {"name": "MEDIUM", "min": 30, "max": 59},
        {"name": "HIGH", "min": 60, "max": 79},
        {"name": "CRITICAL", "min": 80, "max": 100},
    ]
    assert determine_risk_band(36.0, custom) == "MEDIUM"  # a configured methodology is honoured
