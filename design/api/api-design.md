# API Design

Base path `/api`. JSON over HTTPS. Auth: `Authorization: Bearer <JWT>` (from `POST /api/auth/login`). The current surface has **151 routes on 17 routers**; this design **keeps existing paths** (the frontend and Playwright tests depend on them) and changes behaviour where needed. No parallel "v2" API is introduced.

---

## 1. Cross-cutting rules

| Rule | Detail |
|---|---|
| Authentication | Every `/api/*` route except `POST /api/auth/login` requires a valid JWT (existing global `enforce_api_access`). |
| Visibility | Any `{assessment_id}` / `{document_id}` path param is checked against `_scope_assessments_for_user` (existing). **New:** routes that take `assessment_id` in body/query (`/audit/calculator`, `/check-duplicates`, `/similar-assessments`) apply the same scope. |
| Authorisation | `require(Permission.X)` from one matrix (§2). Replaces the four `require_pipeline_role` definitions (two conflicting role sets). |
| Actor | The acting user is **always** `current_user`. Request fields such as `rated_by`, `added_by`, `changed_by`, `submitted_by`, `resolved_by` are **ignored** (accepted but deprecated for one release, then removed from schemas). Fixes D-03. |
| Reason | State-changing review actions (override, exclude, reject, return, dismiss, accept finding, withdraw, close, amend) require `reason` (min 10 chars; overrides min 20). |
| Read-only GETs | No GET writes to the DB (D-07). Exception retained deliberately: `GET /audit-export` records an `AUDIT_EXPORTED` access event (reading sensitive data is itself auditable). |
| Idempotency | `Idempotency-Key` header **supported** on `analyze-async`, `manager-decision`, `committee-decision`, `committee-votes`, `submit-to-manager`, `draft/generate`. Server stores `(user_id, key, route) → response` for 24 h in a small `idempotency_keys` table. Domain idempotency that already exists stays (active-job reuse, vote upsert, sync dedup). |
| Concurrency | Edits to draft/intelligence/assessment accept `expected_version` (or `lock_version`); mismatch → `409 VERSION_CONFLICT`. |
| Pagination | List endpoints accept `limit` (default 50, max 200) and `offset`; return `X-Total-Count`. Applies to `/assessments`, `/audit` lists, `/users`, `/work-queue`, `/delegations`, `/analysis-runs`. |
| Errors | One envelope (§3). No raw exception text. |
| Request id | `X-Request-ID` accepted or generated; echoed; stored on audit events. |

---

## 2. Permission matrix (target `app/auth/permissions.py`)

`O` = owner of the assessment only. `A` = assigned manager (`manager_id == user.id`) or active delegate. `D` = only if delegation authorises.

| Permission | BUSINESS_USER | FCRM_ANALYST | MANAGER | COMMITTEE_MEMBER | ADMIN |
|---|---|---|---|---|---|
| `assessment.create` | ✔ | ✔ | ✔ | – | ✔ |
| `assessment.edit_intake` (DRAFT/SUBMITTED/AMENDMENT_REQUIRED) | O | ✔ | ✔ | – | ✔ |
| `document.upload` | O | ✔ | ✔ | – | ✔ |
| `intelligence.confirm` | O | ✔ | ✔ | – | ✔ |
| `analysis.run` | – | ✔ | ✔ | – | ✔ |
| `factor.rate` / `factor.exclude` / `factor.add` | – | ✔ | ✔ | – | ✔ |
| `control.manage` (map, assess, conditions) | – | ✔ **(change: today MANAGER/ADMIN only)** | ✔ | – | ✔ |
| `challenge.resolve` / `challenge.accept` | – | ✔ (resolve) | ✔ | – | ✔ |
| `override.create` | – | ✔ | ✔ | – | ✔ |
| `draft.generate` / `draft.edit` / `draft.accept` | – | ✔ | ✔ | – | ✔ |
| `info.request` | – | ✔ | ✔ | ✔ | ✔ |
| `info.provide` | O | ✔ | ✔ | – | ✔ |
| `comment.post` (visibility ALL) | ✔ | ✔ | ✔ | ✔ | ✔ |
| `comment.post` (restricted visibility) | – | ✔ | ✔ | ✔ | ✔ |
| `submit_to_manager` | O | – | – | – | ✔ (new) |
| `manager.decide` | – | – | A | – | ✔ |
| `committee.vote` / `committee.decide` | – | – | D | ✔ | ✔ |
| `committee.amend` | – | – | – | ✔ | ✔ |
| `condition.complete` (committee conditions) | – | ✔ | ✔ | ✔ | ✔ **(change: today any user)** |
| `reassessment.manage` | O (flag) | ✔ | ✔ | – | ✔ **(change: today any user)** |
| `audit.read_assessment` | visible | visible | visible | visible | ✔ |
| `audit.read_all` | – | – | – | – | ✔ **(change: today any user — D-01)** |
| `audit.export` | – | ✔ | ✔ | ✔ | ✔ |
| `methodology.read` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `methodology.manage` (create/edit/clone/activate) | – | – | – | – | ✔ **(change: create/edit today any user — D-02)** |
| `reference_data.attest` | – | – | – | – | ✔ |
| `reports.read` | – | ✔ | ✔ | ✔ | ✔ |
| `admin.*` (users, system, retention, legal hold) | – | – | – | – | ✔ |

