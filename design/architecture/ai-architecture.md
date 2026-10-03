# AI Architecture (overview)

Detail lives in `design/ai/`: `langgraph-workflow.md` (nodes), `risk-scoring.md` (formulas), `evidence-and-explainability.md`, `ai-governance.md` (versioning). This page is the map.

## 1. Where AI is used — and where it is not

| # | AI task | Module | Purpose key (`AIUsageLog.purpose`) | Output is… | Human gate |
|---|---|---|---|---|---|
| 1 | Document → structured intake profile | `document_analysis/ai_extractor.py` | `DOCUMENT_EXTRACTION` | Proposal | Analyst/owner **confirms** profile (`/intelligence/confirm`) before analysis may run |
| 2 | Risk-factor identification (10 categories, indicators, quotes, missing info, rationale, misuse scenario) | `ai/risk_factor_analyzer.py` via LangGraph `identify_risks` | `RISK_FACTOR_IDENTIFICATION` | Proposal; quotes machine-verified | Analyst rates / excludes / adds factors |
| 3 | Likelihood × impact suggestion | `ai/likelihood_impact_analyzer.py` | `LIKELIHOOD_IMPACT_SUGGESTION` | Suggestion (`ai_suggested_*`) | Analyst confirms or overrides (`rating_source`) |
| 4 | Control identification | `ai/control_identifier.py` | `CONTROL_IDENTIFICATION` | Suggested mappings | Analyst saves controls |
| 5 | Control design opinion | `ai/control_identifier.py` | `CONTROL_DESIGN_ASSESSMENT` | Opinion text | Analyst records outcome |
| 6 | Draft narrative (summary, recommendation) | `ai/assessment_draft_generator.py` | `DRAFT_NARRATIVE` | Advisory text (wording guard in `services/advisory.py`) | Analyst edits / accepts |
| 7 | Embeddings for similar past assessments | `ai/embeddings.py` | `EMBEDDING` | Retrieval context only | — |

**AI never does:** compute a score, choose a band, decide an escalation, rate a control, compute residual risk, resolve a challenge finding, approve, reject, or write to an assessment's status.

## 2. Why one workflow graph, not many agents

The brief lists candidate nodes such as *AML analysis, Sanctions analysis, Fraud analysis, Bribery analysis, Geographic analysis*. **Design decision: these are not separate LLM agents.**

- The existing single structured call already walks all 10 categories with one shared evidence block, returning a JSON object validated per category. Splitting it into 5–6 agent calls would multiply cost/latency by ~5×, create cross-agent contradictions to reconcile, and give no extra auditability.
- Financial-crime *typologies* (AML, sanctions, fraud, bribery/corruption) are cross-cutting: a remote-onboarding wallet is simultaneously a customer, channel, AML and fraud risk. Typology is therefore modelled as a **tag on each factor** (`crime_typologies`), produced by the same call and checked deterministically, not as a separate analysis step.
- Deterministic analyses that *look* like AI nodes stay deterministic: geographic designation matching (FATF/EU attested snapshots), completeness checks, evidence verification, scoring, challenge rules.

LangGraph is kept because it gives a typed state, per-node tracing, and conditional routing for the degraded path (see ADR-004). Long-lived human review is **not** modelled inside LangGraph; it is the application state machine (days/weeks, multi-user), persisted in Postgres.

## 3. Target risk-identification graph (summary)

```
validate_input ─▶ load_context ─▶ identify_risks (LLM) ─▶ verify_evidence ─▶ tag_and_aggregate ─▶ score_provisional ─▶ persist_run
      │ (incomplete)                    │ AIProviderError                                         
      └─▶ END (no LLM call)             └─▶ rules_fallback ─▶ tag_and_aggregate …
```

7 nodes; only `identify_risks` calls the LLM. Full node contract: `ai/langgraph-workflow.md`. Diagram: `diagrams/ai-workflow.mmd`.

## 4. Model gateway

- OpenRouter only, through `ai/metering.py::metered_post` (masking, cost/token log, Langfuse generation span).
- One **pinned** model per task (`OPENROUTER_MODEL`, optional per-task overrides `OPENROUTER_MODEL_RISK_IDENTIFICATION` etc.). `openrouter/free` routes each call to a random model — the eval harness shows 18/21 errored cases with it — so it is refused outside `APP_ENV=dev`.
- Temperatures standardised: extraction 0.0–0.1, identification 0.1, L×I suggestion 0.2, narrative 0.3. `response_format: json_object` where the model supports it.
- Retries: 2 attempts with back-off on 408/429/5xx (existing `_post_with_retry`); timeout `OPENROUTER_TIMEOUT_SECONDS`.

## 5. Guardrails

| Risk | Guardrail | Where |
|---|---|---|
| Hallucinated evidence | Quote must be ≥12 chars and found verbatim (normalised) in a cited source; else dropped | `risk_engine/evidence.py` |
| Hallucinated indicator | Indicator kept only if a verified quote is tagged with it; sanctions needs topic cue in the sentence | `evidence.py` |
| Hallucinated category | Unknown category → `AIInvalidResponseError` (fix D-19) → rules-only fallback | `risk_factor_analyzer.py` |
| Prompt injection in documents | Sources are delimited data blocks; prompt states they are data; output is schema-validated; AI output cannot trigger actions | prompt + parser |
| PII leakage to provider | `mask_ai_payload` (cards, IBAN, email, phone, national id) before send | `services/data_masking.py` |
| AI outage | `rules_only` provisional result or `unavailable`; never silent low risk | `risk_engine/degraded.py` |
| Advisory text looking like a decision | Verdict wording rewritten ("Suggested outcome: …") | `services/advisory.py` |
| Model/prompt drift | Run record with model + prompt version + hash; eval gate before changing either | `ai/ai-governance.md` |
