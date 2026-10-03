# Complete system flowchart — Financial Crime Risk Assessment Workbench

Diagram: [`complete-system-flowchart.mmd`](complete-system-flowchart.mmd) (rendered copy: `complete-system-flowchart.svg`).
Single `flowchart TD`, Mermaid 11. Validated by rendering with `@mermaid-js/mermaid-cli` 11 (Mermaid 11.17.2); paste the `.mmd` into https://mermaid.live to view and zoom.

Everything in the diagram was traced to source on 2026-10-03: `backend/app` (routers, services, LangGraph, models), `backend/alembic/versions` 0001–0022, `frontend/src` (pages and `api/*.ts`), `backend/.env` (non-secret keys only) and `render.yaml`. No endpoint, table, status, node or role was added that the code does not contain.

## Legend

| Edge | Meaning |
|---|---|
| Blue solid | Frontend → REST API request |
| Slate solid | Backend processing step |
| Purple thick | AI / LLM call (all go through `metered_post()`) |
| Green dotted | Database read / write |
| Orange | Human action or decision |
| Grey dashed | Audit / observability write |
| Red | Error, refusal or blocked path |

Node shapes: stadium = user role · rectangle = process · diamond = decision / gate · double-bordered = LangGraph node · hexagon = external LLM service · cylinder = data store · flag = audit record. A dashed border marks an optional or supported-but-inactive component.

## Sections

**Users and reviewers.** Roles come from `UserRole`: Business User (owner), FCRM Analyst, Manager (or an active delegate), Committee Member (incl. Committee Chair designation), Auditor/Executive (read-only), Admin/Policy Admin. A Control Owner role also exists for control and action-item visibility; it is not on the main path, so it is not drawn.

**Frontend.** React 19 + TypeScript + Vite SPA. `LoginPage`/`AuthContext` store the JWT in localStorage; every `api/*.ts` call goes through `authFetch()` with a Bearer token. Long-running steps return a ProcessingJob that `AssessmentWorkflow` polls via `GET /api/processing-jobs/{id}`.

**REST API layer and security.** FastAPI with SecurityHeaders, CORS, RequestTiming and AdminAudit middleware (optional HTTPS redirect). `POST /api/auth/login` verifies bcrypt hashes and issues an HS256 JWT. Every other router is protected by `enforce_api_access` (authenticated user, assessment visibility by role, legal-entity/business-unit/country scope); refusals are 401/403 and logged as `ACCESS_DENIED`. All status changes go through the workflow state machine (`services/workflow.py`, `TRANSITIONS` role matrix + guards).

**1 – Business intake.** `POST /api/assessments` stores the product/service, change type, entity, business unit, countries, channels, transaction and vendor details. Submissions (not drafts) are validated for mandatory fields (422 otherwise). The record gets a `reference_id`, an intake snapshot and triage priority/routing. Workflow status moves DRAFT → SUBMITTED (`POST /workflow/submit` for saved drafts). An alternative route uploads a brief: `POST /analyze-document` (AI field extraction) then `POST /create-with-document`.

**2 – Evidence review.** `POST /advance-stage-async` moves INTAKE → EVIDENCE_COLLECTION (refused while title/description/evidence are empty). Documents (DOCX, PDF, XLSX, TXT, CSV, ZIP) are type/size checked, text-extracted, stored encrypted and embedded into pgvector. A structured business profile (`assessment_intelligence`) is built and evidence gaps/consistency are reported. Two gates must pass before AI analysis: the owner has confirmed the profile (otherwise 400 `PROFILE_NOT_CONFIRMED`, workflow INTAKE_VALIDATION), and expired documents are acknowledged or excluded.

**3 – AI risk identification.** Advancing EVIDENCE_COLLECTION → RISK_IDENTIFICATION (or `POST /analyze-async` for pipeline roles) creates a ProcessingJob executed by an in-process thread pool, which calls `run_risk_assessment_workflow()`. The LangGraph graph is linear: `load_assessment → gather_intelligence → identify_risks → calculate_scores → persist_results`.
`identify_risks` builds the context (intake fields, usable current documents, evidence sources, similar cases via pgvector on Postgres), prompts the LLM to assess the 10 canonical financial-crime risk categories in JSON mode, verifies every quote verbatim against its source (evidence status per factor) and applies the deterministic Stage 4 rules. If the LLM fails operationally, the deterministic RiskEngine produces a provisional `rules_only` result; if that cannot run either, the mode is `unavailable` and no rating is written. `calculate_scores` uses the deterministic `calculate_inherent_risk()` (weighted average of *rated* factors, methodology bands and escalation rules) — a fresh AI run rates nothing, so a score normally appears only after human rating. `persist_results` writes versioned risk factors, the recomputed score and analysis mode, and an audit event. An exception rolls back and fails the job (retryable); success transitions the assessment to RISK_IDENTIFICATION.
Configured provider (`backend/.env`): `LLM_PROVIDER=openai`, `OPENAI_MODEL` unset → default **gpt-4.1-mini**; embeddings **text-embedding-3-small** (1536-d). OpenRouter is supported but not active. Other AI calls outside LangGraph: document extraction, likelihood × impact suggestions, control identification/design, draft narrative.

