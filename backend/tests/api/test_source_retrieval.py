"""
Retrieval of Source Library passages for risk assessments: only approved,
in-effect, applicable versions; jurisdiction and category filters; ranking
(keyword, and vector when embeddings are on); graceful degradation.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from datetime import date, timedelta

import pytest

from app.ai.embeddings import EMBEDDING_DIM, EmbeddingError
from app.database import SessionLocal
from app.models.approved_source import ApprovedSource
from app.models.source_library import SourceChunk, SourceEmbedding, SourceRecord, SourceVersion
from app.services import source_documents, source_retrieval
from tests.api.test_source_library_governance import BASE, act, approve_flow, ids, new_source, unique_pdf
from tests.conftest import ok


def marked_pdf(marker: str, sentence: str) -> bytes:
    return unique_pdf(f"{sentence} {marker}", marker=marker)


def approved(client, auth, marker: str, sentence: str, **overrides) -> dict:
    return approve_flow(client, auth, new_source(client, auth("policy"), pdf=marked_pdf(marker, sentence), **overrides))


def passages(client, auth, query: str, who: str = "analyst", **params) -> list[dict]:
    # httpx sends a None parameter as an empty string; "not given" means omitted.
    params = {key: value for key, value in params.items() if value is not None}
    response = client.get(f"{BASE}/passages", params={"q": query, **params}, headers=auth(who))
    return ok(response)


def titles(rows: list[dict]) -> set[str]:
    return {row["title"] for row in rows}


# -- applicability -----------------------------------------------------------------------


def test_only_the_approved_version_is_retrievable(client, auth):
    marker = f"zq{uuid.uuid4().hex[:10]}"
    draft = new_source(client, auth("policy"), pdf=marked_pdf(marker, "Draft text about beneficial ownership"))
    assert passages(client, auth, marker) == []

    rid, vid = ids(draft)
    ok(act(client, auth("policy"), rid, vid, "submit"))
    assert passages(client, auth, marker) == []  # in review: still not usable

    ok(act(client, auth("compliance"), rid, vid, "reject", comment="Rejected: wrong source document."))
    assert passages(client, auth, marker) == []  # rejected

    ok(client.patch(f"{BASE}/records/{rid}/versions/{vid}", json={"version_label": "2"}, headers=auth("policy")))
    ok(act(client, auth("policy"), rid, vid, "submit"))
    ok(act(client, auth("compliance"), rid, vid, "approve", comment="Corrected and verified."))
    found = passages(client, auth, marker)
    assert found and found[0]["title"] == draft["title"] and found[0]["version_label"] == "2"


def test_a_pending_new_version_does_not_leak_while_the_approved_one_is_served(client, auth):
    old, new = f"oldmk{uuid.uuid4().hex[:8]}", f"newmk{uuid.uuid4().hex[:8]}"
    record = approved(client, auth, old, "Original requirement text")
    rid = record["id"]
    v2 = ok(client.post(f"{BASE}/records/{rid}/versions", data={"version_label": "v2"},
                        files={"file": ("v2.pdf", marked_pdf(new, "Revised requirement text"), "application/pdf")}, headers=auth("policy")), 201)["pending_version"]["id"]
    ok(act(client, auth("policy"), rid, v2, "submit"))
    assert passages(client, auth, old) and passages(client, auth, new) == []  # v1 served, v2 not yet

    ok(act(client, auth("compliance2"), rid, v2, "approve", comment="Revised text verified in full."))
    assert passages(client, auth, old) == []  # v1 is superseded: gone
    served = passages(client, auth, new)
    assert served and served[0]["version_label"] == "v2"


def test_retired_sources_are_not_retrievable(client, auth):
    marker = f"rt{uuid.uuid4().hex[:10]}"
    record = approved(client, auth, marker, "Retirement test requirement")
    assert passages(client, auth, marker)
    ok(client.post(f"{BASE}/records/{record['id']}/retire", json={"reason": "No longer applicable."}, headers=auth("policy")))
    assert passages(client, auth, marker) == []


def test_a_version_not_yet_in_effect_is_not_retrievable(client, auth):
    marker = f"fut{uuid.uuid4().hex[:9]}"
    future = (date.today() + timedelta(days=30)).isoformat()
    approved(client, auth, marker, "Future requirement", effective_date=future)
    assert passages(client, auth, marker) == []


def test_an_outdated_source_is_returned_but_flagged(client, auth):
    marker = f"old{uuid.uuid4().hex[:9]}"
    approved(client, auth, marker, "Stale guidance", review_date="2020-01-01")
    found = passages(client, auth, marker)
    assert found and found[0]["outdated"] is True


# -- jurisdiction and category ----------------------------------------------------------------


def test_jurisdiction_filter_applies_home_global_and_named_jurisdictions(client, auth):
    marker = f"jur{uuid.uuid4().hex[:9]}"
    for label, jurisdiction in (("home", "India"), ("eu", "European Union"), ("us", "United States"), ("de", "Germany"), ("glob", "Global"), ("none", "")):
        approved(client, auth, marker, f"Requirement for {label}", title=f"{label} {marker}", jurisdiction=jurisdiction)

    def got(jurisdictions):
        return {t.split()[0] for t in titles(passages(client, auth, marker, jurisdictions=jurisdictions, limit=25))}

    assert got(None) == {"home", "eu", "us", "de", "glob", "none"}  # no filter given
    assert got("") == {"home", "glob", "none"}  # home jurisdiction + global only
    assert got("Germany, Poland") == {"home", "glob", "none", "eu", "de"}  # a member state brings in the EU
    assert got("United States") == {"home", "glob", "none", "us"}  # ...and the US source only for a US assessment


def test_category_filter(client, auth):
    marker = f"cat{uuid.uuid4().hex[:9]}"
    approved(client, auth, marker, "Sanctions procedure", title=f"sanc {marker}", category="SANCTIONS_RESOURCE")
    approved(client, auth, marker, "Internal policy text", title=f"pol {marker}", category="INTERNAL_POLICY")
    only = passages(client, auth, marker, category="SANCTIONS_RESOURCE")
    assert {t.split()[0] for t in titles(only)} == {"sanc"}
    both = passages(client, auth, marker, category=["SANCTIONS_RESOURCE", "INTERNAL_POLICY"], limit=25)
    assert {t.split()[0] for t in titles(both)} == {"sanc", "pol"}


def test_approved_listing_honours_the_same_filters(client, auth):
    marker = f"lst{uuid.uuid4().hex[:9]}"
    approved(client, auth, marker, "x", title=f"a {marker}", jurisdiction="United States")
    approved(client, auth, marker, "y", title=f"b {marker}", jurisdiction="India")
    new_source(client, auth("policy"), title=f"c {marker}", jurisdiction="India")  # a draft
    everything = ok(client.get(f"{BASE}/approved", headers=auth("analyst")))
    mine = [r for r in everything if marker in r["title"]]
    assert {r["title"].split()[0] for r in mine} == {"a", "b"}
    assert all(r["current_version"]["status"] == "APPROVED" and r["pending_version"] is None for r in mine)
    india = [r for r in ok(client.get(f"{BASE}/approved", params={"jurisdictions": ""}, headers=auth("analyst"))) if marker in r["title"]]
    assert {r["title"].split()[0] for r in india} == {"b"}


def test_passages_carry_everything_needed_to_cite_them(client, auth):
    marker = f"cite{uuid.uuid4().hex[:8]}"
    pdf = unique_pdf(f"CHAPTER 3 SCREENING\nCustomers shall be screened {marker} against sanctions lists.", marker=marker)
    record = approve_flow(client, auth, new_source(client, auth("policy"), pdf=pdf, version_label="Rev 7"))
    hit = passages(client, auth, marker)[0]
    assert hit["source_code"] == record["source_code"] and hit["title"] == record["title"]
    assert hit["version_label"] == "Rev 7" and hit["authority"] == "Reserve Bank of India"
    assert hit["page_start"] == 1 and hit["location"].startswith("p. 1") and "CHAPTER 3" in hit["section"]
    assert marker in hit["text"] and hit["method"] == "keyword"


def test_query_validation(client, auth):
    assert client.get(f"{BASE}/passages", params={"q": "x"}, headers=auth("analyst")).status_code == 422
    assert client.get(f"{BASE}/passages", params={"q": "valid query", "limit": 500}, headers=auth("analyst")).status_code == 422
    assert client.get(f"{BASE}/passages", params={"q": "valid query"}).status_code == 401


# -- jurisdiction helper ----------------------------------------------------------------------


def test_applicable_jurisdictions():
    assert source_retrieval.applicable_jurisdictions("Germany, Poland") >= {"IN", "DE", "PL", "EU"}
    us_uk = source_retrieval.applicable_jurisdictions("USA and UK")
    assert {"US", "GB", "IN"} <= us_uk and "EU" not in us_uk
    assert source_retrieval.applicable_jurisdictions(None) == {"IN"}


# -- vector retrieval -------------------------------------------------------------------------


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Hash each word into a bucket: shared words mean a higher cosine."""

    vectors = []
    for text in texts:
        vector = [0.0] * EMBEDDING_DIM
        for word in text.lower().split():
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        vectors.append([x / norm for x in vector])
    return vectors


