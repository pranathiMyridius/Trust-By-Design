"""
Stage 19 (Explainability): "distinguish facts, assumptions,
recommendations, and decisions" and "automated output shall be
reviewable and traceable".

Every statement returned here is tagged with its kind, whether a person
or the system produced it, who / which model, when, its review state, and
a reference back to the exact row it came from. Like explainability.py,
nothing is re-derived -- it only reads what earlier stages recorded.
"""

from sqlalchemy.orm import Session

from app.models.assessment import Assessment
from app.models.assessment_override import AssessmentOverride
from app.models.challenge_review import ChallengeFinding
from app.models.inherent_risk_calculation import InherentRiskCalculation
from app.models.risk_factor import RiskFactor

FACT = "FACT"
ASSUMPTION = "ASSUMPTION"
RECOMMENDATION = "RECOMMENDATION"
DECISION = "DECISION"

# P4 (R5.4): "clearly distinguish direct evidence, extracted information,
# system interpretation, analyst commentary and assumptions". A second,
# separate label on every statement -- the Stage 19 kind above says what a
# statement is for; this says what it rests on. Decisions are neither
# evidence nor interpretation and carry no evidence category.
DIRECT_EVIDENCE = "DIRECT_EVIDENCE"
EXTRACTED_INFORMATION = "EXTRACTED_INFORMATION"
SYSTEM_INTERPRETATION = "SYSTEM_INTERPRETATION"
ANALYST_COMMENTARY = "ANALYST_COMMENTARY"
EVIDENCE_ASSUMPTION = "ASSUMPTION"
EVIDENCE_CATEGORIES = {
    DIRECT_EVIDENCE: "Direct evidence: source material itself -- the request as stated, documents on file, "
    "verified quotes and approved-source passages.",
    EXTRACTED_INFORMATION: "Extracted information: values the system read out of documents, with their provenance.",
    SYSTEM_INTERPRETATION: "System interpretation: what the AI or a deterministic rule or calculation concluded.",
    ANALYST_COMMENTARY: "Analyst commentary: a person's own assessment, rating or added factor.",
    EVIDENCE_ASSUMPTION: "Assumption: provisional, unconfirmed or missing information the conclusion depends on.",
}

