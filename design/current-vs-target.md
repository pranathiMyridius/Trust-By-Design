# Current vs Target — Financial Crime Risk Assessment Workbench

**Stage:** 2 — Design · **Basis:** inspection of the repository on 2026-09-30 (backend `app/`, `alembic/versions/0001–0008`, `frontend/src`, `tests/`, `docs/`, `eval_results/`).

Legend: ✅ **Implemented** · 🟡 **Partial** · ❌ **Missing** · 🔧 **Needs improvement**

The single most important finding: **the system is already far past MVP scaffolding.** 37 tables, 151 API routes, a 5-node LangGraph pipeline, deterministic scoring with methodology versioning, evidence-quote verification, degraded mode, a two-tier approval chain with delegation, frozen decision records and an append-only audit log all exist. Stage 3 is therefore a **hardening and consolidation** stage, not a rebuild.

---

## 1. Component status

| Area | What exists (file references) | Status | Target change (summary) |
|---|---|---|---|
| Frontend stack | React 19.2, Vite 8, TS 6; no router, no UI kit (`package.json`) | ✅ | Keep. Add `react-router` for URLs (ADR-001). |
| Frontend routing | State-based `currentPage` in `App.tsx`; refresh always lands on Dashboard | 🔧 | URL routes per screen; deep links for reviewers/auditors. |
| Frontend structure | `AssessmentWorkflow.tsx` 8,566 lines, 171 `useState`; `api/assessments.ts` ~3,300 lines | 🔧 | Split into step modules + one API client. |
| API base URL | `http://127.0.0.1:8000` hard-coded in 11 places in `src/` (+1 in e2e helpers) | 🔧 | `import.meta.env.VITE_API_BASE_URL`. |
| Backend stack | FastAPI 0.141, SQLAlchemy 2.0, Pydantic 2, uvicorn | ✅ | Keep (ADR-002). |
| API surface | 151 routes on 17 routers (`app/api/*`) | ✅/🔧 | Keep paths; fix authz, actor, GET-writes, error envelope (see `api/api-design.md`). |
| Database | PostgreSQL on **Neon** + pgvector; `DATABASE_URL` env | ✅ | Keep (ADR-003). |
| DB connection | `create_engine(DATABASE_URL)` with **no pool args**; **defaults to `sqlite:///./risk.db`** if unset; `CREATE EXTENSION vector` at import (Postgres only) | 🔧 | Fail fast without `DATABASE_URL` outside tests; `pool_pre_ping`, `pool_recycle`; pooled vs direct Neon hosts. |
| Migrations | Alembic 0001–0008, run **at import** of `main.py`, advisory lock | 🟡 | Keep Alembic; run as explicit release step using the direct (non-pooled) Neon URL. |
| JSON storage | All JSON in `Text` columns, `json.loads` helpers | 🟡 | MVP keep; production: JSONB for queried columns. |
| Timestamps | Naive `DateTime` with Python UTC default | 🔧 | Production: `timestamptz`. |
| Auth | JWT HS256 (python-jose), bcrypt, 5 roles, per-request DB user load | ✅ | Keep for MVP; SSO/OIDC for production. |
| Authorization | Global `enforce_api_access` + visibility scoping; four definitions of `require_pipeline_role` with two different role sets (analyst+manager+admin vs manager+admin) | 🔧 | One permission matrix (`app/auth/permissions.py`). |
| Access-control gaps | `/audit/all` open to all users; methodology create/update open; reassessment + committee-condition endpoints have no role check | ❌ (bugs) | Close in Stage 3 (P0). |
| Actor attribution | ~20 endpoints take actor from request body (`payload.rated_by` etc.); some audits have no actor | 🔧 | Actor always from JWT (`current_user`). |
| LangGraph | `load_assessment → gather_intelligence → identify_risks → calculate_scores → persist_results` | ✅ | Keep; add `validate_input`, split `verify_evidence`, add `record_run` (ADR-004). |
| LLM gateway | OpenRouter via `metered_post()`; model from `OPENROUTER_MODEL` (default `openrouter/free`) | ✅/🔧 | Keep OpenRouter; **pin model**, refuse `openrouter/free` outside dev (ADR-005). |
| AI touchpoints | 7: document extraction, risk-factor identification, L×I suggestion, control identification, control design, draft narrative, embeddings | ✅ | Register each prompt with a version. |
| Prompts | Inline f-strings in 5 modules; temperatures 0.1–0.5 | 🔧 | Prompt registry with `prompt_key`, `version`, SHA-256. |
| Prompt / workflow / run versioning | None (`grep prompt_version` → 0 hits) | ❌ | `analysis_runs` + `prompt_versions` tables (ADR-009). |
| Deterministic scoring | `risk_engine/scoring.py`: L×I per factor, weighted average, bands, escalation rules, residual grid | ✅ | Keep formula; fix edge cases (see `ai/risk-scoring.md`). |
| AI produces scores? | **No.** Prompt forbids scores; AI factors start unrated | ✅ | Keep — matches ADR-006. |
| Methodology versioning | `risk_methodologies` with version, fingerprint, lock-on-use, activation | ✅ | Add `scoring_engine_version`; drop unused `thresholds`. |
| Evidence verification | `risk_engine/evidence.py`: exact normalised substring match, ≥12 chars, sanctions topic cues | ✅ | Keep; relabel "quote verified in source" ≠ "fact verified". |
| Confidence | Only `is_provisional`; no confidence measure | ❌ | Deterministic `confidence_level` (see `ai/risk-scoring.md` §6). |
| Degraded mode | `ai_assisted` / `rules_only` / `unavailable`; acknowledgement gate | ✅ | Keep; fix mitigant-category mapping of rules-only output. |
| Legacy 6-dimension `risk_results` | CUSTOMER/OPERATIONAL/FINANCIAL/COMPLIANCE/TECHNOLOGY/THIRD_PARTY, mirrored from factors | 🔧 | Freeze as read-only history; stop using for display or challenge. |
| OCC risk profile | Maps factors to CREDIT, INTEREST_RATE, LIQUIDITY, PRICE, FX, … | 🔧 (scope) | **Remove from UI** — outside financial-crime scope. |
| Financial-crime typologies | Categories are dimension-based; only `SANCTIONS_EXPOSURE` indicator is typology-specific; no bribery/corruption | 🟡 | Add `crime_typologies` tag on factors (AML, SANCTIONS, FRAUD, BRIBERY_CORRUPTION). |
| Controls | Library of 12 FC controls, design/operating effectiveness, gaps | ✅ | Fix two endpoints that 500 (`assess-design`; `identify-controls` with `auto_create`). |
| Challenge review | Two systems: legacy `/challenge` (hard-coded 80/60) and `challenge_engine` | 🔧 | Retire legacy; one challenge engine. |
| Lifecycle | Pipeline `status` (19 values) + derived `workflow_status` (16) + `TRANSITIONS` table | ✅ | Keep; one transition entry point; retire `PATCH /status`. |
| Human review | FCRM review, overrides (`assessment_overrides` with ai_value/human_value/reason), comments, info requests | ✅/🔧 | `reviewed_by` hard-coded "FCRM Reviewer" → real user; override guard rules. |
| Approvals | Manager → committee, votes, conditions, delegation (AW.1–AW.7) | ✅ | Keep. |
| Decision record | Frozen JSON + SHA-256 checksum, `verify_record` | ✅/🔧 | Add to protected tables; include run IDs + prompt versions. |
| Audit | `audit_events` append-only (ORM hook), `workflow_transitions`, admin middleware | ✅/🔧 | Always actor_id; request_id; production hash chain. |
| Explainability | FACT / ASSUMPTION / RECOMMENDATION / DECISION statements with provenance | ✅ | Add VERIFIED_EVIDENCE vs AI_INFERENCE vs MISSING labels. |
| Background jobs | In-process `ThreadPoolExecutor`; jobs survive restart as FAILED/re-queued | 🟡 | MVP keep (single instance); production: Postgres-backed queue. |
| File storage | Local disk `uploaded_files/`, optional Fernet encryption | 🟡 | MVP: persistent volume; production: object storage. |
| Observability | Langfuse tracing (optional), `AIUsageLog`, request timing | ✅ | Keep; production centralised logs. |
| Testing | pytest (unit/api/workflow), DeepEval 21-case dataset, Playwright 3 specs, fake LLM | 🟡 | Latest eval **fails gates** (free model); pin model, add cases. |
| Deployment | `docker-compose.yml` = Postgres only; **no app Dockerfile**; no CI in repo copy inspected | ❌ | Add backend Dockerfile + static frontend build (see `architecture/deployment-architecture.md`). |
| Docs | `AI_ORCHESTRATION.md` says AI produces 6 dimension scores — **stale** | 🔧 | Replace with `design/ai/langgraph-workflow.md`. |