**4 – Human risk rating.** Performed by the pipeline roles (FCRM Analyst, Manager, Admin); a Business User cannot rate. A provisional rules-only result must first be acknowledged. Each applicable factor is rated likelihood × impact (`PATCH /risk-factors/{id}/rating`), optionally from an AI suggestion; a rating that differs from the suggestion requires a reason and is recorded in the override ledger. Advancing is blocked until all applicable factors are rated; the inherent score is then frozen. Control assessment uses AI-proposed controls; a control outcome must be recorded and the residual band determined (inherent band × control rating grid) before HUMAN_REVIEW, where a draft narrative is generated.

**5 – FCRM review** (workflow ANALYST_REVIEW). The FCRM Analyst saves `PATCH /fcrm-review` (justification + human severity reconciliation; a differing rating needs a justification and creates an override-ledger entry), works review comments and challenge findings, and completes the independent challenge-review sign-off (refused while a HIGH/CRITICAL finding is open). Rework path: `POST /request-information` → INFORMATION_REQUESTED → owner `POST /provide-information` resumes the previous status. The owner then `POST /submit-to-manager`.

**6 – Manager approval** (workflow CHALLENGE_REVIEW). Only the assigned manager or an active delegate may decide — not on their own submission, and an Admin cannot substitute. `POST /manager-decision`: *approve* (gated on resolved comments, a final inherent result and committee readiness) → READY_FOR_COMMITTEE; *return* (comment required) → RETURNED_BY_MANAGER / AMENDMENT_REQUIRED, owner resubmits; *reject* (comment required) → MANAGER_REJECTED.

**7 – Committee decision.** Implemented (not planned) and required for every risk level. `POST /workflow/open-committee-review` (or the first vote) → COMMITTEE_REVIEW. Votes are one current vote per seat, append-only, re-cast with a reason. `POST /committee-decision`: *defer* → DEFERRED and back to review; *approve*, *approve with conditions* or *reject* require a complete decision record and FINAL_DECISION readiness (409/400 otherwise).

**8 – Final decision.** The outcome (APPROVED, APPROVED_WITH_CONDITIONS, REJECTED, or MANAGER_REJECTED) is stored with rationale and decider. Approvals freeze a checksummed decision record and lock the decision; conditions become tracked action items. `POST /workflow/close` → CLOSED is refused while action items/conditions are open. `POST /amend` (Committee/Admin) opens a controlled amendment → REMEDIATION → back to INTAKE. Reassessment triggers can start a child assessment that supersedes the parent on approval.

**9 – Audit trail.** `log_audit_event()` → `audit_events` (CREATED, ANALYSIS, AI_ANALYSIS_DEGRADED, STAGE_ADVANCED, OVERRIDE_APPLIED, MANAGER_*, COMMITTEE_*, DECISION_RECORD_FROZEN, ASSESSMENT_CLOSED…); every status move → `workflow_transitions`; AI value vs human value → `assessment_overrides`; every LLM call → `ai_usage_logs` (+ `ai_evaluation_records`); frozen decision records and vote history; AdminAudit/ACCESS_DENIED/request-timing logs. Read via `/audit`, `/audit/all`, `/audit-export`, `/explain`, with retention policies and legal holds.

**Database.** PostgreSQL on Neon with pgvector, SQLAlchemy 2, Alembic migrations 0001–0022 (start-up guard refuses a schema not at head). SQLite (`risk.db`) remains the default when `DATABASE_URL` is unset (local dev/tests); pgvector features are skipped there.

## Planned, provisional or gaps (kept separate from the diagram)

Nothing in the diagram is "planned" — every step is implemented. These items are partial, provisional or worth tracking:

1. **Provisional governance policy (In Progress — pending governance approval):** SoD exception tiers and Admin/committee exclusivity (R-GOV-01…04), retention periods (2,555-day default), Stage 4 signal definitions (Q-1). Implemented as configuration; APIs return `PROVISIONAL_PENDING_GOVERNANCE_APPROVAL`.
2. **No LOW-risk committee bypass** (Q-5): every assessment goes to committee by design until governance decides otherwise.
3. **Designation holders not assigned:** without Head of FCRM / Governance Owner / Compliance Manager / Challenge Reviewer designations, overrides, SoD exceptions and challenge sign-off cannot be approved, so committee submission is blocked on day one (production readiness checklist §1.6–1.7).
4. **Langfuse tracing is optional and not configured** in `backend/.env`; spans are no-ops today.
5. **OpenRouter** is a supported alternative provider but inactive.
6. **Background jobs are in-process** (`ThreadPoolExecutor`), not a durable queue; interrupted jobs are recovered on start-up but are lost with the process until then.
7. **Deployment drift:** `render.yaml` provisions a Render-managed Postgres (`fromDatabase`), while `backend/.env` points at Neon; uploaded files live on the local filesystem (`backend/uploaded_files`), which is not persistent on a free container host. Load testing and hosted file storage are untested.
8. **Documentation drift:** `backend/AI_ORCHESTRATION.md` still describes the AI scoring six dimensions; the code identifies 10 categories and scores only from analyst ratings.
9. **Schema drift:** 19 FK/index constraints declared in the models are not created by migrations (readiness checklist §5).
10. **Production release approvals open:** change approval for the production migration to head, and rotating the OpenAI key that was shared in chat (noted in `REMAINING_REQUIREMENTS.md`).
