# Evidence and Explainability

## 1. Evidence-first rule

> An AI statement that matters to the risk result must either point at text that really exists in a citable source, or be explicitly labelled as inference or as missing information. The AI may not introduce evidence.

This is largely implemented (`risk_engine/evidence.py`, `risk_factor_analyzer.py` prompt, `services/explainability_statements.py`). This design adds explicit **origin** and **claim-type** labels and fixes one wording risk: a "verified" quote proves the text is *in the source*, not that the source is *true*.

## 2. Evidence sources

| Source id | Origin (new label) | Examples | Verified how |
|---|---|---|---|
| `FIELD:<name>` | `USER_PROVIDED` | `FIELD:description`, `FIELD:countries_jurisdictions`, `FIELD:transaction_types` (12 intake fields) | Exact match against the stored field text |
| `DOC:<id>` | `DOCUMENT` (user-uploaded) | Intake document, policy, vendor due-diligence pack | Exact match against current `assessment_documents.extracted_text` (version + checksum recorded) |
| `REF:<snapshot>:<iso>` | `SYSTEM_DERIVED` | FATF call-for-action, EU high-risk third country | Deterministic lookup in **attested** reference snapshot |
| `CALC:<id>` | `SYSTEM_DERIVED` | Rule fired, control gap detected | Produced by engines |
| `EXT:*` | `EXTERNAL` | (none in MVP — no web retrieval) | — |

The prompt lists only FIELD and DOC sources as quotable; the model cannot cite REF/CALC (those are added by the system).

## 3. Evidence record (per quote, stored in `risk_factors.evidence`)

| Field | Meaning |
|---|---|
| `source_id`, `source_type`, `source_label` | Which source |
| `document_id`, `document_version`, `source_checksum` | Exact version of the text checked (`sha256:` of normalised text) |
| `verbatim_quote` | Model's quote |
| `normalized_offset`, `page` | Where it was found |
| `verification` | `EXACT_VERIFIED`, `NOT_FOUND`, `UNKNOWN_SOURCE`, `TOO_SHORT` |
| `quote_verified` | bool |
| `indicator`, `supports_indicator` | Indicator the quote is offered for, and whether the topic check passed |
| `origin` **(new)** | `USER_PROVIDED` / `DOCUMENT` |
| `verified_at` **(new)** | Timestamp of verification (= run time) |
| `analysis_run_id` **(new, at factor level)** | Run that produced it |

Evidence **timestamp** semantics: `verified_at` = when the system checked it; the source's own date is `assessment_documents.effective_date` / `created_at` (document) or the assessment's `updated_at` (field).

## 4. Verification rules (existing, retained)

1. Normalise (NFKC, fold typographic quotes/dashes, collapse whitespace, casefold).
2. Quote must be ≥ 12 normalised chars.
3. Exact substring match in the cited source (no fuzzy matching — paraphrase is rejected by design).
4. Indicator accepted only if at least one verified quote is tagged with it and `supports_indicator`.
5. `SANCTIONS_EXPOSURE` additionally requires a sanctions topic cue in the same sentence (`sanction`, `embargo`, `designated person`, `asset freeze`, `screening hit`, …).
6. **New:** extend topic cues to the proposed bribery indicators (`PEP_EXPOSURE`: "politically exposed", "PEP", "senior public function"; `PUBLIC_OFFICIAL_INTERACTION`: "government official", "public official", "licence", "permit", "state-owned") — only if Q-02 approves the indicators.

`evidence_status` per factor: `NOT_APPLICABLE` → `CONFLICTING_EVIDENCE` → `EVIDENCE_FOUND` → `NOT_VERIFIED` (quotes given, none verified) → `INSUFFICIENT_EVIDENCE` (no quotes).

## 5. Three claim types the UI and API must distinguish

| Claim type | What it is | Examples | UI treatment |
|---|---|---|---|
| **Verified evidence** | A quote found verbatim in a user-provided field or uploaded document; or a system-derived fact from attested reference data / engines | "Customers can withdraw cash at partner agents" (DOC:14 p.3) · "BR: FATF increased monitoring (snapshot 2026-06, attested)" | Quote block with source chip, doc version, "Quote found in source ✓". Label **"Quote found in source"**, never "Fact verified" |
| **AI inference** | Model reasoning not directly quotable | `rationale`, `misuse_scenario`, typology tags without a supporting accepted indicator, L×I suggestions, draft narrative | Italic panel labelled **"AI inference — not evidence"**, model + prompt version in provenance line |
| **Missing information** | What the model or engine says is needed | `missing_information[]`, `input_issues[]`, rejected indicators with reasons | Amber list labelled **"Information needed"** with a "Request information" action |

Mapping to existing explainability kinds (`explainability_statements.py`): verified evidence → `FACT` (origin HUMAN for fields/docs, AUTOMATED for REF/CALC); AI inference → `ASSUMPTION` (PENDING_REVIEW until an analyst rates/accepts); recommendations → `RECOMMENDATION` (advisory wording enforced); human acts → `DECISION`.

## 6. The AI must not invent evidence — enforcement points

| Point | Mechanism |
|---|---|
| Prompt | "CITABLE SOURCES are the ONLY text you may quote … paraphrased quotes are rejected … never guess" (existing) |
| Parser | Unknown `source_id` → `UNKNOWN_SOURCE`; quote dropped |
| Verifier | Exact normalised match; min length; topic cues |
| Indicators | Dropped without a verified quote |
| Escalation | Rules fire only on *accepted* indicators and *attested* reference data |
| Draft narrative | Built from stored facts; evidence references come from verified records, not from the narrative model |
| Evaluation | DeepEval faithfulness + "evidence quotes verified" metrics (94 % in latest run) gate prompt/model changes |

## 7. Explainability surfaces

| Surface | Endpoint | Content |
|---|---|---|
| Factor evidence panel | `GET /risk-factors` | Per factor: quotes (verified/rejected), indicators (accepted/rejected + reason), missing info, AI inference block, rating provenance |
| Score explanation | `GET /scores`, `GET /explain` | See `risk-scoring.md` §9 |
| Statement ledger | `GET /explain/statements` | FACT / ASSUMPTION / RECOMMENDATION / DECISION with `origin`, `source`, `basis`, `actor`, `model_version`, `review_status`, `reference{type,id}` |
| Run provenance | `GET /analysis-runs/{uuid}` | Model, prompt version, workflow version, input hash, evidence stats |
| Decision record | `GET /decision-record` | Frozen snapshot + checksum + `intact` |
| Audit package | `GET /audit-export` | Everything above as one JSON download (ADMIN/analyst/manager/committee) |

## 8. Wording rules (UI + generated text)

- Never "verified evidence" alone → "quote found in source".
- Never present unrated as low → "Unrated".
- Automated recommendations always carry "Advisory — not a decision" (existing `advisory_notice`).
- Rules-only results always carry "Provisional — AI unavailable; categories not evaluated: …".