---

## 2. Defects found during inspection (input to Stage 3)

These are facts from the code, not design opinions. Each is referenced by ID elsewhere in the package.

| ID | Defect | Where | Severity |
|---|---|---|---|
| D-01 | Any signed-in user can read every audit event | `GET /api/assessments/audit/all` | High |
| D-02 | Any signed-in user can create an *active* methodology (bypasses ADMIN-only activate) | `POST /api/risk-methodologies` with `is_active` | High |
| D-03 | Actor taken from request body in ~20 endpoints | `assessments.py`, `controls.py`, `reassessment.py` | High (audit integrity) |
| D-04 | `assess-design` always 500; `identify-controls` with body `auto_create=true` 500 **after commit** | `controls.py` L479–485, 548–554 (undefined `AuditAction.CONTROL_IDENTIFIED/CONTROL_ASSESSED`, plus unsupported `user_email=` kwarg) | High |
| D-05 | Reassessment endpoints and `PATCH committee-conditions/{id}` have no role check | `reassessment.py`, `approvals.py` | Medium |
| D-06 | `SQLite` fallback when `DATABASE_URL` unset — a mis-configured deploy silently writes to a local file | `database.py` | High |
| D-07 | ~15 GET endpoints write to the DB (e.g. `/inherent-risk`, `/challenge-review`, `/control-summary`, `/decision-package` may call AI) | various | Medium |
| D-08 | `advance-stage` commits mid-request, then may fail → partial state | `assessments.py` advance handler | Medium |
| D-09 | Hard-coded 80/60/40 bands in manual factor add and legacy challenge | `add_assessment_risk_factor`, `/challenge` | Medium |
| D-10 | `thresholds` stored but unused and outside the fingerprint | `scoring.py`, `methodology.py` | Low |
| D-11 | Rules-only COMPLIANCE findings map to mitigant category `CONTROL_ENVIRONMENT_RISK` and are dropped | `risk_engine/degraded.py` `DIMENSION_TO_CATEGORY` (L116–134) | Medium |
| D-12 | FCRM review `reviewed_by` hard-coded "FCRM Reviewer"; logged as `APPROVAL` | `update_fcrm_review` | Medium |
| D-13 | `decision_records`, `residual_risk_calculations`, `challenge_findings` not protected from update/delete | `data_protection.py` | Medium |
| D-14 | Rating mismatch keyed on `RiskResult.id`, which changes on every recalculation | `challenge_engine` | Low |
| D-15 | UI shows unrated factors as LOW / 0; sidebar score goes stale | `AssessmentWorkflow.tsx` (known E2E failures) | High (UX safety) |
| D-16 | Three unaligned stage models (9-node tracker, 8-step wizard, 7-stage backend) | frontend | Medium |
| D-17 | `waitForProcessingJob` polls forever, no timeout/unmount cleanup | `api/processing.ts` | Low |
| D-18 | Async upload handlers block the event loop (sync extraction + AI inside `async def`) | `create-with-document`, `analyze-document` | Medium |
| D-19 | Hallucinated category raises `ValueError` → `UNEXPECTED_ANALYSIS_FAILURE` instead of `AI_INVALID_RESPONSE` | `risk_factor_analyzer._validate_factor` | Low |
| D-20 | `required_approvals` in methodology is fingerprinted but never enforced | `methodology.py` | Low (open question Q-03) |

---

## 3. What is deliberately **not** changing

- React/Vite/TS, FastAPI, Neon PostgreSQL, LangGraph, OpenRouter — all retained.
- The scoring formula (L×I normalised to 0–100, weighted average, band lookup, escalation floors, residual grid).
- The AI boundary: the LLM never produces a score, band, likelihood or impact of record.
- The pipeline status values and `TRANSITIONS` table (they already model the lifecycle well).
- The approval chain, delegation model and committee voting.
- The 10 canonical risk categories and 12 indicators (extended, not replaced).
- Supersede-don't-delete versioning (`version`, `is_current`, `superseded_at`) used across factor, calculation, control and draft tables.
