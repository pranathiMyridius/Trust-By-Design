# System Architecture

## 1. Purpose

The **Financial Crime Risk Assessment Workbench** assesses the *financial-crime* risk of a proposed business change (new product, new geography, new channel, new vendor, material change) before launch. It covers AML/CTF, sanctions, fraud, bribery and corruption, and the customer, product, geographic, channel and transaction drivers of those risks, plus the control environment that mitigates them.

**Out of scope:** credit, market, liquidity, interest-rate, FX and investment risk. (The existing OCC profile panel that shows these is removed from the MVP UI — see `current-vs-target.md`.)

## 2. Actors

| Actor | Role code | What they do |
|---|---|---|
| Business requester | `BUSINESS_USER` | Creates and submits the change request, uploads documents, answers information requests, submits to manager. Sees only own assessments. |
| FCRM analyst | `FCRM_ANALYST` | Confirms intake profile, runs analysis, rates factors (L×I), maps and assesses controls, resolves challenge findings, drafts recommendation. |
| Line manager | `MANAGER` | First-line approval (approve / return / reject); can act as delegate. |
| Risk committee member | `COMMITTEE_MEMBER` | Votes and records committee decision, sets conditions, opens amendments. |
| Administrator | `ADMIN` | Users, methodology activation, reference-data attestation, backups, legal hold. |
| Auditor (read-only) | *new, production* `AUDITOR` | Reads everything incl. audit export; cannot change anything. MVP: ADMIN performs this. |
| OpenRouter | external | LLM gateway used for 6 AI tasks + embeddings. |
| Neon | external | Managed PostgreSQL (+ pgvector). |
| Langfuse | external, optional | Trace viewer for AI calls. |

## 3. Architectural principles

1. **AI proposes, rules decide, people approve.** The LLM identifies applicable risk categories, indicators, verbatim evidence and missing information. Scores, bands, floors and residual risk are computed by Python. Approval is a named human decision.
2. **Evidence first.** An AI claim is only kept if it is backed by a quote that is found verbatim in a citable source. Everything else is labelled *AI inference* or *missing information*.
3. **Nothing is overwritten.** Every recalculation, re-analysis, override and draft creates a new version; old versions stay with `is_current = false`.
4. **Everything is attributable.** Every write records the authenticated user (never a name from the request body), and every AI output records the run, model, prompt version and methodology fingerprint that produced it.
5. **Monolith first.** One FastAPI service, one database, one LangGraph graph. No microservices, message brokers, caches or vector databases beyond pgvector in Neon.
6. **Fail safe, not silent.** If AI fails, the result is visibly *rules-only / provisional* or *unavailable* — never a low-risk-looking score.

## 4. Logical architecture (target)

```
┌──────────────────────────── Browser ────────────────────────────┐
│ React + Vite + TS SPA (react-router)                            │
│  Dashboard · Create · Assessment workspace · Review · Approvals │
└───────────────▲─────────────────────────────────────────────────┘
                │ HTTPS, JSON, Bearer JWT
┌───────────────┴──────────── FastAPI (single service) ───────────┐
│ Middleware: security headers · timing · admin audit · CORS      │
│ Auth: JWT → current_user → permission matrix → visibility scope │
│ Routers (/api/...)                                              │
│ ┌──────────────── Application services ───────────────────────┐ │
│ │ Intake & documents │ Workflow/transition service │ Approvals │ │
│ │ Analysis orchestration (jobs) │ Review & overrides │ Reports │ │
│ └───────┬──────────────────────┬──────────────────────────────┘ │
│  ┌──────▼──────┐   ┌───────────▼───────────┐  ┌───────────────┐ │
│  │ LangGraph   │   │ Deterministic engines │  │ Audit service │ │
│  │ risk-ident. │──▶│ evidence · scoring ·  │  │ (append-only) │ │
│  │ workflow    │   │ controls · challenge  │  └───────────────┘ │
│  └──────┬──────┘   └───────────────────────┘                    │
│  ┌──────▼──────────────┐                                        │
│  │ AI gateway          │ metered_post · masking · retry ·       │
│  │ (app/ai/*)          │ prompt registry · run recording        │
│  └──────┬──────────────┘                                        │
│  Background worker pool (in-process, Postgres-backed jobs)      │
└─────────┼──────────────────────────────┬────────────────────────┘
          │ HTTPS                        │ TLS (sslmode=require)
   ┌──────▼──────┐                ┌──────▼─────────────────────┐
   │ OpenRouter  │                │ Neon PostgreSQL + pgvector │
   └─────────────┘                └────────────────────────────┘
```

Diagrams: `diagrams/system-context.mmd`, `diagrams/architecture.mmd`, `diagrams/detailed-architecture.mmd`.

## 5. Responsibility split

| Concern | Owner | Authoritative? |
|---|---|---|
| Structuring an intake document into fields | AI (document extraction) → **human confirms** profile | Human-confirmed profile is authoritative |
| Which of the 10 risk categories apply; indicators; evidence quotes; missing information; rationale; misuse scenario | AI (risk-factor identification) | **No** — proposal, reviewed by analyst |
| Whether a quote is really in the source | Python (`evidence.py`) | Yes |
| Suggested likelihood/impact | AI (L×I suggestion) | **No** — suggestion, stored in `ai_suggested_*` |
| Likelihood/impact of record | Analyst | Yes |
| Factor score, weighted inherent score, band | Python (`scoring.py`) | Yes |
| Escalation floors (e.g. sanctions → CRITICAL) | Python rules from active methodology | Yes |
| Jurisdiction designations | Attested reference data (FATF/EU snapshots) | Yes |
| Control effectiveness rating, gaps, residual band | Python (`control_engine`, residual grid) | Yes |
| Suggested controls / control-design opinion | AI | **No** |
| Challenge findings | Python rules (`challenge_engine`) | Yes (findings), human resolves/accepts |
| Draft narrative & recommendation wording | AI (advisory, labelled) | **No** |
| Overrides | Named human with reason | Yes, recorded alongside original |
| Approve / reject | Manager, then committee | Yes |
| Frozen decision record | Python (checksum) | Yes |

## 6. Key quality attributes

| Attribute | How it is achieved |
|---|---|
| Explainability | Per-factor evidence with source/offset/checksum; calculation snapshot (inputs, weights, bands, rules fired); statements labelled FACT/ASSUMPTION/RECOMMENDATION/DECISION. |
| Reproducibility | Deterministic engines + methodology snapshot + `scoring_engine_version`; raw AI output stored per run so evidence verification and scoring can be replayed. |
| Auditability | Append-only `audit_events`, `workflow_transitions`, versioned rows, frozen decision record with SHA-256. |
| Security | JWT + permission matrix + per-assessment visibility; secrets in env; PII masking before AI calls; encryption of uploaded files. |
| Reliability | Degraded mode; retryable jobs; transactions per unit of work; Neon PITR. |
| Testability | Pure-function engines; fake LLM (`tests/support/fake_llm.py`); DeepEval dataset; Playwright journeys. |
| Maintainability | One service; engines separated from API; frontend split by screen. |