@pytest.fixture
def embeddings_on(monkeypatch):
    monkeypatch.setenv("SOURCE_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(source_documents, "embed_texts", fake_embed)
    monkeypatch.setattr(source_retrieval, "embed_texts", fake_embed)


def test_embeddings_are_stored_per_chunk_and_used_for_ranking(client, auth, embeddings_on):
    tag = uuid.uuid4().hex[:8]
    close = approved(client, auth, tag, f"wire transfers cross border payments originator information {tag}", title=f"close {tag}")
    far = approved(client, auth, tag, f"employee parking permits and canteen opening hours {tag}", title=f"far {tag}")
    assert close["current_version"]["embedding_status"] == "COMPLETE"

    db = SessionLocal()
    try:
        chunk_ids = [c.id for c in db.query(SourceChunk).filter(SourceChunk.version_id == uuid.UUID(close["current_version"]["id"]))]
        stored = db.query(SourceEmbedding).filter(SourceEmbedding.chunk_id.in_(chunk_ids)).all()
        assert len(stored) == len(chunk_ids) and stored[0].dimensions == EMBEDDING_DIM
    finally:
        db.close()

    ranked = passages(client, auth, f"cross border wire transfers originator information {tag}", limit=25)
    order = [t.split()[0] for t in [r["title"] for r in ranked] if tag in t]
    assert order[:2] == ["close", "far"] and ranked[0]["method"] == "vector"


def test_embedding_failure_degrades_to_keyword_search(client, auth, monkeypatch):
    monkeypatch.setenv("SOURCE_EMBEDDINGS_ENABLED", "true")

    def failing(texts):
        raise EmbeddingError("provider down")

    monkeypatch.setattr(source_documents, "embed_texts", failing)
    monkeypatch.setattr(source_retrieval, "embed_texts", failing)
    marker = f"deg{uuid.uuid4().hex[:9]}"
    record = approved(client, auth, marker, "Degraded mode requirement")  # the upload still succeeds
    assert record["current_version"]["embedding_status"] == "UNAVAILABLE"
    assert record["current_version"]["processing_status"] == "PROCESSED"
    found = passages(client, auth, marker)
    assert found and found[0]["method"] == "keyword"


def test_partial_embedding_marks_the_version_partial(client, auth, monkeypatch):
    monkeypatch.setenv("SOURCE_EMBEDDINGS_ENABLED", "true")
    monkeypatch.setattr(source_documents, "EMBED_BATCH", 1)
    calls = {"n": 0}

    def flaky(texts):
        calls["n"] += 1
        if calls["n"] > 1:
            raise EmbeddingError("rate limited")
        return fake_embed(texts)

    monkeypatch.setattr(source_documents, "embed_texts", flaky)
    long_text = "\n".join(f"SECTION {n} HEADING\n" + " ".join(["clause"] * 30) for n in range(1, 4))
    record = new_source(client, auth("policy"), pdf=unique_pdf(long_text))
    assert record["versions"][0]["chunk_count"] >= 3 and record["versions"][0]["embedding_status"] == "PARTIAL"


# -- migrated (text-only) sources -------------------------------------------------------------


def test_a_migrated_text_only_version_is_chunked_on_first_use(client, auth):
    marker = f"mig{uuid.uuid4().hex[:9]}"
    db = SessionLocal()
    try:
        legacy = ApprovedSource(title=f"Migrated {marker}", source_type="INTERNAL_POLICY", version="1", content=f"Legacy policy text {marker}.", status="APPROVED")
        db.add(legacy)
        db.flush()
        record = SourceRecord(source_code=f"SRC-MIG-{marker[-6:].upper()}", title=legacy.title, authority="Group Compliance", category="INTERNAL_POLICY", topics=[], status="ACTIVE")
        db.add(record)
        db.flush()
        version = SourceVersion(record_id=record.id, version_number=1, version_label="1", status="APPROVED", processing_status="PROCESSED",
                                extracted_text=legacy.content, legacy_source_id=legacy.id)
        db.add(version)
        db.commit()
        assert version.chunk_count == 0
    finally:
        db.close()
    found = passages(client, auth, marker)
    assert found and found[0]["title"] == f"Migrated {marker}" and found[0]["page_start"] is None
