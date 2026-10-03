# Technical Architecture

This document tells a Stage 3 developer **where code lives and how a request flows**. It is grounded in the current repository layout; new modules are marked **(new)**.

## 1. Backend module map (`backend/app`)

| Layer | Package | Responsibility | Status |
|---|---|---|---|
| Entry | `main.py` | App factory, router registration, startup tasks | Keep; move migrations out of import (§5), replace `@app.on_event` with `lifespan` |
| Middleware | `middleware.py` | Security headers, timing, admin audit | Keep; add CSP + `X-Request-ID` |
| Auth | `auth/security.py`, `auth/dependencies.py`, `auth/access.py` | JWT, bcrypt, `get_current_user`, visibility | Keep |
| Auth | `auth/permissions.py` **(new)** | Single role→permission matrix; `require(permission)` dependency | Replaces the 4 `require_pipeline_role` definitions (2 role sets) |
| API | `api/*.py` (17 routers) | HTTP only: validate, authorise, call service, map errors | Thin out `assessments.py` (5,097 lines) into `api/assessments/{intake,analysis,factors,scores,review,documents}.py` |
| Schemas | `schemas/*.py` | Pydantic request/response | Keep; add `ErrorResponse`, typed responses for untyped dict routes |
| Services | `services/*.py` | Use-case logic, transactions | Keep; add `analysis_service.py` **(new)**, `transition_service` = existing `workflow.transition()` as sole entry point |
| Workflow orchestration | `langgraph/` | Risk-identification graph | Extend (see `ai/langgraph-workflow.md`) |
| AI gateway | `ai/*.py`, `document_analysis/ai_extractor.py` | Prompt build, OpenRouter call via `metered_post`, parse | Move prompts to `ai/prompts/` **(new)** registry |
| Deterministic engines | `risk_engine/`, `control_engine/`, `challenge_engine/` | Pure functions: evidence, scoring, controls, challenge | Keep; fix D-09/D-10/D-11 |
| Reference data | `reference_data/` | FATF/EU JSON, country normaliser | Keep |
| Persistence | `database.py`, `models/*.py`, `alembic/` | Engine, session, ORM, migrations | Harden for Neon (see `database/database-architecture.md`) |
| Observability | `observability/tracing.py`, `ai/metering.py` | Langfuse spans, AI usage log | Keep; link to `analysis_run_id` |

### Layering rules (enforced in code review)

1. `api/*` may import `services/*`, `schemas/*`, `auth/*`. It must not contain business rules or call `ai/*` directly.
2. `services/*` own the DB transaction (`db.commit()` happens once, at the end of a use case). Engines never commit.
3. `risk_engine/`, `control_engine/`, `challenge_engine/` are **pure**: input dicts → output dicts, no DB session, no network. This is what makes scoring reproducible and unit-testable.
4. `ai/*` never touches the DB except through `metered_post` → `AIUsageLog` and the new `record_run` helper.
5. GET handlers do not write (fixes D-07). Derived state is recomputed on the write that changes its inputs.

## 2. Request flow — standard write

```
HTTP → SecurityHeaders → Timing → CORS → AdminAudit
     → router dependency: get_db, get_current_user, enforce_api_access (visibility)
     → require(permission)                          # new, single matrix
     → handler: parse Pydantic body
     → service.use_case(db, actor=current_user, ...)
          ├─ ensure_assessment_editable()            # decision_lock
          ├─ domain change (+ version supersede)
          ├─ engine recompute (pure)                 # scoring/controls/challenge
          ├─ workflow.transition()  (if status moves) # writes workflow_transitions
          ├─ log_audit_event(actor_id=current_user.id, request_id=...)
          └─ db.commit()                              # exactly once
     → response model
```

On any exception the session rolls back; nothing partial is committed (fixes D-08).

## 3. Request flow — analysis (target)

```
POST /api/assessments/{id}/analyze-async   (Idempotency: returns active job if one exists)
  → analysis_service.request_analysis()
      checks: status ∈ {EVIDENCE_COLLECTION, RISK_IDENTIFICATION, REMEDIATION}
              profile confirmed; permission analysis.run
      inserts processing_jobs(RISK_ANALYSIS, QUEUED) + analysis_runs(QUEUED)
      commit → enqueue
Worker thread:
  run_risk_assessment_workflow(run_id)  → LangGraph (7 nodes, see ai/langgraph-workflow.md)
     each node → ProgressReporter(progress %, stage_message)  → processing_jobs row
  on success: if status == EVIDENCE_COLLECTION → workflow.transition(→ RISK_IDENTIFICATION,
              actor = requesting user, action="ANALYZE")
Browser polls GET /api/processing-jobs/{job_id}  (1–2 s, timeout 10 min, stops on unmount)
```

