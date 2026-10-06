"""
LangGraph integration of the Source Library: approved passages go to the
model as REFERENCE material, its citations are verified verbatim, invented
or unsupported ones are rejected, and none of it can change an indicator,
an evidence status, a rating or a score -- a human still rates every factor.
Real workflow, database and rule engine; fake LLM whose answers are scripted
per test.
"""

from __future__ import annotations

import json
import re
import uuid

import pytest

from app.database import SessionLocal
from app.langgraph.service import run_risk_assessment_workflow
from app.models.audit_event import AuditEvent
from app.models.risk_factor import RiskFactor
from tests.api.test_source_library_governance import BASE, act, approve_flow, ids, new_source, unique_pdf
from tests.conftest import ok
from tests.support.fake_llm import chat, default_reply, factors_for, prompt_of

PASSAGE = re.compile(r'<passage id="([^"]+)" source="([^"]+)" title="([^"]*)" version="([^"]*)"[^>]*>\n(.*?)\n</passage>', re.S)

# Wording shared with the FULL_REQUEST intake so the keyword retrieval finds it.
REQUIREMENT = (
    "Regulated entities shall complete customer due diligence before remote onboarding of merchants "
    "and shall apply enhanced due diligence to cross-border settlement through an external processor."
)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def library_source(client, auth):
    """An approved source whose text matches the test assessment."""

    marker = f"ctn{uuid.uuid4().hex[:8]}"
    pdf = unique_pdf(f"CHAPTER 4 REMOTE ONBOARDING\n{REQUIREMENT} {marker}", marker=marker)
    record = approve_flow(client, auth, new_source(client, auth("policy"), pdf=pdf, title=f"RBI KYC Direction {marker}", jurisdiction="Global", version_label="2025 update"))
    yield record
    client.post(f"{BASE}/records/{record['id']}/retire", json={"reason": "Test finished; withdrawing."}, headers=auth("policy"))


def scripted(fake_llm, script):
    """Replace the model with `script(prompt, factors, passages)`; returns
    the list of prompts the model was sent."""

    prompts: list[str] = []

    def transport(url, payload):
        prompt = prompt_of(payload)
        if "Work through EVERY one of the 10 risk categories" not in prompt:
            return default_reply(url, payload)
        prompts.append(prompt)
        passages = {pid: {"source": src, "title": title, "version": version, "text": text} for pid, src, title, version, text in PASSAGE.findall(prompt)}
        factors = factors_for(prompt)
        script(prompt, factors, passages)
        return chat(json.dumps({"factors": factors}))

    fake_llm.transport = transport
    return prompts


def current_factors(db, assessment_id):
    db.expire_all()
    return db.query(RiskFactor).filter(RiskFactor.assessment_id == assessment_id, RiskFactor.is_current.is_(True)).all()


def run(db, create_assessment):
    assessment_id = create_assessment()["id"]
    run_risk_assessment_workflow(assessment_id, db)
    return assessment_id, {f.category: f for f in current_factors(db, assessment_id)}


def snapshot(factors):
    return {c: (f.applicable, tuple(f.get_indicators()), f.evidence_status, f.score, f.likelihood, f.impact, f.rating_source) for c, f in factors.items()}


def quote_from(passage_text: str, length: int = 70) -> str:
    start = passage_text.index("Regulated")
    return passage_text[start : start + length]


# -- what the model is given -----------------------------------------------------------


def test_approved_passages_are_given_to_the_model_as_reference(db, create_assessment, fake_llm, library_source):
    prompts = scripted(fake_llm, lambda *_: None)
    run(db, create_assessment)
    prompt = prompts[0]
    assert "REFERENCE LIBRARY" in prompt and library_source["source_code"] in prompt and "2025 update" in prompt
    assert "CHAPTER 4" in prompt  # the section travels with the passage
    assert "NOT facts about this change" in prompt and "never use them to justify an indicator" in prompt


def test_unapproved_and_non_applicable_sources_are_never_given_to_the_model(client, auth, db, create_assessment, fake_llm):
    secret = f"draftonly{uuid.uuid4().hex[:8]}"
    draft = new_source(client, auth("policy"), pdf=unique_pdf(f"{REQUIREMENT} {secret}", marker=secret), title=f"Draft {secret}")
    in_review = new_source(client, auth("policy"), pdf=unique_pdf(f"{REQUIREMENT} {secret}b", marker=secret + "b"), title=f"Review {secret}")
    ok(act(client, auth("policy"), *ids(in_review), "submit"))
    foreign = approve_flow(client, auth, new_source(client, auth("policy"), pdf=unique_pdf(f"{REQUIREMENT} {secret}c", marker=secret + "c"), title=f"Foreign {secret}", jurisdiction="Brazil"))
    prompts = scripted(fake_llm, lambda *_: None)
    run(db, create_assessment)
    assert secret not in prompts[0]  # draft, in review, and an approved source for a jurisdiction that doesn't apply
    for record in (draft, in_review, foreign):
        client.post(f"{BASE}/records/{record['id']}/retire", json={"reason": "Test finished; withdrawing."}, headers=auth("policy"))