_INTAKE_FACTS = (
    ("legal_entity", "Legal entity"),
    ("business_unit", "Business unit"),
    ("customer_segment", "Customer segment"),
    ("countries_jurisdictions", "Countries and jurisdictions"),
    ("delivery_channels", "Delivery channels"),
    ("expected_transaction_volume", "Expected transaction volume"),
    ("expected_transaction_value", "Expected transaction value"),
    ("third_party_vendor_usage", "Third parties or vendors"),
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _statement(
    kind,
    text,
    *,
    origin,
    source,
    actor=None,
    recorded_at=None,
    reference_type=None,
    reference_id=None,
    model_version=None,
    review_status=None,
    basis=None,
    evidence_category=None,
    location=None,
):
    return {
        "kind": kind,
        "evidence_category": evidence_category,
        "location": location,
        "statement": text,
        # HUMAN = entered/decided by a named person; AUTOMATED = produced by
        # the AI or a deterministic calculation.
        "origin": origin,
        "source": source,
        "basis": basis,
        "actor": actor,
        "model_version": model_version,
        "recorded_at": _iso(recorded_at),
        # Automated output only: REVIEWED once a person has confirmed,
        # rated, edited or accepted it; PENDING_REVIEW until then.
        "review_status": review_status,
        "reference": {"type": reference_type, "id": reference_id} if reference_type else None,
    }


def classify_statements(db: Session, assessment_id: int, masked_document_ids: set[int] | None = None) -> dict | None:
    from app.governance import provenance as field_provenance
    from app.models.approved_source import SourceEvidenceLink
    from app.models.assessment_document import AssessmentDocument
    from app.models.assessment_draft import AssessmentDraft
    from app.models.assessment_intelligence import AssessmentIntelligence
    from app.models.committee_vote import CommitteeVote
    from app.models.user import User
    from app.services.advisory import ADVISORY_NOTICE, ensure_advisory_wording
    from app.services.data_masking import mask_sensitive_text
    from app.services.evidence_currency import evaluate as evaluate_document

    assessment = db.query(Assessment).filter(Assessment.id == assessment_id).first()
    if not assessment:
        return None

    masked_document_ids = masked_document_ids or set()
    statements: list[dict] = []

    def user_name(user_id):
        if user_id is None:
            return None
        user = db.query(User).filter(User.id == user_id).first()
        return (user.full_name or user.email) if user else f"user {user_id}"

    def quote_text(text, document_id):
        return (mask_sensitive_text(text) or "") if document_id in masked_document_ids else text

    # FACTS -- what the requester stated, and the evidence on file.
    for field, label in _INTAKE_FACTS:
        value = getattr(assessment, field, None)
        if value:
            statements.append(
                _statement(
                    FACT,
                    f"{label}: {value}",
                    origin="HUMAN",
                    source="Business request (as stated by the requester)",
                    actor=assessment.submitted_by,
                    recorded_at=assessment.created_at,
                    reference_type="assessment",
                    reference_id=assessment.id,
                    evidence_category=DIRECT_EVIDENCE,
                )
            )

    documents = (
        db.query(AssessmentDocument)
        .filter(
            AssessmentDocument.assessment_id == assessment_id,
            AssessmentDocument.is_current.is_(True),
        )
        .all()
    )
    from app.models.processing_job import ProcessingJob

    for document in documents:
        text = f"Evidence on file: {document.filename} ({document.document_type}, v{document.version})."
        # Stage 19 (Reliability): don't overstate evidence whose content
        # couldn't be (fully) extracted.
        job = (
            db.query(ProcessingJob)
            .filter(ProcessingJob.document_id == document.id)
            .order_by(ProcessingJob.id.desc())
            .first()
        )
        if job is not None and job.status == "FAILED":
            text += " Its content could not be extracted, so it has not been used in the analysis."
        elif job is not None and job.status == "PARTIAL":
            text += " Its content was only partially extracted."
        elif job is not None and job.status in ("QUEUED", "RUNNING"):
            text += " Its content is still being processed."
        # P4 (R2.6): say whether an expired document may be relied on.
        currency = evaluate_document(db, document)
        if currency["state"] == "ACKNOWLEDGEMENT_REQUIRED":
            text += f" Expired ({document.expiry_date}); not used until someone decides whether to rely on it."
        elif currency["state"] == "ACKNOWLEDGED_FOR_USE":
            text += f" Expired ({document.expiry_date}); used as evidence by acknowledgement: {currency['acknowledgement']['reason']}"
        elif currency["state"] == "EXCLUDED_FROM_EVIDENCE":
            text += f" Expired ({document.expiry_date}); excluded from evidence."
        statements.append(
            _statement(
                FACT,
                text,
                origin="HUMAN",
                source="Uploaded evidence",
                actor=document.document_owner,
                recorded_at=document.created_at,
                reference_type="document",
                reference_id=document.id,
                evidence_category=DIRECT_EVIDENCE,
            )
        )

    # P4 (R2.4 / R5.4): each extracted profile value, with its provenance.
    # Values the requester typed are already above as the request itself.
    intelligence = db.query(AssessmentIntelligence).filter(AssessmentIntelligence.assessment_id == assessment_id).first()
    if intelligence is not None:
        for field, record in field_provenance.load(intelligence.field_provenance).items():
            origin = record.get("origin")
            if origin == field_provenance.INTAKE_FORM:
                continue
            value = record.get("value")
            shown = ", ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            label = field.replace("_", " ").capitalize()
            location = {
                key: record.get(key)
                for key in ("document_id", "document_version", "filename", "page", "sheet")
                if record.get(key) is not None
            } or None
            if origin == field_provenance.USER_CORRECTION:
                by_owner = record.get("corrected_by_id") is not None and record.get("corrected_by_id") == assessment.owner_id
                statements.append(
                    _statement(
                        FACT,
                        f"{label}: {shown} (corrected by {record.get('corrected_by')}).",
                        origin="HUMAN",
                        source="Profile correction by the business owner" if by_owner else "Profile correction by a reviewer",
                        actor=record.get("corrected_by"),
                        recorded_at=None,
                        reference_type="assessment_intelligence",
                        reference_id=intelligence.id,
                        evidence_category=DIRECT_EVIDENCE if by_owner else ANALYST_COMMENTARY,
                    )
                )
                statements[-1]["recorded_at"] = record.get("corrected_at")
                continue
            confirmed = bool(intelligence.confirmed)
            statements.append(
                _statement(
                    FACT if confirmed else ASSUMPTION,
                    f"{label}: {shown}. Confidence {record.get('confidence')} -- {record.get('confidence_basis')}",
                    origin="AUTOMATED",
                    source="AI document extraction" if origin == field_provenance.AI_EXTRACTION else "Rule-based document extraction",
                    basis=record.get("verification"),
                    recorded_at=None,
                    reference_type="assessment_intelligence",
                    reference_id=intelligence.id,
                    review_status="REVIEWED" if confirmed else "PENDING_REVIEW",
                    evidence_category=EXTRACTED_INFORMATION,
                    location=location,
                )
            )
            statements[-1]["recorded_at"] = record.get("extracted_at")

    inherent = (
        db.query(InherentRiskCalculation)
        .filter(
            InherentRiskCalculation.assessment_id == assessment_id,
            InherentRiskCalculation.is_current.is_(True),
        )
        .first()
    )
    if inherent:
        inputs = inherent.get_inputs()
        rated = sum(1 for item in inputs if item.get("rated"))
        if inputs and rated == 0 and not inherent.overridden:
            text = f"No inherent risk score yet: none of {len(inputs)} applicable factors has been rated."
        else:
            text = f"Inherent risk calculated as {inherent.final_score} ({inherent.risk_band})."
        if inherent.is_provisional:
            text += (
                f" Provisional: {len(inputs) - rated} applicable factor(s) are not yet rated "
                "and are left out of the score until an analyst rates them."
            )
        for rule in inherent.get_triggered_rules():
            text += (
                f" Policy rule {rule.get('rule_code')} set the band to at least "
                f"{rule.get('min_band')} ({', '.join(rule.get('triggered_by') or [])})."
            )
        statements.append(
            _statement(
                ASSUMPTION if inherent.is_provisional else FACT,
                text,
                origin="AUTOMATED",
                source="Risk methodology calculation",
                basis=f"Weighted calculation, method '{inherent.calculation_method}'",
                recorded_at=getattr(inherent, "created_at", None),
                reference_type="inherent_risk_calculation",
                reference_id=inherent.id,
                review_status="REVIEWED" if inherent.overridden else "PENDING_REVIEW",
                evidence_category=EVIDENCE_ASSUMPTION if inherent.is_provisional else SYSTEM_INTERPRETATION,
            )
        )

    # Risk factors: AI-identified and not yet rated by a person are
    # assumptions; once rated (or added by an analyst) they are facts on
    # record; an exclusion is an analyst decision.
    factors = (
        db.query(RiskFactor)
        .filter(
            RiskFactor.assessment_id == assessment_id,
            RiskFactor.is_current.is_(True),
            RiskFactor.applicable.is_(True),
        )
        .all()
    )
    links_by_factor: dict[int, list] = {}
    for link in db.query(SourceEvidenceLink).filter(SourceEvidenceLink.assessment_id == assessment_id).all():
        links_by_factor.setdefault(link.risk_factor_id, []).append(link)

    for factor in factors:
        category = factor.category.replace("_", " ").title()
        if factor.excluded:
            statements.append(
                _statement(
                    DECISION,
                    f"{category} excluded from the assessment: {factor.exclusion_reason}",
                    origin="HUMAN",
                    source="Analyst exclusion",
                    actor=factor.excluded_by,
                    recorded_at=factor.excluded_at,
                    reference_type="risk_factor",
                    reference_id=factor.id,
                )
            )
            continue

        is_automated = factor.source in ("AI", "RULES")
        statements.append(
            _statement(
                ASSUMPTION if is_automated and factor.rated_by is None else FACT,
                f"{category} risk: {factor.severity} (score {factor.score:g}). {factor.rationale}",
                origin="AUTOMATED" if is_automated else "HUMAN",
                source=(
                    "AI risk identification" if factor.source == "AI"
                    else "Deterministic rule engine (AI unavailable)" if factor.source == "RULES"
                    else "Analyst-added risk factor"
                ),
                actor=factor.rated_by or factor.added_by,
                recorded_at=factor.rated_at or factor.created_at,
                reference_type="risk_factor",
                reference_id=factor.id,
                review_status=("REVIEWED" if factor.rated_by else "PENDING_REVIEW") if is_automated else None,
                evidence_category=SYSTEM_INTERPRETATION if is_automated else ANALYST_COMMENTARY,
            )
        )

        if is_automated and factor.rated_by:
            statements.append(
                _statement(
                    FACT,
                    f"{category} rated by an analyst: likelihood {factor.likelihood} x impact {factor.impact}"
                    + (f" ({factor.rating_source.replace('_', ' ').lower()})." if factor.rating_source else "."),
                    origin="HUMAN",
                    source="Analyst rating",
                    actor=factor.rated_by,
                    recorded_at=factor.rated_at,
                    reference_type="risk_factor",
                    reference_id=factor.id,
                    evidence_category=ANALYST_COMMENTARY,
                )
            )

        # P4 (Stage 4): a fixed rule that required this category.
        for trigger in factor.get_rule_triggers():
            signals = "; ".join(
                f"{signal.get('field')}: '{signal.get('context')}'" for signal in (trigger.get("signals") or [])[:4]
            )
            statements.append(
                _statement(
                    FACT,
                    f"{category} required by fixed Stage 4 rule {trigger.get('rule_id')} "
                    f"({trigger.get('description')}; {trigger.get('effect')}). Signals: {signals}.",
                    origin="AUTOMATED",
                    source="Fixed Stage 4 rule (signals provisional, pending business validation)",
                    basis=f"Ruleset {trigger.get('ruleset_version')}",
                    recorded_at=factor.created_at,
                    reference_type="risk_factor",
                    reference_id=factor.id,
                    evidence_category=SYSTEM_INTERPRETATION,
                )
            )

        # P4 (R5.4): the verified quotes the factor rests on are direct
        # evidence; rejected ones support nothing and are not listed.
        for record in factor.get_evidence():
            if not record.get("quote_verified"):
                continue
            location = {
                key: record.get(key)
                for key in ("source_id", "document_id", "document_version", "page")
                if record.get(key) is not None
            }
            statements.append(
                _statement(
                    FACT,
                    f"{category} evidence: \"{quote_text(record.get('verbatim_quote') or '', record.get('document_id'))}\" "
                    f"({record.get('source_label') or record.get('source_id')}"
                    + (f", page {record['page']}" if record.get("page") else "")
                    + ").",
                    origin="AUTOMATED",
                    source="Verified quote" + (f" ({record.get('indicator')})" if record.get("indicator") else ""),
                    basis=record.get("verification"),
                    recorded_at=factor.created_at,
                    reference_type="risk_factor",
                    reference_id=factor.id,
                    evidence_category=DIRECT_EVIDENCE,
                    location=location,
                )
            )

        for link in links_by_factor.get(factor.id, []):
            statements.append(
                _statement(
                    FACT,
                    f"{category} evidence from '{link.source_title}' v{link.source_version}: \"{link.passage}\""
                    + (f" (outdated when attached; acknowledged: {link.outdated_acknowledgement_reason})" if link.outdated_at_attach else ""),
                    origin="HUMAN",
                    source="Approved source passage",
                    actor=link.retrieved_by,
                    recorded_at=link.retrieved_at,
                    reference_type="source_evidence_link",
                    reference_id=link.id,
                    evidence_category=DIRECT_EVIDENCE,
                    location={"source_id": link.source_id, "reference": link.reference},
                )
            )

        for missing in factor.get_missing_information():
            statements.append(
                _statement(
                    ASSUMPTION,
                    f"{category}: information not yet available -- {missing}",
                    origin="AUTOMATED",
                    source="Risk identification",
                    recorded_at=factor.created_at,
                    reference_type="risk_factor",
                    reference_id=factor.id,
                    review_status="REVIEWED" if factor.rated_by else "PENDING_REVIEW",
                    evidence_category=EVIDENCE_ASSUMPTION,
                )
            )

    # ASSUMPTIONS / RECOMMENDATIONS from the current draft.
    draft = (
        db.query(AssessmentDraft)
        .filter(
            AssessmentDraft.assessment_id == assessment_id,
            AssessmentDraft.is_current.is_(True),
        )
        .first()
    )
    if draft:
        automated = not draft.is_edited
        common = dict(
            origin="AUTOMATED" if automated else "HUMAN",
            actor=draft.edited_by or draft.generated_by,
            model_version=draft.model_version,
            recorded_at=draft.edited_at or draft.generated_at,
            reference_type="assessment_draft",
            reference_id=draft.id,
            review_status=("REVIEWED" if draft.accepted_by else "PENDING_REVIEW") if automated else None,
        )
        interpretation = SYSTEM_INTERPRETATION if automated else ANALYST_COMMENTARY
        for assumption in draft.get_assumptions():
            statements.append(
                _statement(ASSUMPTION, str(assumption), source="Assessment draft", evidence_category=EVIDENCE_ASSUMPTION, **common)
            )
        for missing in draft.get_missing_information():
            statements.append(
                _statement(
                    ASSUMPTION,
                    f"Information not yet available: {missing}",
                    source="Assessment draft",
                    evidence_category=EVIDENCE_ASSUMPTION,
                    **common,
                )
            )
        for condition in draft.get_recommended_conditions():
            statements.append(
                _statement(
                    RECOMMENDATION,
                    f"Recommended condition: {condition}",
                    source="Assessment draft",
                    evidence_category=interpretation,
                    **common,
                )
            )
        if draft.analyst_recommendation:
            statements.append(
                _statement(
                    RECOMMENDATION,
                    ensure_advisory_wording(draft.analyst_recommendation),
                    source="Automated draft recommendation" if automated else "Analyst recommendation",
                    basis=draft.generation_method,
                    evidence_category=interpretation,
                    **common,
                )
            )

    for finding in db.query(ChallengeFinding).filter(ChallengeFinding.assessment_id == assessment_id).all():
        statements.append(
            _statement(
                RECOMMENDATION,
                f"Challenge ({finding.severity}): {finding.description}",
                origin="AUTOMATED",
                source="Challenge review",
                recorded_at=finding.detected_at,
                reference_type="challenge_finding",
                reference_id=finding.id,
                review_status="PENDING_REVIEW" if finding.resolution_status in (None, "OPEN") else "REVIEWED",
                evidence_category=SYSTEM_INTERPRETATION,
            )
        )

    # DECISIONS -- only ever made by named people.
    for override in db.query(AssessmentOverride).filter(AssessmentOverride.assessment_id == assessment_id).all():
        statements.append(
            _statement(
                DECISION,
                f"Override of {override.section}.{override.field_name}: '{override.ai_value}' -> "
                f"'{override.human_value}'. Reason: {override.reason}",
                origin="HUMAN",
                source="Analyst override",
                actor=override.overridden_by,
                recorded_at=override.created_at,
                reference_type="assessment_override",
                reference_id=override.id,
            )
        )

    if assessment.manager_decision:
        text = f"Manager decision: {assessment.manager_decision}."
        if assessment.manager_comment:
            text += f" {assessment.manager_comment}"
        statements.append(
            _statement(
                DECISION,
                text,
                origin="HUMAN",
                source="Manager review",
                actor=user_name(assessment.manager_decided_by_id),
                recorded_at=assessment.manager_decided_at,
                reference_type="assessment",
                reference_id=assessment.id,
            )
        )

    for vote in db.query(CommitteeVote).filter(CommitteeVote.assessment_id == assessment_id).all():
        statements.append(
            _statement(
                DECISION,
                f"Committee vote (v{vote.version or 1}{'' if vote.is_current else ', superseded'}): {vote.vote}."
                + (f" {vote.comment}" if vote.comment else "")
                + (f" Changed because: {vote.recast_reason}" if vote.recast_reason else ""),
                origin="HUMAN",
                source="Risk Committee vote",
                actor=vote.member_name or user_name(vote.member_id),
                recorded_at=vote.voted_at,
                reference_type="committee_vote",
                reference_id=vote.id,
            )
        )

    if assessment.committee_decision:
        statements.append(
            _statement(
                DECISION,
                f"Risk Committee decision: {assessment.committee_decision}.",
                origin="HUMAN",
                source="Risk Committee",
                actor=user_name(assessment.committee_decided_by_id),
                recorded_at=assessment.committee_decided_at,
                reference_type="assessment",
                reference_id=assessment.id,
            )
        )

    kinds = (FACT, ASSUMPTION, RECOMMENDATION, DECISION)
    return {
        "assessment_id": assessment_id,
        "advisory_notice": ADVISORY_NOTICE,
        "final_decision_recorded": bool(assessment.committee_decision),
        "counts": {kind: sum(1 for s in statements if s["kind"] == kind) for kind in kinds},
        "evidence_categories": EVIDENCE_CATEGORIES,
        "evidence_category_counts": {
            category: sum(1 for s in statements if s["evidence_category"] == category) for category in EVIDENCE_CATEGORIES
        },
        "automated_pending_review": sum(1 for s in statements if s["review_status"] == "PENDING_REVIEW"),
        "statements": statements,
    }
