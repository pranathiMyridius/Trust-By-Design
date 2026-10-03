# Stage 2 Design Summary — Financial Crime Risk Assessment Workbench

**Prepared:** 2026-09-30 · **Inputs:** full inspection of the repository (backend, 37 models, 8 migrations, 151 routes, LangGraph, engines, frontend, tests, eval results, docs). **Stage 3 starts from:** `mvp-vs-production.md` → *Stage 3 build list*.

## Package index

| Folder | Files |
|---|---|
| `architecture/` | `system-architecture.md` · `technical-architecture.md` · `ai-architecture.md` · `deployment-architecture.md` · `assessment-lifecycle.md` (added) · `security-architecture.md` (added) |
| `database/` | `database-architecture.md` · `schema-design.md` · `migration-strategy.md` |
| `api/` | `api-design.md` |
| `ai/` | `langgraph-workflow.md` · `risk-scoring.md` · `evidence-and-explainability.md` · `ai-governance.md` |
| `ux/` | `user-journeys.md` · `wireframes.md` · `ux-decisions.md` |
| `diagrams/` | 10 Mermaid files (all validated with mermaid-cli 11) |
| `decisions/` | ADR-001 … ADR-010 |
| root | `current-vs-target.md` · `requirements-traceability.md` · `design-risks.md` · `mvp-vs-production.md` · this file |

---

## A. Current architecture

A working modular monolith: **React 19 + Vite + TS** SPA (state-based navigation) → **FastAPI** (17 routers, 151 routes, JWT + 5 roles, per-assessment visibility) → services and **pure deterministic engines** (evidence verification, L×I scoring with methodology versioning and escalation rules, control effectiveness, residual grid, challenge rules) → **LangGraph** 5-node pipeline with one OpenRouter LLM call → **Neon PostgreSQL + pgvector** (37 tables, Alembic 0001–0008, append-only audit). Degraded mode, two-tier approvals with delegation, frozen decision records with SHA-256 and explainability statements already exist. Main weaknesses: no prompt/workflow/run versioning, SQLite fallback and no Neon pool tuning, access-control gaps, client-supplied actor names, GETs that write, two controls endpoints that always fail, and UI states that show unrated as LOW. Full list: `current-vs-target.md` (D-01…D-20).

## B. Target architecture

Same stack and topology, hardened: one FastAPI service with a single permission matrix, error envelope and actor-from-JWT; analysis only via async jobs running a **7-node LangGraph** graph (one LLM node) that records an **`analysis_runs`** row with model, prompt version, workflow version, input hash and raw output; a **prompt registry**; deterministic scoring with engine version, confidence and replay; Neon with pooled/direct endpoints, pre-ping and per-environment branches; React with URL routing, a typed client and explicit UNRATED/provisional states. See `diagrams/architecture.mmd`, `diagrams/detailed-architecture.mmd`.

## C. Key design decisions

1. Keep React/Vite, FastAPI, Neon, LangGraph, OpenRouter (ADR-001…005).
2. **AI proposes, rules decide, people approve** — no LLM score of record (ADR-006).
3. **Evidence-first**: indicators and escalations depend only on verbatim-verified quotes or attested reference data (ADR-007).
4. **One LLM call, not many agents**; typologies (AML, sanctions, fraud, bribery) are tags (ADR-004).
5. Human review stays in the application state machine, not in LangGraph (ADR-004, ADR-008).
6. **Run + prompt versioning** as first-class data (ADR-009).
7. Audit: actor id always from JWT, before/after, request id, extended protection (ADR-010).
8. No new infrastructure for MVP (no Redis, Kafka, vector DB, Kubernetes).

## D. AI responsibilities

Does: structure intake documents; decide which of 10 FC risk categories apply; list indicators; quote verbatim evidence with source ids; flag conflicts; list missing information; write rationale and misuse scenarios; propose typology tags; suggest L×I; suggest controls and control-design opinions; draft advisory narrative; embed text for similar-case retrieval.
Does **not**: score, band, escalate, rate controls, compute residual, resolve findings, change status, approve or reject.

## E. Deterministic responsibilities