Separation-of-duties rules (existing, retained and centralised): cannot decide own submission; committee member who approved as manager cannot vote/decide (now applied to non-delegated votes too); delegate cannot re-delegate.

Open question Q-01 confirms the `control.manage` change for analysts.

---

## 3. Error envelope and codes

```json
{ "error": { "code": "INVALID_TRANSITION", "message": "…", "details": {}, "request_id": "…" } }
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `VALIDATION_FAILED` | Business validation (e.g. rating outside methodology scale) |
| 401 | `UNAUTHENTICATED` | Missing/expired token |
| 403 | `FORBIDDEN`, `NOT_VISIBLE`, `SEPARATION_OF_DUTIES`, `DELEGATION_INVALID` | Authorisation |
| 404 | `NOT_FOUND` | Unknown id (after visibility check) |
| 409 | `ASSESSMENT_LOCKED`, `INVALID_TRANSITION`, `GATE_NOT_MET`, `VERSION_CONFLICT`, `ANALYSIS_IN_PROGRESS`, `ALREADY_DONE`, `METHODOLOGY_LOCKED` | State conflicts |
| 413 | `UPLOAD_TOO_LARGE` | > `MAX_UPLOAD_MB` |
| 422 | `MISSING_FIELDS` (`details.missing_fields[]`), `REASON_REQUIRED`, `SCHEMA_INVALID` | Input shape |
| 429 | `RATE_LIMITED` | Login throttling (new, 10/min/IP) |
| 502 | `AI_PROVIDER_ERROR` | Only for synchronous AI helper endpoints (suggest ratings, identify controls); analysis uses jobs instead |
| 500 | `INTERNAL_ERROR` | Unexpected; message generic, details logged with `request_id` |

`GATE_NOT_MET` carries `details.missing[]` with machine-readable items, e.g. `{"code":"FACTORS_UNRATED","count":3}`, `{"code":"PROFILE_NOT_CONFIRMED"}`, `{"code":"BLOCKING_FINDINGS","ids":[12,15]}`, `{"code":"DEGRADED_NOT_ACKNOWLEDGED"}`. This replaces misleading gate messages found in E2E (TESTING.md §8 item 8).

---

## 4. Requested endpoints → existing/target mapping

| Requested | Target endpoint | Status |
|---|---|---|
| `POST /api/assessments` | `POST /api/assessments` (JSON) and `POST /api/assessments/create-with-document` (multipart) | Exists — keep; retire `create-from-document` (duplicate, no size check, D-18) |
| `GET /api/assessments` | same, + `workflow_status`, `risk_level`, `owner=me` filters | Exists — extend |
| `GET /api/assessments/{id}` | same | Exists |
| `PUT /api/assessments/{id}` | `PATCH /api/assessments/{id}` (partial update is the right verb) | Exists — keep PATCH |
| `POST /api/assessments/{id}/analyze` | `POST /api/assessments/{id}/analyze-async` (202 + job) | Exists — **make async the only path**; sync `/analyze` kept for tests only (`APP_ENV=test`) |
| `GET /api/assessments/{id}/intelligence` | same | Exists |
| `GET /api/assessments/{id}/findings` | `GET /api/assessments/{id}/risk-factors` (AI/rules/manual factors) + `GET /api/assessments/{id}/challenge-review` (review findings) | Exists — no alias needed |
| `GET /api/assessments/{id}/recommendations` | `GET /api/assessments/{id}/draft` (`analyst_recommendation`, `recommended_conditions`, `required_approvals`) + challenge `recommended_action` | Exists |
| `GET /api/assessments/{id}/scores` | **New** `GET /api/assessments/{id}/scores` — read-only aggregate of current inherent + residual + confidence + provenance | New (thin, read-only) |
| `POST /api/assessments/{id}/review` | `PATCH /api/assessments/{id}/fcrm-review`, `POST /overrides`, `POST /comments`, `PATCH /challenge-findings/{fid}/resolve|accept` | Exists — fix actor |
| `POST /api/assessments/{id}/approve` | `POST /api/assessments/{id}/manager-decision`, `POST /committee-decision`, `POST /committee-votes` | Exists |
| — | **New** `GET /api/assessments/{id}/analysis-runs`, `GET /api/analysis-runs/{run_uuid}` | New |
| — | **New** `GET /api/assessments/summary` (dashboard counts) | New |
| — | **New** `GET /health/ready` | New |

---

## 5. Core endpoint specifications

### 5.1 `POST /api/assessments` — create
- **Purpose:** create a draft or submitted change request.
- **Request:** `AssessmentCreate` — `title` (req), `change_type` (req, enum `CHANGE_TYPES`), `is_draft` (bool, default true), intake fields (§ Create form in `ux/user-journeys.md`). `submitted_by` ignored.
- **Validation:** if `is_draft=false` all mandatory intake fields present, `expected_launch_date` ISO date → else `422 MISSING_FIELDS`.
- **Auth/Z:** `assessment.create`.
- **Response:** `201 AssessmentResponse`.
- **DB:** insert `assessments` (`owner_id`, `submitted_by_id` = user, `reference_id` when submitted), `workflow_transitions` (`CREATED`), `audit_events` (`CREATED`); if submitted → triage/routing fields + `ACKNOWLEDGMENT` audit. One transaction.
- **Idempotency:** none (client prevents double-submit; duplicate check endpoint warns).

### 5.2 `GET /api/assessments` — list
- **Query:** `limit`, `offset`, `search`, `status`, `workflow_status`, `risk_level`, `legal_entity`, `business_unit`, `owner=me`, `sort=updated_at|-updated_at|launch_date`.
- **Response:** `200 AssessmentSummary[]` (slim projection, not full intake) + `X-Total-Count`.
- **Change:** remove the in-request `escalate_overdue_assessments` commit (moves to the scheduled escalation loop).

### 5.3 `GET /api/assessments/summary` — dashboard (new)
- **Response:** `{ total, by_workflow_status: {DRAFT: n, …}, by_risk_level: {LOW, MEDIUM, HIGH, CRITICAL, UNRATED}, pending_my_action: n, launching_within_7_days: n, provisional: n }` — all scoped to caller visibility.
- **DB:** grouped `COUNT` queries; read-only.

### 5.4 `PATCH /api/assessments/{id}` — edit intake
- **Request:** `AssessmentUpdate` + `expected_version`.
- **Validation:** allowed only when `workflow_status ∈ {DRAFT, SUBMITTED, AMENDMENT_REQUIRED}` → else `409 ASSESSMENT_LOCKED`.
- **Z:** `assessment.edit_intake`.
- **DB:** update + `before_json/after_json` audit; submitting via this route is **removed** (use `POST /workflow/submit` only).

### 5.5 `POST /api/assessments/{id}/analyze-async` — run analysis
- **Purpose:** start the LangGraph risk-identification run.
- **Request:** optional `{ "reason": "Re-run after new evidence uploaded" }` (required when a previous successful run exists).
- **Pre-conditions (→ `409 GATE_NOT_MET`):** status ∈ {EVIDENCE_COLLECTION, RISK_IDENTIFICATION, REMEDIATION}; intelligence confirmed; not locked.
- **Z:** `analysis.run`.
- **Response:** `202 { job: ProcessingJobResponse, analysis_run_id: uuid }`. If a job is already QUEUED/RUNNING → `200` with that job (existing behaviour) — also guaranteed by the partial unique index.
- **DB:** insert `processing_jobs` + `analysis_runs(QUEUED)`; audit `ANALYSIS_REQUESTED`. Worker writes factors, calculation, run completion, and (from EVIDENCE_COLLECTION) the transition to RISK_IDENTIFICATION.
- **Idempotency:** `Idempotency-Key` supported.

### 5.6 `GET /api/processing-jobs/{job_id}` — progress
- **Response:** `{ id, status: QUEUED|RUNNING|SUCCEEDED|PARTIAL|FAILED, progress 0–100, stage_message, stage_key, is_incomplete, incomplete_reasons[], error_message, attempts, max_attempts, analysis_run_id }`.
- **New field `stage_key`**: one of `validate_input`, `load_context`, `identify_risks`, `verify_evidence`, `tag_and_aggregate`, `score_provisional`, `persist_run` so the UI can render a stepper.

### 5.7 `GET /api/assessments/{id}/risk-factors`
- **Response:** `RiskFactorResponse[]` (current versions). Each includes `rated: bool` (explicit — the UI must not infer from `score=0`), `rating_source`, `ai_suggestion {likelihood, impact, rationale}`, `evidence[]` (with `origin`, `verification`), `evidence_status`, `rejected_indicators[]`, `missing_information[]`, `crime_typologies[]`, `inference {rationale, misuse_scenario}` (labelled AI inference), `analysis_run_id`.

### 5.8 `PATCH /api/assessments/{id}/risk-factors/{fid}/rating`
- **Request:** `{ likelihood: int, impact: int, reason?: string }` — `reason` **required** if it differs from the AI suggestion (`rating_source = ANALYST_OVERRIDE`).
- **Validation:** values within methodology scales → `400 VALIDATION_FAILED`.
- **Z:** `factor.rate`. Lock check.
- **DB:** update factor (score via `compute_factor_score`), `rated_by_id`; recompute inherent (new calc version) and residual preview; challenge recompute; audit with before/after. One transaction.

### 5.9 `GET /api/assessments/{id}/scores` (new, read-only)
```json
{
  "inherent": { "calculation_id": 88, "version": 4, "score": 64.0, "band": "HIGH",
                "is_provisional": false, "confidence_level": "MEDIUM",
                "rating_coverage": 1.0, "evidence_coverage": 0.67,
                "triggered_rules": [{"rule_code": "SANCTIONS_EXPOSURE_001", "band_before": "HIGH", "band_after": "CRITICAL"}],
                "override": null,
                "methodology": {"id": 3, "version": "v2", "fingerprint": "sha256:…"},
                "scoring_engine_version": "1.0.0" },
  "residual": { "calculation_id": 41, "band": "MEDIUM", "score": 44.0, "control_rating": "PARTIAL",
                "floors_applied": [], "grid_version": "1.0", "frozen": false },
  "analysis_run_id": "5b1e…"
}
```
Fields `inherent`/`residual` may be `null` with `reason` (`"NOT_CALCULATED"`, `"UNRATED"`).

### 5.10 `POST /api/assessments/{id}/inherent-risk/override`
- **Request:** `{ override_band: "HIGH", override_value?: number, reason: string(min 20), comment?: string }`.
- **Rules (deterministic, new):** cannot lower below a floor set by a `non_mitigable` triggered rule → `409 GATE_NOT_MET {code:"NON_MITIGABLE_FLOOR"}`; a downward override by ≥1 band auto-creates a HIGH challenge finding `OVERRIDE_REDUCED_RISK` that the manager must accept.
- **Z:** `override.create`.
- **DB:** new calc version with `calculated_*` preserved and `override_*` set; `assessment_overrides` row (`ai_value` = calculated band, `human_value`, `previous_value_source=CALCULATED`); audit.

### 5.11 `POST /api/assessments/{id}/manager-decision`
- **Request:** `{ decision: "approve"|"return"|"reject", comment: string }` (+ `Idempotency-Key`).
- **Gates (approve):** no unresolved comments, inherent not provisional (unless overridden), no blocking findings, residual frozen → `409 GATE_NOT_MET`.
- **Z:** `manager.decide`; SoD.
- **DB:** transition, `manager_*` fields (incl. delegation), audit; production also `approval_decisions`.

### 5.12 `POST /api/assessments/{id}/committee-decision`
- **Request:** `CommitteeDecisionRequestV2` — `decision`, `rationale` (req), `conditions[]` (req for `approve_with_conditions`: description, owner, due_date, priority).
- **Gates:** `decision_record_service.missing_requirements` empty.
- **DB:** transition; `committee_conditions` + `action_items`; `decision_records` frozen (format 1.1, includes run ids & prompt versions); next review date; audits. Single transaction.

### 5.13 `GET /api/assessments/{id}/analysis-runs` and `GET /api/analysis-runs/{run_uuid}` (new)
- **List response:** `[{ run_uuid, run_type, status, workflow_version, prompt {key, version, sha256}, requested_model, response_model, methodology_fingerprint, assessment_mode, ai_status, started_at, duration_ms, requested_by }]`.
- **Detail** adds `output_summary`, `input_sha256`, `reference_snapshot_ids`, `trace_id`; `input_snapshot` and `raw_output` only with `audit.export`.
- **Z:** visibility; detail payloads need `audit.export`.

---

## 6. Other endpoint changes (delta list)

| Endpoint | Change | Defect |
|---|---|---|
| `GET /api/assessments/audit/all` | ADMIN only + pagination | D-01 |
| `POST/PATCH /api/risk-methodologies` | ADMIN only; `is_active` on create ignored (use `/activate`) | D-02 |
| `POST /controls/{cid}/assess-design`, `POST /risk-factors/{fid}/identify-controls` (body `auto_create`) | Define the missing `AuditAction` members and fix the audit call; single commit; return 502 `AI_PROVIDER_ERROR` on AI failure | D-04 |
| `reassessment/*`, `PATCH committee-conditions/{cid}` | Add permissions | D-05 |
| `GET /inherent-risk`, `/residual-risk`, `/control-summary`, `/challenge-review`, `/challenge`, `/decision-package`, `/reassessment/check-triggers`, `/retention*`, `/challenge-triggers`, `/reports/*`, `/workflow/*` GETs | Read-only; compute on write or return `null` + reason | D-07 |
| `PATCH /advance-stage` | From EVIDENCE_COLLECTION: enqueue analysis (202); other stages: single transaction; `GATE_NOT_MET` details | D-08 |
| `PATCH /{id}/status` (legacy) | Deprecated → 410 after Stage 3 | duplication |
| `GET/PATCH /{id}/challenge` (legacy) | Deprecated; gate moves to `challenge_engine` findings | duplication |
| `POST /create-from-document` | Deprecated (use `create-with-document`) | D-18 |
| `PATCH /fcrm-review` | `reviewed_by_id` = user; audit action `FCRM_REVIEW_SAVED` | D-12 |
| `GET /occ-risk-profile` | Hidden from UI; ADMIN-only; out of FC scope | scope |
| Async upload handlers | Run extraction in threadpool (`run_in_threadpool`) or as job | D-18 |
| `POST /api/auth/login` | Throttle 10/min/IP, audit `LOGIN_FAILED` / `LOGIN_SUCCEEDED` | security |

All other existing endpoints (inventory in `docs` of this stage's inspection: approvals, comments, votes, conditions, delegations, action items, workflow actions, documents, reports, system, reference data) are **retained unchanged** apart from the cross-cutting rules in §1.