def test_without_a_library_the_prompt_has_no_reference_section(db, create_assessment, fake_llm, monkeypatch):
    # Other tests in the session approve library sources; this one needs it empty.
    monkeypatch.setattr("app.services.source_citations.build_library_sources", lambda *_a, **_k: {})
    prompts = scripted(fake_llm, lambda *_: None)
    run(db, create_assessment)
    assert "<passage " not in prompts[0]


# -- citations are verified, never trusted ------------------------------------------------------


def cite_all(prompt, factors, passages):
    library_id, passage = next(iter(passages.items()))
    for factor in factors:
        if factor["applicable"]:
            factor["source_citations"] = [
                {"source_id": library_id, "quote": quote_from(passage["text"]), "relevance": "Sets the due diligence standard."},
                {"source_id": library_id, "quote": "Regulated entities must freeze all accounts on any alert."},  # not in the passage
                {"source_id": "LIB:99", "quote": "A source that was never offered to the model."},
                {"source_id": library_id, "quote": "short"},
            ]
    for factor in factors:
        if not factor["applicable"]:
            factor["source_citations"] = [{"source_id": library_id, "quote": quote_from(passage["text"])}]


def test_verbatim_citations_are_kept_with_full_source_detail_and_the_rest_rejected(db, create_assessment, fake_llm, library_source):
    scripted(fake_llm, cite_all)
    _, factors = run(db, create_assessment)
    # (A category forced applicable by a fixed Stage 4 rule, not by the model,
    # has no model citations to keep.)
    cited = [f for f in factors.values() if f.get_source_citations()]
    assert cited
    for factor in cited:
        citations = factor.get_source_citations()
        verified = [c for c in citations if c["quote_verified"]]
        assert len(verified) == 1
        good = verified[0]
        assert good["source_code"] == library_source["source_code"]
        assert good["source_title"] == library_source["title"] and good["version_label"] == "2025 update"
        assert good["page_start"] == 1 and "CHAPTER 4" in good["section"]
        assert good["verification"] == "EXACT_VERIFIED" and good["quote"].startswith("Regulated entities")
        assert good["version_id"] and good["passage_sha256"] and good["ai_relevance_note"] == "Sets the due diligence standard."
        by_reason = {c["verification"] for c in citations if not c["quote_verified"]}
        assert by_reason == {"NOT_FOUND", "UNKNOWN_SOURCE", "TOO_SHORT"}  # the model's inventions are on record, rejected
    # A category the model found not applicable carries no citations at all.
    assert all(not f.get_source_citations() for f in factors.values() if not f.applicable)


def test_citations_never_change_indicators_status_or_scores(db, create_assessment, fake_llm, client, auth, library_source):
    scripted(fake_llm, lambda *_: None)
    _, plain = run(db, create_assessment)
    scripted(fake_llm, cite_all)
    _, cited = run(db, create_assessment)
    assert snapshot(cited) == snapshot(plain)
    # Every factor is still unrated: only a human's likelihood x impact scores it.
    assert all(f.likelihood is None and f.impact is None and f.score == 0 and f.rating_source is None for f in cited.values())


def test_library_text_cannot_be_used_as_evidence_for_an_indicator(db, create_assessment, fake_llm, library_source):
    def abuse(prompt, factors, passages):
        library_id, passage = next(iter(passages.items()))
        for factor in factors:
            if factor["category"] == "GEOGRAPHIC_RISK":
                factor.update(applicable=True, indicators=["SANCTIONS_EXPOSURE"],
                              evidence=[{"source_id": library_id, "quote": quote_from(passage["text"]), "indicator": "SANCTIONS_EXPOSURE"}])

    scripted(fake_llm, abuse)
    _, factors = run(db, create_assessment)
    geo = factors["GEOGRAPHIC_RISK"]
    assert "SANCTIONS_EXPOSURE" not in geo.get_indicators()  # a regulation's wording is not a fact about this change
    assert geo.get_rejected_indicators() and geo.evidence_status != "EVIDENCE_FOUND" or "SANCTIONS_EXPOSURE" not in geo.get_indicators()
    assert all(e["verification"] == "UNKNOWN_SOURCE" for e in geo.get_evidence() if e["source_id"].startswith("LIB:"))