`PATCH /advance-stage` from EVIDENCE_COLLECTION no longer runs the LLM synchronously; it enqueues the same job and returns `202` with the job id (the UI already knows how to poll jobs).

## 4. Frontend architecture (`frontend/src`)

| Concern | Current | Target |
|---|---|---|
| Routing | `currentPage` state | `react-router` routes (see `ux/ux-decisions.md` §2) |
| API client | 9 modules, 11 hard-coded base URLs, 7 fetch wrappers | `api/client.ts` (one `request<T>()`, base URL from `VITE_API_BASE_URL`, global 401 → logout, error envelope parsing); domain modules re-export typed calls |
| Server state | Loaded once in `App.tsx`, manual refresh | Per-screen loaders + explicit `refresh()` after mutations. (TanStack Query is *optional*; not required for MVP.) |
| Assessment workspace | 1 file, 8 steps, nested ternaries | `features/assessment/` with one component per stage; stage list from `GET /api/workflow/config` |
| Auth | Token in `localStorage` | MVP keep + CSP; production httpOnly cookie via SSO |
| Components | `RiskLevelBadge`, `FormFeedback`, `ProcessingStatus`, `ExplainabilityPanel`, `FactorEvidence` | Keep and reuse; add `UnratedBadge`, `EvidenceOriginTag`, `ProvenanceLine` |

## 5. Configuration and environments

All configuration comes from environment variables (`backend/.env.example` is the reference). Target additions:

| Variable | Purpose | Required |
|---|---|---|
| `APP_ENV` | `dev` \| `test` \| `staging` \| `prod`; gates fail-fast checks | Yes **(new)** |
| `DATABASE_URL` | Neon **pooled** host (`-pooler`), `sslmode=require` — app runtime | Yes (no SQLite fallback unless `APP_ENV=test`) |
| `DATABASE_URL_DIRECT` | Neon **direct** host — Alembic migrations, `CREATE EXTENSION` | Yes for migrations **(new)** |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_RECYCLE_SECONDS` | Pool tuning (defaults 5 / 5 / 300) | No **(new)** |
| `RUN_MIGRATIONS_ON_STARTUP` | `true` only in dev | No **(new)** |
| `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Gateway + pinned model | Yes (startup refuses `openrouter/free` when `APP_ENV ∈ {staging, prod}`) |
| `OPENROUTER_ALLOWED_MODELS` | Comma list of approved model ids | Prod **(new)** |
| `JWT_SECRET` | Required when `APP_ENV ≠ dev` (no random per-process secret) | Yes |
| `FILE_ENCRYPTION_KEY` | Required in staging/prod | Yes |
| `VITE_API_BASE_URL` | Frontend API base | Yes **(new, frontend)** |

Startup validation (`app/config.py` **(new)**) fails the process with a clear message if a required variable is missing for the current `APP_ENV`.

## 6. Error contract

All non-2xx responses use one envelope (new `ErrorResponse` schema, via a global exception handler):

```json
{
  "error": {
    "code": "ASSESSMENT_LOCKED",
    "message": "This assessment has a final decision and can no longer be edited.",
    "details": { "status": "APPROVED" },
    "request_id": "7f3c…"
  }
}
```

`code` values are stable (used by the UI and tests); `message` is safe for end users; raw exception text is never returned (today 500s include it). See `api/api-design.md` §3 for the code list.

## 7. Transactions and concurrency

- One DB transaction per use case; worker jobs open their own `SessionLocal`.
- Optimistic concurrency for review edits: `PATCH` bodies carry `expected_version` for drafts and intelligence; mismatch → `409 VERSION_CONFLICT`.
- Status transitions lock the assessment row (`SELECT … FOR UPDATE`) inside `workflow.transition()` to prevent two approvals racing.
- Only one active `RISK_ANALYSIS` job per assessment (already enforced; add a partial unique index, see schema).