Input validation and completeness; evidence-quote verification and indicator acceptance; jurisdiction designation matching against attested FATF/EU snapshots; typology tag rules; factor score, weighted inherent score, band, escalation floors; confidence; control ratings, gaps, residual grid and floors; challenge findings and blocking; state transitions, gates, SLA and escalation; decision-record freezing and checksum; masking before AI calls.

## F. Database design

Neon PostgreSQL 16 + pgvector is the sole runtime DB (SQLite only for unit tests). Runtime uses the pooled endpoint with `pool_pre_ping`, `pool_recycle=300`, `sslmode=require`; Alembic runs as a release step on the direct endpoint. Retain all 37 tables except: freeze `risk_results` and retire `assessment_challenges`; add `analysis_runs`, `prompt_versions`, `idempotency_keys`; add actor-id, run-id, engine-version and confidence columns (migrations 0009–0014, expand-only). JSON stays in `Text` for MVP; JSONB for queried columns in production; frozen records stay byte-stable text. See `database/*`, `diagrams/er-diagram.mmd`.

## G. UX design

12 screens (S1–S12) with URL routes; dashboard with server-side counts; create form regrouped into 7 sections with ISO country selection; analysis progress as a 7-step stepper; each finding split into *Evidence (quote found in source)*, *AI inference — not evidence*, *Information needed*; score breakdown with contributions and rules fired; review checklist that explains every disabled action; approvals with decision package and conditions; unified audit timeline. Mermaid for technical diagrams, Figma for UX, Excalidraw for the pitch. See `ux/*`.

## H. Security and governance

JWT + permission matrix + visibility + SoD; secrets only in platform store; PII masking before AI; encrypted uploads; CSP and login throttling; append-only audit with actor ids; frozen decision records; model/prompt/workflow/methodology/engine versions on every result; eval gate for prompt/model changes; reproducible replay of deterministic steps. See `architecture/security-architecture.md`, `ai/ai-governance.md`.

## I. MVP scope (Stage 3)

P0: config/DB hardening; permission matrix and actor fixes; controls endpoint fixes; migrations 0009–0011; prompt registry and run recording; 7-node graph with async progress; UNRATED/provisional UI; pinned model + eval baseline. P1: override rules; `/scores`, `/assessments/summary`, `/analysis-runs`; error envelope and read-only GETs; router + component split; typology tags; retire legacy endpoints. P2: idempotency keys, Dockerfile/CI deploy, login throttling/CSP. Detail: `mvp-vs-production.md`.

## J. Production roadmap

SSO/MFA and AUDITOR role; dedicated worker with Postgres `SKIP LOCKED` queue; object storage; JSONB/timestamptz/CHECK constraints and DB-level append-only triggers; audit hash chain + SIEM; normalised `factor_evidence` and `approval_decisions`; Neon Scale plan with PITR, read replica, protected branch; OpenTelemetry; continuous evals; enterprise model gateway option.

## K. Open questions (product/business decisions only)

| ID | Question | Default if not answered |
|---|---|---|
| Q-01 | Should FCRM analysts map and assess controls (today only MANAGER/ADMIN can, while analysts run every other analyst stage)? | Yes — analysts get `control.manage` |
| Q-02 | Add bribery/corruption indicators `PEP_EXPOSURE` and `PUBLIC_OFFICIAL_INTERACTION` (requires methodology version bump and eval cases)? | Add as tags only (typology `BRIBERY_CORRUPTION`); indicators after approval |
| Q-03 | Should `required_approvals` by band be enforced (e.g. LOW = analyst only) or removed? Is any auto/light approval acceptable for LOW risk? | Keep manager → committee for all bands; mark config as informational |
| Q-04 | Approve the FATF/EU jurisdiction escalation rules (currently `draft`) so designations affect the band? | Keep draft; designations raise challenge findings only |
| Q-05 | Are equal default weights (0.10 per category) the approved production methodology? | Keep equal weights; FCRM to confirm before go-live |
| Q-06 | Which LLM provider/model is approved (data residency, zero-retention, DPA)? | Pin one paid model with zero-retention routing; masking on |
| Q-07 | Has `backend/.env` ever been committed or shared? If yes, rotate Neon, OpenRouter and JWT secrets. | Rotate before demo |