def test_a_claim_about_a_source_without_a_verified_citation_is_flagged(db, create_assessment, fake_llm, library_source):
    def claim(prompt, factors, passages):
        for factor in factors:
            if factor["applicable"]:
                factor["rationale"] = f"{library_source['title']} requires enhanced due diligence for this product."
                factor["source_citations"] = []

    scripted(fake_llm, claim)
    _, factors = run(db, create_assessment)
    flagged = [f for f in factors.values() if f.applicable and any("without a verified quote" in m for m in f.get_missing_information())]
    assert flagged and all(library_source["source_code"] in " ".join(f.get_missing_information()) for f in flagged)


def test_a_verified_citation_clears_the_unsupported_claim_warning(db, create_assessment, fake_llm, library_source):
    def claim_and_cite(prompt, factors, passages):
        library_id, passage = next(iter(passages.items()))
        for factor in factors:
            if factor["applicable"]:
                factor["rationale"] = f"{library_source['title']} sets the standard."
                factor["source_citations"] = [{"source_id": library_id, "quote": quote_from(passage["text"])}]

    scripted(fake_llm, claim_and_cite)
    _, factors = run(db, create_assessment)
    assert not any("without a verified quote" in m for f in factors.values() for m in f.get_missing_information())


def test_malformed_citations_are_ignored_not_fatal(db, create_assessment, fake_llm, library_source):
    def junk(prompt, factors, passages):
        for factor in factors:
            factor["source_citations"] = "not a list" if factor["category"].startswith("P") else [None, 7, {"quote": "x"}, {"source_id": "LIB:1"}]

    scripted(fake_llm, junk)
    _, factors = run(db, create_assessment)
    assert len(factors) == 10
    assert all(not c.get("quote_verified") for f in factors.values() for c in f.get_source_citations())


# -- audit, API and degradation ------------------------------------------------------------------


def test_the_analysis_audit_event_names_the_sources_supplied_and_the_citation_outcome(db, create_assessment, fake_llm, library_source):
    scripted(fake_llm, cite_all)
    assessment_id, _ = run(db, create_assessment)
    event = db.query(AuditEvent).filter(AuditEvent.assessment_id == assessment_id, AuditEvent.action == "ANALYSIS").one()
    assert "Source Library:" in event.details and library_source["source_code"] in event.details
    assert "citation(s) verified verbatim" in event.details and "rejected" in event.details


def test_citations_are_exposed_on_the_risk_factor_api(client, auth, db, create_assessment, fake_llm, library_source):
    scripted(fake_llm, cite_all)
    assessment_id, _ = run(db, create_assessment)
    rows = ok(client.get(f"/api/assessments/{assessment_id}/risk-factors", headers=auth("owner")))
    cited = [r for r in rows if r["source_citations"]]
    assert cited and any(c["quote_verified"] and c["source_title"] == library_source["title"] for r in cited for c in r["source_citations"])


def test_a_retrieval_failure_never_breaks_the_analysis(db, create_assessment, fake_llm, monkeypatch, library_source):
    from app.services import source_retrieval

    def boom(*args, **kwargs):
        raise RuntimeError("vector index offline")

    monkeypatch.setattr(source_retrieval, "retrieve", boom)
    prompts = scripted(fake_llm, lambda *_: None)
    _, factors = run(db, create_assessment)
    assert len(factors) == 10 and "<passage " not in prompts[0]


def test_a_superseded_version_is_never_the_one_cited(client, auth, db, create_assessment, fake_llm, library_source):
    rid = library_source["id"]
    new_marker = f"rev{uuid.uuid4().hex[:8]}"
    v2 = ok(client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "2026 update"},
                        files={"file": ("v2.pdf", unique_pdf(f"CHAPTER 4 REMOTE ONBOARDING\n{REQUIREMENT} {new_marker}", marker=new_marker), "application/pdf")},
                        headers=auth("policy")), 201)["pending_version"]["id"]
    ok(act(client, auth("policy"), rid, v2, "submit"))
    prompts = scripted(fake_llm, lambda *_: None)
    run(db, create_assessment)
    assert "2025 update" in prompts[0] and new_marker not in prompts[0]  # under review: the approved v1 is still what's cited
    ok(act(client, auth("compliance2"), rid, v2, "approve", comment="Verified the 2026 text in full."))
    prompts = scripted(fake_llm, lambda *_: None)
    run(db, create_assessment)
    reference = prompts[0].split("REFERENCE LIBRARY")[1]
    assert 'version="2026 update"' in reference and new_marker in reference and 'version="2025 update"' not in reference
