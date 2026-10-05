# Remaining Requirements: Gap Analysis, Plan and Checklist

**Basis:** repository audit on 2026-10-02 (working tree, uncommitted), against
`Risk Assessment Workbench Requirements.pdf` (the brief). Baseline before this
work: 285/285 backend tests pass; frontend type-check clean; Playwright 9/12
(3 pre-existing failures, see §5).

Legend: ✅ complete · 🟡 partial · ❌ missing

The checklist in §6 is the live status and is updated as each phase lands.

---

## 1. Specification sources for items flagged as "unspecified"

| Item | Where the brief specifies it | Consequence |
|---|---|---|
| Five evidence categories (R5.4) | Stage 5, R5.4: *Direct evidence, Extracted information, System interpretation, Analyst commentary, Assumptions* | Specified — build to this text. Kept **alongside** the Stage 19 four-way FACT / ASSUMPTION / RECOMMENDATION / DECISION labels, which are a separate requirement. |
| Stage 4 fixed rules | Stage 4 acceptance criteria: cross-border payment product → geographic, product, transaction and sanctions factors considered; remote digital channel → channel and authentication risk; third-party processor → third-party risk | Specified — implemented as deterministic rules. The *signals* that detect "cross-border", "remote digital" and "third-party processor" are not in the brief (see Q-1). |
| Retention periods | R16.4: "configured retention periods and legal holds" — no periods given | **Not specified.** No new periods are invented; the existing 2,555-day default is kept and made editable (Q-3). |
| LOW-risk committee workflow | Not in the brief; `required_approvals` default (LOW → ANALYST only) is an earlier design decision | **Not changed** (per instruction). Every assessment still goes to committee. |
| SoD approved exception (R15.2) | "unless an approved exception exists" — approver not named | Approver rule is a design assumption (Q-2). |

## 2. Gap analysis

### 2.1 Evidence traceability

| Req | Status before | What exists | Gap |
|---|---|---|---|
| R2.4 confidence + page for extracted fields | 🟡 | Risk-factor evidence quotes carry document, version, page (PDF `[Page N]`), quote-verified flag (`risk_engine/evidence.py`). Profile extraction stores `raw_extraction`, one `source_document_id`, whole-record confirmation. | No per-field source/page/confidence/extraction date on the profile; extraction method (AI vs rules) not stored; `source_document_id` wrong for multi-file uploads; provenance not shown in UI. |
| R2.6 / Stage 2 AC acknowledgement of expired documents | 🟡 | Expired-document warning in `/evidence-gaps` (string compare, current docs only). | No acknowledgement, no gate, expired docs silently feed AI evidence; outdated approved sources attach without acknowledgement. |
| R3.4 snapshot before editing | 🟡 | Intelligence PATCH requires a reason after confirmation and un-confirms. | `PATCH /assessments/{id}` overwrites with no snapshot, no old/new values, no reason after validation; no version history. |
| R5.4 five-way categories | 🟡 | Four Stage 19 kinds in `services/explainability_statements.py`. | No R5.4 category; extracted profile fields and approved-source passages are not emitted as statements. |
| Stage 4 fixed rules | 🟡 | AI assesses all 10 categories; missing ones filled in as INSUFFICIENT_EVIDENCE. | Applicability is the AI's call; nothing deterministic forces the brief's three scenarios; indicators cannot be edited on AI factors. |

### 2.2 Risk governance

| Req | Status before | What exists | Gap |
|---|---|---|---|
| R7.2/R10.2 edit and unmap controls | 🟡 | `PATCH /controls/{id}` edits metadata in place; versioned `ControlAssessment`. | Edit is in place (no version), audit has no actor, no recompute; no unmap; no remap; **bug:** API sends `ADEQUATE/INADEQUATE`, engine checks `DESIGN_INADEQUATE`, so design inadequacy never raises a gap. |
| R6.7/R10.2/R10.4 overrides without overwriting | 🟡 | Factor-rating override writes a ledger row but **overwrites** factor likelihood/impact; inherent override keeps calculated score but overwrites `assessment.inherent_score`; residual confirmation stores beside calculated (good pattern). | Generic `POST /overrides` changes nothing and trusts a client-supplied `ai_value`; FCRM `human_ratings` never applied; overrides absent from decision package. |
| R11 mandatory challenge review | 🟡 | Every assessment passes the challenge stage; HIGH findings block. | No challenge-review completion record (who/when/outcome); nothing recorded when no trigger fires; legacy challenge outcome has no actor. |
| R12.6 re-cast votes immutable | ❌ | Unique `(assessment_id, member_id)`; re-cast **overwrites**. | Need append-only vote history; current vote = latest. |
| R15.2 SoD approved exception | ❌ | Unconditional block (owner, assigned/deciding manager). | No exception workflow. |

### 2.3 Security and compliance

| Req | Status before | Gap |
|---|---|---|
| R15.3/R15.5 secure downloads | 🟡 | CONFIDENTIAL originals downloadable by anyone who can see the assessment; views/downloads not audited; the route's RESTRICTED refusal not audited; evidence quotes from confidential documents returned unmasked; header filename not RFC 5987-safe. |
| R16.4 configurable retention | 🟡 → ✅ P5 | One global value, no UI, logged as generic STATUS_CHANGE without previous value, updated in place; soft-deleted assessments still readable by id. **P5:** versioned, independently approved, record-type-aware policy; legal-hold history; one eligibility rule; read-only report; dedicated audit (periods provisional). |
| R19 encryption at rest | 🟡 → ✅ P7 (with open items) | Files encrypted only when `FILE_ENCRYPTION_KEY` set; DB columns plaintext (relies on provider); DB TLS not enforced in code. Needs verification evidence and documentation. **P7:** TLS enforced, production key requirement, verification evidence; legacy uploads and backups still plaintext. |

### 2.4 Reassessment (Stage 18)

| Step | Status before | Gap |
|---|---|---|
| Due queue | 🟡 → ✅ P6 | Owner/manager/admin only; date triggers only; no actions. **P6:** every open trigger, analysts within scope, server actions. |
| Flag / resolve trigger | 🟡 | Backend only, no UI; denials not logged; generic audit action. |
| Start reassessment | 🟡 | Clones intake fields only; parent's open triggers stay open; UI role gate wrong (shows to committee, hides from owner/analyst). |
| Reused fields with source/age (R18.3) | 🟡 | Stored, never shown. |
| Compare versions (R18.2) | 🟡 | Factors by category set, controls by type, conditions by text. |
| Parent "under reassessment" / superseded | ❌ → ✅ P6 | Nothing happens to the parent. **P6:** `reassessment_state` flag, superseded on child approval, released otherwise. |
| Final approval of child | 🟡 | Normal approval; parent keeps raising expiry triggers. |

### 2.5 Testing and validation

| Item | Status before | Gap |
|---|---|---|
| Backend pytest | 🟡 | 285 tests; no route × role sweep; several areas only in legacy scripts. |
| Playwright E2E | 🟡 | 12 tests on intake/analysis/workflow; 3 failing; no governance, committee, download, reassessment journeys. |
| Role/entity access validation | 🟡 | Roles/scope tests for Auditor/Executive only. |
| Migrations 0011–0016 on staging Neon | ❌ | Only exercised against SQLite at app import. No staging database is configured (`backend/.env` points at the primary Neon branch). **Blocked on Q-4.** |

## 3. Implementation plan

Phases are ordered by risk: open security exposure first, then integrity of
governance records, then traceability, then journeys. Each phase ends with the
full backend suite, type-check, and the phase's new tests.

| Phase | Scope | Depends on | Migration | Key risk |
|---|---|---|---|---|
| P1 Security | Secure document downloads + access audit; mask confidential evidence quotes; block soft-deleted assessments for non-Admin/Auditor | — | none | Over-restricting a role that legitimately needs a file (mitigated: rule table + tests per role) |
| P2 Governance records | Append-only committee votes; overrides beside calculated values; challenge-review sign-off; control edit/remap/unmap with versions; design-adequacy bug | — | 0017 | Vote uniqueness change on existing data (migration keeps existing rows as version 1) |
| P3 SoD exceptions | Request → independent approval → time-boxed, single-assessment exception consumed by the SoD check | P2 | 0017 | Exception becoming a bypass (mitigated: deterministic rules, no self-approval, audit) |
| P4 Evidence traceability | Field provenance; expiry acknowledgement + gate; intake snapshots + old/new values; R5.4 categories; Stage 4 rules; indicator editing | — | 0020 (`0020_evidence_traceability`) | AI prompt change for field evidence (provenance derived deterministically from quote verification, never from model-reported confidence) |
| P5 Retention | Versioned, admin-editable policy with reason; dedicated audit action; admin screen | P1 | 0019 (`0019_retention_lifecycle`) | — |
| P6 Reassessment | Parent marking, trigger linking, supersede on approval, flag/resolve UI, reused-field display, structured compare | — | 0019 | Parent status change must not break existing approved records (flag column, not a new pipeline status) |
| P7 Encryption | Verify + document; enforce TLS for remote Postgres; production key requirement | — | none | Cannot verify provider-side encryption from code (documented as such) |
| P8 Tests | Permission matrix sweep; E2E journeys; fix the 3 baseline failures; migration test script; staging Neon run (on confirmation) | all | — | Staging DB access |


**Execution order (agreed 2026-10-02):** P1 → P2 → P4 → P6 → P3 → P5 → P7, with tests in every phase and P8 completing E2E, permission sweep and migration validation.

## 4. Provisional decisions (2026-10-02) and open governance questions

**Decision pack (2026-10-03):** every open governance question (G-1 to G-20; draft proposed answers received 2026-10-03 are included with their change-from-today impact, **not applied**), with today's provisional default, options, a recommendation, urgency (before go-live / before disposal / can follow) and where the answer is configured, is in `docs/GOVERNANCE_DECISIONS.md`, with a decision record to complete. Gap found while comparing, **fixed 2026-10-03**: the shared workflow rule listed Admin for the three manager transitions (SUBMITTED_TO_MANAGER → READY_FOR_COMMITTEE / RETURNED_BY_MANAGER / MANAGER_REJECTED). The manager-decision endpoint already refused Admins (assigned manager or delegate only), but the rule advertised Admin as allowed and would have let any other caller through, and refusals weren't logged. Admin removed from the rule; refusals logged as `ACCESS_DENIED`; `tests/api/test_manager_decision_authority.py` (6). Assessment #6 (0021 backfill) is resolved by migration 0022.

The project gave these **provisional** decisions. Each one is built as configuration or clearly marked as pending, so none becomes permanent policy by default.

| # | Topic | Provisional decision | Still open for governance |
|---|---|---|---|
| Q-1 | Stage 4 signals | Deterministic rules with configurable, versioned signal definitions; initial signals are the documented constants | Business validation of the signal lists |
| Q-2 | SoD exception | **Implemented (P3)**: tiered independent approval by designated approvers (Governance Owner / Head of FCRM / Compliance Manager; Committee Chair for high-risk, long, enterprise-wide or repeated), declaration, expiry and revocation; Admins are not approvers | R-GOV-01 tiers and thresholds: pending governance approval |
| Q-3 | Retention | **Implemented (P5)**: the 2,555-day default kept as provisional v1 (not compliance-approved); versioned policies with independent approval; legal holds with history and independent release; record-type-specific structure (only ASSESSMENT configured) | The approved periods; D-1 to D-4 (see the P5 report) |
| Q-4 | Neon | Never migrate the primary database; prepare validation for an isolated staging branch; connection string supplied through secure configuration | Staging branch provisioning |
| Q-5 | LOW-risk committee | Keep the committee workflow for all risk levels; no bypass | The LOW-risk governance requirement |
| Q-6 | Admin voting | **Implemented (P3)**: mutually exclusive by default; only an approved, declared dual-role SoD exception lets an Admin act as a committee member, with same-case administration refused; the P2 env switch is retired | R-GOV-02: pending governance approval |
| Q-7 | Who may open classified originals (P1) | CONFIDENTIAL and RESTRICTED originals follow the existing unmasked-text rule: the assessment owner, FCRM analysts and Admins (`UNMASKED_ROLES` in `services/data_masking.py`) | Whether reviewing managers, committee members or auditors need the originals (they currently get masked text only) |

## 5. Pre-existing E2E failures (baseline)

**✅ Fixed in P8 (2026-10-03)** by correcting the test setup (the owner confirms the profile first); the gate was not changed. Playwright is now 30/30.

1. `analyze-assessment.spec.ts` "an analyst can analyze straight from the dashboard": the dashboard Analyze action on an unconfirmed profile never reaches "Risk Assessment in Progress".
2. `status-workflow.spec.ts` "an analyst cannot finalise inherent risk…" and 3. "a business user cannot rate risk factors": the test setup calls `/analyze` before the profile is confirmed, and the R3.3 gate now refuses that, as it should. Fix: correct the test setup; the gate is not weakened.

## 6. Phase reports

### P1 Security: ✅ complete (2026-10-02)

**Checklist**

| # | Item | Status |
|---|---|---|
| 1 | One rule for opening original files: CONFIDENTIAL/RESTRICTED originals go only to viewers who may read the unmasked text; refusals logged as `ACCESS_DENIED` with the assessment id | ✅ |
| 2 | Every original-file view and download audited (`DOCUMENT_VIEWED` / `DOCUMENT_DOWNLOADED`, recording user, document id, version and classification, never content) | ✅ |
| 3 | Safe headers: `?disposition=inline\|attachment` (default attachment), RFC 5987 filename with a sanitised ASCII fallback, `Content-Security-Policy: sandbox` on inline; `no-store` and `nosniff` come from the middleware | ✅ |
| 4 | `can_open_original` on document responses; the UI disables Download and previews the masked text instead of the file | ✅ |
| 5 | Evidence quotes on risk factors masked for viewers who see the source document masked (stored records unchanged) | ✅ |
| 6 | Soft-deleted assessments return 404 to everyone except Admins and Auditors (including their documents and files); every change is refused with 409 and logged; they are removed from the work queue, reassessment alerts, action escalations and the review-date sweep | ✅ |

**Behaviour change to note:** before P1, a CONFIDENTIAL original was served to anyone who could see the assessment. It now follows the same rule as RESTRICTED. Reviewing managers and committee members see the masked text only (Q-7).

**Files changed**

- `backend/app/services/data_masking.py`: `can_open_original_file`, `mask_evidence_records`
- `backend/app/api/assessments.py`: download route rewritten (rule, audit, headers, `disposition`); `can_open_original`; evidence masking in every risk-factor response (`_masked_document_ids`)
- `backend/app/file_processing/storage.py`: `content_disposition`
- `backend/app/services/audit_service.py`: `DOCUMENT_VIEWED`, `DOCUMENT_DOWNLOADED`
- `backend/app/auth/dependencies.py`: `log_denied_attempt(..., assessment_id=)`
- `backend/app/auth/access.py`: soft-deleted read gate (`DELETED_ASSESSMENT_READERS`) and write gate
- `backend/app/models/audit_trail.py`: `soft_deleted_assessment_ids`, `is_soft_deleted`
- `backend/app/api/workflow.py`, `backend/app/services/reassessment_service.py`: deleted assessments excluded from queues and the sweep
- `backend/app/schemas/assessment_document.py`: `can_open_original`
- `frontend/src/api/assessments.ts`, `frontend/src/components/AssessmentWorkflow.tsx`: disposition parameter, disabled Download, masked preview
- `backend/tests/api/test_p1_security.py`: new, 8 tests
- `docs/NON_FUNCTIONAL_REQUIREMENTS.md`: masking row updated

**Migrations:** none.

**Test results**

- `pytest` (unit/api/workflow): **293 passed** (285 baseline + 8 new)
- Legacy suites: `test_stage19_nfr.py` 22, `test_stage14_workflow.py` 9, `test_aw7_delegation.py` 13 and `test_stage17_reporting.py` 7, all passed
- `tsc -b`: clean. ESLint: no findings in changed lines (11 pre-existing findings elsewhere in `AssessmentWorkflow.tsx`)
- Not yet covered: a browser E2E check of the disabled Download button (planned for P8)

**Acceptance criteria covered**

- R15.3 AC "Given restricted evidence, unauthorized users cannot view or download it": `test_classified_original_only_for_unmasked_readers` (CONFIDENTIAL and RESTRICTED), `test_evidence_quotes_from_classified_documents_are_masked`
- R15.5 "log document views, downloads" and "records the denied attempt": `test_views_and_downloads_are_recorded_separately`, plus the denial assertions above
- R16.1/R16.4 "no completed assessment history can be deleted without a controlled, logged process" (a soft-deleted assessment can't be seen or changed): `test_soft_deleted_assessment_is_gone_except_for_admins_and_auditors`, `test_soft_deleted_assessment_leaves_work_queues`

**Unresolved:** Q-7. Masking is pattern-based (cards, IBANs, e-mails, IDs, phones) and is not a guarantee. The bearer token is still kept in `localStorage`; httpOnly cookies are a production item.

### Incident 2026-10-02: primary Neon database migrated without approval

**What happened.** During P2 a bare `python -c "import app.main"` import check ran from `backend/` with no test `DATABASE_URL`. `app/database.py` loaded `backend/.env`, and `app/main.py` ran `alembic upgrade head` at import. The primary Neon database (`ep-falling-water-…-pooler`, `neondb`) was migrated to `0017_governance_records`. The decision was to **leave it at 0017**: no downgrade, no restore.

**Read-only verification afterwards** (`SET TRANSACTION READ ONLY` queries):

| Check | Result |
|---|---|
| `alembic_version` | `0017_governance_records` |
| 0011 `action_items.closure_requested_by_id` | present |
| 0012 `challenge_trigger_configs.disabled_triggers` | present |
| 0013 `ai_usage_logs.prompt_version` | present |
| 0014 `recommended_conditions` table, `residual_risk_calculations.confirmed_*` | present |
| 0015 `users.scope_*` | present |
| 0016 `approved_sources`, `source_evidence_links` | present |
| 0017 vote columns, constraints `uq_committee_vote_version` + partial `uq_committee_vote_current`; old `uq_committee_vote_member` removed; override review columns; `challenge_review_signoffs`, `control_revisions` | present |
| Existing data | 10 assessments, 236 audit events, 2 committee votes (now v1, current, `cast_by_id` = member), 1 override (unchanged, review fields NULL); new tables empty |
| Rows written using any 0011–0016 feature | none (0 prompt versions, recommended conditions, approved sources, scoped users, residual confirmations) |
| Latest application activity | 2026-10-01 11:05 UTC (latest audit event and assessment update) |

**Were 0011–0016 applied before the incident?** All of their schema is present and applied correctly. But no data uses them, and the last application activity (2026-10-01 11:05 UTC) predates their authoring. The previous session also recorded that they "will apply on the next app start". So they were **most likely applied by the same accidental run, together with 0017**. The database can't prove this, because Alembic keeps no history table. Neon's console operation log would show the DDL timestamps.

**Compatibility of deployed code with the schema.**
- No deployment of this working tree exists. Git `HEAD` (`2cbce9f`) has no Alembic, Dockerfile or `render.yaml`, nothing was listening locally, and the last local server log is from 2026-09-23.
- Code without migration 0017 (the working tree before P2, or any other checkout) would fail at start-up, because Alembic can't locate revision `0017_governance_records`. Only code that includes 0017 can now start against this database.
- Schema-wise, older ORM code would work: every added column is nullable or has a server default. The only behaviour difference is that older code re-casts a vote by updating the row in place, which the database still permits.

### Migration safety guard: ✅ complete (2026-10-02)

| # | Item | Status |
|---|---|---|
| 1 | Importing the application never touches a database: no migration, no admin seeding, no `CREATE EXTENSION` at import | ✅ |
| 2 | `prepare_database()` (`app/main.py`) runs on server start-up and explicitly in tests and scripts | ✅ |
| 3 | Local databases (SQLite, Postgres on localhost) migrate automatically | ✅ |
| 4 | Remote databases migrate only with `MIGRATION_AUTHORIZED_HOST=<exact host>` or `--confirm-host`; protected hosts (`PROTECTED_DATABASE_HOSTS`) also need `MIGRATION_ALLOW_PROTECTED_HOST=true` / `--allow-protected-host` | ✅ |
| 5 | Start-up against a remote database that is behind the code refuses to start, without migrating | ✅ |
| 6 | `alembic/env.py` enforces the same rule, so the raw `alembic` CLI can't bypass it | ✅ |
| 7 | `python -m app.migrations status` (read-only) and `upgrade` CLI | ✅ |
| 8 | Refusal messages never include credentials | ✅ |

**Files changed:** `backend/app/migrations.py` (rewritten), `backend/app/main.py`, `backend/app/database.py`, `backend/alembic/env.py`, `backend/tests/conftest.py`, `backend/tests/e2e_server.py`, the seven legacy `backend/test_*.py` scripts (explicit `prepare_database()`), `backend/tests/unit/test_migration_guard.py` (new, 12 tests), `backend/.env.example`, `render.yaml`, `docs/DEPLOYMENT.md`.

**Test results (all against isolated databases):**
- `test_migration_guard.py` 12/12 and `test_migration_0017.py` 3/3.
- Full `pytest`: 308 passed.
- Legacy scripts: `test_stage19_nfr.py` 22/22; `test_degraded_mode.py`, `test_phase1_evidence_scoring.py` and `test_phase2_governance.py` all checks pass.
- `test_stage14_workflow.py` (1 failure), `test_aw7_delegation.py` (4) and `test_stage17_reporting.py` (2) fail **because of in-progress P2 behaviour**, not the guard: the new mandatory challenge sign-off gate, and overrides that now require a real `entity_id`. They'll be updated in P2.
- Playwright: 9 passed and 3 failed, the same 3 pre-existing failures as the baseline (§5). The E2E server boots through the new explicit preparation.

**Recommended (not done, needs your action):** add the primary host to `PROTECTED_DATABASE_HOSTS` in `backend/.env`. That file holds secrets and wasn't edited.

### P2 Governance records: ✅ complete, including staging validation on an isolated Neon branch (2026-10-02)

**What changed**

| # | Requirement | Behaviour now |
|---|---|---|
| 1 | R12.6 append-only votes | A re-cast adds version n+1 and supersedes the seat's current vote (`is_current`, `superseded_at`, `superseded_by_id`). A re-cast needs a `recast_reason`. Nothing is overwritten or deleted. `GET …/committee-votes` returns current votes; `?include_history=true` returns all of them. Existing votes became version 1 (migration 0017). The decision package and frozen decision record (format 1.1) carry the full history. |
| 2 | Q-6 Admin ≠ committee | Admins can't vote or make the committee decision unless `COMMITTEE_ADMIN_AUTHORITY=true` (provisional, off by default). Refusals are logged `ACCESS_DENIED`. |
| 3 | R6.7/R10.2–R10.4 overrides | The system value is read from the record by the server. A client-sent `ai_value` is ignored (and the audit says so). A free-standing override is `PROPOSED` and counts only once an independent reviewer marks it `CONFIRMED` (or `REJECTED`). Typed endpoints write `APPLIED` ledger rows: factor rating, inherent override (new `override_by_id`), residual confirmation, FCRM review ratings and remaps of system-mapped controls. Calculated values (`calculated_*`, `residual_*`, `ai_suggested_*`) are never changed. The decision package and decision record list every calculated value with its human value beside it (`value_comparisons`). |
| 4 | Safety | The mandatory HIGH/CRITICAL challenge trigger uses the higher of the calculated band and the band of record, so lowering the band by override can't switch it off. |
| 5 | R11 mandatory sign-off | Moving to READY_FOR_COMMITTEE or COMMITTEE_REVIEW needs a current challenge-review sign-off: reviewer, role, time, system-derived outcome (`NO_TRIGGERS_FIRED` / `FINDINGS_ADDRESSED`), reason, and frozen snapshots of the triggers and findings (ids and states only). Sign-off is refused while a HIGH/CRITICAL finding is open. A finding raised after sign-off makes it stale. A manager return or rejection supersedes it. Allowed at HUMAN_REVIEW, SUBMITTED_TO_MANAGER and, for items already in flight, READY_FOR_COMMITTEE and DEFERRED. |
| 6 | R7.2/R10.2 controls | `PATCH …/controls/{id}` (edit or remap; reason required) and `POST …/unmap` (reason required) append a `ControlRevision` with the previous config, new config, changed fields, who, when and why, and bump the control's version. An unmapped control stays on record (`?include_unmapped=true`, `/revisions`). Gaps are recomputed. If residual risk is already frozen, it is re-frozen as a new version. A human confirmation carries forward only when the calculated result is unchanged; otherwise the audit says it no longer applies. |
| 7 | Design-adequacy bug | The engine recognises `INADEQUATE` (API/UI) and `DESIGN_INADEQUATE` (AI). Design inadequacy now raises a gap even without operating evidence, and earns no reduction. |
| 8 | Smaller fixes | Control-condition create checks editability. Control-condition updates and control edits record the actor. |

**Workflow (challenge to committee)**

1. Analyst review (HUMAN_REVIEW): overrides proposed and reviewed. The analyst may sign off the challenge review.
2. Owner submits to manager (SUBMITTED_TO_MANAGER). The manager resolves or accepts HIGH findings, signs off if not already done, then approves. Approval is refused (409, listing the issue) without a valid sign-off.
3. Committee (READY_FOR_COMMITTEE → COMMITTEE_REVIEW). Members vote; changing a vote needs a reason. The binding decision is unchanged.

**Permissions**

| Action | Who | SoD / scope |
|---|---|---|
| Propose override | FCRM Analyst, Manager, Admin (pipeline roles) | Assessment visibility + entity scope (global gate) |
| Review override | FCRM Analyst, Manager (`OVERRIDE_REVIEWER_ROLES`, provisional) | Not the proposer, not the owner |
| Challenge sign-off | FCRM Analyst, or the assigned Manager or their delegate (`CHALLENGE_SIGNOFF_ROLES`, provisional) | Not the owner |
| Edit / remap / unmap a control | Pipeline roles | Editable assessments only |
| Vote / committee decision | Committee Member or their delegate; Admin only if `COMMITTEE_ADMIN_AUTHORITY=true` | Not the owner, not the assigned or deciding manager |

Auditor and Executive remain read-only everywhere. Every refusal above is logged as `ACCESS_DENIED`.

**Audit behaviour**

- New actions: `OVERRIDE_REVIEWED`, `CHALLENGE_REVIEW_SIGNED_OFF`, `CHALLENGE_REVIEW_SIGNOFF_SUPERSEDED`, `CONTROL_UPDATED`, `CONTROL_REMAPPED`, `CONTROL_UNMAPPED`. Every vote cast (including re-casts) writes `COMMITTEE_VOTE_CAST` naming the version it replaces.
- ORM protection (`services/data_protection.py`):
  - `control_revisions` is append-only.
  - `committee_votes`, `challenge_review_signoffs` and `assessment_overrides` are supersede-only: only the retire and review fields may change, once.
  - `controls`, `control_revisions` and `challenge_review_signoffs` can't be deleted.
- Snapshots hold ids and states, never evidence text.

**Migration 0017 (`0017_governance_records`)**

- Changes: vote columns; unique `(assessment_id, member_id, version)` and a partial unique index (one current vote per seat) replace `uq_committee_vote_member`; existing votes become v1 and current, with `cast_by_id` filled from delegate or member. Override review columns (legacy rows keep NULL, unrelabelled). `inherent_risk_calculations.override_by_id`. New tables `challenge_review_signoffs` and `control_revisions`.
- Idempotent and safe to re-run. Downgrade refuses if any re-cast vote exists, rather than lose vote history.
- Validated locally (SQLite, a genuine pre-0017 shape): `test_migration_0017.py` and the staging-script rehearsal.

**Test results (2026-10-02, isolated databases only)**

| Suite | Result |
|---|---|
| `pytest` (unit/api/workflow) | **337 passed** (P2: `test_p2_governance.py` 29, `test_migration_0017.py` 3, `test_staging_validation.py` 4; guard 12) |
| Legacy scripts | NFR 22/22, workflow 9/9, delegation 13/13, reporting 7/7; degraded 59, phase-1 68, phase-2 86 checks: all pass |
| `tsc -b` | clean |
| ESLint | 19 findings project-wide, all in files P2 didn't touch (`AssessmentWorkflow.tsx` still 11, as at baseline) |
| Playwright | **13 passed, 3 failed**. The 3 are the known baseline failures (§5). **No new failures.** New `governance-records.spec.ts` 4/4: sign-off gate, re-cast vote history, override propose and independent confirm, control edit/unmap history |

**Legacy test changes and why they reflect the rules:**
- `test_stage14_workflow.py` now asserts the 409 without a sign-off, then signs off as the analyst.
- `test_aw7_delegation.py` records a genuine API sign-off for assessments placed before the committee, and its vote test now expects append-only history and a required re-cast reason.
- `test_stage17_reporting.py` names the record its override targets and expects the server-read original value.
- None of the new controls was bypassed or weakened.

**UI**
- Override ledger with system and human values and review status, plus Confirm/Reject for eligible reviewers.
- Challenge sign-off panel (FCRM review and the manager's approval row).
- Vote history with superseded votes, and a re-cast reason field.
- Decision package: "Calculated values and human overrides" table, the sign-off, and vote history.
- Control Edit/remap, Unmap and History.
- Admins no longer see committee voting rows.
- Verified in the browser on the isolated E2E server.

**Staging validation: ✅ passed on an isolated Neon child branch (2026-10-02)**

*Target verification (before anything ran; no credentials printed):*
- `STAGING_DATABASE_URL` is present in `backend/.env` and differs from `DATABASE_URL`.
- Its host differs from the primary's and isn't in `PROTECTED_DATABASE_HOSTS`. It's a different Neon endpoint (`ep-small-leaf-…`, pooled, `sslmode` set) in the same project domain and region as the primary (`ep-falling-water-…`).
- Branch identity was confirmed read-only, without touching the primary. Its contents match the primary snapshot recorded earlier exactly: revision `0017_governance_records`, 10 assessments, 236 audit events, 2 votes, 1 override, 8 users, last audit event 2026-10-01 11:05:31, and Neon `timeline_id` / `tenant_id` / `endpoint_id` are present. So it's a child branch of the primary, taken **after** the 2026-10-02 incident, and it **starts at 0017**.
- The script's own `--status-only` pre-flight accepted the target, which it refuses for the primary or any protected host.

*`python scripts/validate_staging_migration.py --rollback-check`: 13/13 checks passed:*

| Check | Result |
|---|---|
| alembic upgrade head (authorized for the staging host only) | PASS (no-op; already at head) |
| database at code head | PASS `0017_governance_records` |
| no rows lost or added in governed tables | PASS (10 tables, identical counts) |
| committee vote values unchanged (fingerprint) | PASS |
| existing votes are version 1 and current | PASS |
| 0017 vote constraints present / old constraint gone | PASS |
| one-current-vote partial index present | PASS |
| 0017 tables present | PASS |
| application starts against the schema (`prepare_database()` + `/health` = healthy) | PASS |
| rollback `downgrade -1` (0017 → 0016) | PASS |
| no rows lost by rollback | PASS |
| re-upgrade 0016 → 0017 | PASS |
| vote values unchanged after round trip | PASS |

The report is saved at `backend/eval_results/staging_migration_report.json`.

*Extra read-only post-check on staging after the round trip:*
- `uq_committee_vote_version` is the only unique constraint.
- `uq_committee_vote_current` is unique with predicate `is_current`.
- Both votes are v1, current, not superseded, and `cast_by_id` matches the delegate or member.
- The six override review columns are present, and the legacy override keeps `review_status` NULL.
- `override_by_id` and both new tables are present.
- Users are still 8, so start-up seeded nothing.

*What this proves, and what it doesn't:*
- **Proves:** the rollback leg took production-shaped data down to 0016 and back, so a real **0016 → 0017 upgrade has now run on a copy of production data**. Downgrade and upgrade are both lossless, and the application starts and answers `/health` on that schema.
- **Doesn't prove:**
  - The upgrade of the *original* pre-incident 0016 state. The branch starts after the incident, and its 0016 state was produced by our own downgrade. A branch from a timestamp before the incident (2026-10-02; the first read-only check afterwards ran at 09:54 UTC, so pick a time well before that) would cover that, and is optional now that the primary is already at 0017.
  - Load or concurrency behaviour.
  - Running through the deployed container image (the app was started in-process).

*Safety during the run:*
- The primary database was not connected to.
- No protected-host flags were used.
- Nothing was deployed.
- One script fix, which tightens rather than weakens the guard: an explicitly set `STAGING_DATABASE_URL` (even empty) now always takes precedence over `backend/.env`. Before this, the "refuses without a staging URL" test would have fallen back to the real staging URL now configured in `.env`.

*Regression after staging validation (isolated databases only):*

| Suite | Result |
|---|---|
| Backend `pytest` | 341 passed |
| Legacy scripts | NFR 22, workflow 9, delegation 13, reporting 7; degraded 59, phase-1 68, phase-2 86 checks: all pass |
| `tsc -b` | clean |
| Playwright | 13 passed, 3 failed (the known baseline failures in §5; no new failures) |

**Unresolved / pending governance confirmation**
- Q-2 (P3, not started).
- Q-6: `COMMITTEE_ADMIN_AUTHORITY` stays off by default.
- `OVERRIDE_REVIEWER_ROLES` and `CHALLENGE_SIGNOFF_ROLES` (both exclude Admin).
- Whether outstanding PROPOSED overrides should block the committee: not specified, so they don't block; they're shown as pending in the package.

**Known limitations**
- FCRM review `human_ratings` still apply to the legacy six-dimension results only (shown beside the calculated severity, never applied to the factor score).
- The challenge stage is still the manager stage; there is no separate challenge reviewer queue.

### P3 SoD exceptions and governance enforcement: ✅ implemented and verified (2026-10-02), on **provisional** policy

**Policy status: PENDING GOVERNANCE APPROVAL.**
- The governance recommendations R-GOV-01 to R-GOV-04 (approver tiers, Admin/committee exclusivity, the override and challenge role matrix, readiness blocking) have **no approval record** in the repository, the requirements brief or the docs.
- On 2026-10-02 the project chose to implement them as **provisional, configurable defaults**:
  - The rules live in `backend/app/governance/policy.py`, with optional overrides from the JSON file named in `GOVERNANCE_POLICY_FILE`.
  - Every API that applies them returns `policy_status: PROVISIONAL_PENDING_GOVERNANCE_APPROVAL`.
  - Every UI surface that relies on them shows "Pending Governance Approval".
- Nothing in P3 is presented as approved policy.

**Decisions taken for P3 (2026-10-02, provisional):**
- **Role model:** governance *designations* on top of the 9 base roles: SENIOR_ANALYST, QA_REVIEWER, CHALLENGE_REVIEWER, HEAD_OF_FCRM, FCRM_GOVERNANCE_OWNER, COMPLIANCE_MANAGER and COMMITTEE_CHAIR. Each is valid only with its permitted base role. They are Admin-assigned, can't be self-assigned, need a reason and are audited.
- **Exception tiers:** an exception goes to the Committee tier (Committee Chair) if any of the following holds; otherwise it is Standard tier (FCRM Governance Owner, Head of FCRM or Compliance Manager):
  - its risk is HIGH or CRITICAL
  - the assessment's band is HIGH or CRITICAL
  - it lasts more than 30 days
  - it is enterprise-wide (no assessment)
  - it is repeated (2 or more other exceptions for the same person in 365 days)
- **Medium-severity challenge findings** block until resolved or explicitly accepted with a reason; accepted ones are listed to the Committee. LOW findings never block.
- **Assumption (not in the brief):** an exception lasts at most 90 days (mirrors the delegation limit); expiry warning 7 days. Both are configurable.

**What was built**

| Area | Behaviour |
|---|---|
| SoD exception lifecycle (R15.2, R-GOV-01) | `DRAFT → PENDING_APPROVAL → APPROVED → EXPIRED / REVOKED`, or `→ REJECTED`. Fields: reference, requestor, affected user, conflicting roles (derived by the server; refused if there is no real conflict), assessment (or enterprise-wide), justification, why the standard workflow can't be followed, risk level, compensating controls, start/end (expiry), tier and tier reasons, assigned approver and reviewer, decision and rationale, declaration, revocation, periodic review, timestamps. An append-only history and an audit event for every step. Optimistic locking (`row_version`) plus status checks prevent duplicate or concurrent decisions. Content is frozen once it leaves DRAFT (`data_protection.py`). |
| Independent approval | Refused for the requestor, the affected user, the affected user's direct manager, a party to the assessment (owner, assigned or deciding manager), anyone without the tier's designation, and anyone other than the assigned approver (if one is set). |
| Use at the point of authorization | An exception authorizes only while APPROVED, in its window and **declared** (the affected person's conflict-of-interest declaration). Expiry is checked at every use as well as by the background sweep, and expired or revoked exceptions authorize nothing. Every use is logged (`SOD_EXCEPTION_USED`). Flags: REPEATED, EXPIRING_SOON, DECLARATION_MISSING. |
| Committee separation (R15.2) | A Committee Member who submitted or managed the assessment may vote or decide **only** with an approved, declared `COMMITTEE_SEPARATION` exception for that assessment. A delegate acting for a conflicted member never gets one. |
| Admin vs committee (R-GOV-02) | Mutually exclusive by default. The P2 switch `COMMITTEE_ADMIN_AUTHORITY` is **retired** and grants nothing (a warning is logged if it's set). An Admin votes or decides only with an approved, declared `ADMIN_COMMITTEE_DUAL_ROLE` exception. An enterprise-wide one, or one on a HIGH/CRITICAL case, needs the Chair. While one is in force, the global access gate refuses (403, logged) global configuration changes (users, methodology, challenge triggers, reference data, retention policy, approving or revoking SoD exceptions) and case administration on the covered case (workflow assign and target date, retention, legal hold, soft-delete, documents, amendment). Another administrator must act. No endpoint edits or deletes audit records. |
| Overrides (R-GOV-03) | Proposed by an FCRM Analyst (incl. Senior). Independently reviewed by a Senior Analyst, QA Reviewer or FCRM Manager. Material changes are approved by an FCRM Manager or the Head of FCRM, critical ones by the Head of FCRM only, and critical overrides are listed to the Committee. Proposer, reviewer, approver and the owner (the beneficiary) are always different people. **Materiality is classified by the server from the actual effect** (band change, factor band, applicability, indicators, control effectiveness or mapping, conditions, triage-relevant intake fields), never from a user label. Typed changes (factor rating, inherent override, residual confirmation, FCRM rating, remap) still take effect at once without touching the calculation, but a material one must be reviewed and approved before the committee. A **rejected** material change blocks until the record no longer holds the rejected value. |
| Challenge review (R-GOV-03) | Two steps. (1) An independent review by a Challenge Reviewer, Senior Analyst or QA Reviewer who did **not** prepare the case. Preparers are the owner, factor raters, override proposers, the FCRM reviewer and control assessors, matched by id with a name fallback for older rows. (2) A sign-off by the assigned FCRM Manager (or their delegate) or the Head of FCRM, who is not the reviewer, after a current review. CRITICAL cases are flagged as escalated to the Committee. |
| Readiness (R-GOV-04) | One server-side evaluation (`app/governance/readiness.py`) used by the transition guard (entry into Ready for Committee or Committee Review), the **final decision** (approve, approve with conditions, reject; deferral stays possible) and `GET /api/assessments/{id}/readiness`. Each blocker carries a code, message, responsible role, next action and items. Blocking: mandatory fields, residual missing, unresolved comments, open HIGH/CRITICAL findings, open unaccepted MEDIUM findings, incomplete challenge review or sign-off, material/critical overrides pending review or approval, rejected material overrides still in effect, and votes cast by a conflicted person without an exception. Warnings: LOW findings, accepted findings, critical escalation, critical and legacy overrides, pending SoD requests. Refusals are audited (`GOVERNANCE_READINESS_BLOCKED`, in their own session). |
| Visibility | Holders of HEAD_OF_FCRM, FCRM_GOVERNANCE_OWNER, COMPLIANCE_MANAGER, COMMITTEE_CHAIR, QA_REVIEWER and CHALLENGE_REVIEWER see all assessments (R15.4 entity scope still applies), so they can govern the cases they approve for. SoD exceptions are visible to their parties and governance roles only. `GET /api/sod-exceptions/candidates` exposes only id, name and role. |
| UI | **SoD Exceptions** page: request form, Mine / Awaiting my approval / All tabs, status and flag badges, detail with read-only history, and actions whose availability and reasons come from the server. **Committee readiness panel** (manager approval row, committee row for the final decision, FCRM Human Review). Two-step **challenge review** panel. Override ledger with materiality, governance state and **approval** step. **Governance designations** editor on Users. All marked "Pending Governance Approval". |

**Role and permission matrix (provisional)**

| Action | Allowed | Never |
|---|---|---|
| Request an SoD exception | Any signed-in, non-read-only user | — |
| Approve or reject (standard tier) | FCRM Governance Owner, Head of FCRM, Compliance Manager | Requestor, affected user, their direct manager, case parties |
| Approve or reject (committee tier) | Committee Chair | Same as above |
| Declare | The affected person | Anyone else |
| Revoke | Admin, FCRM Governance Owner, Head of FCRM, Committee Chair, the tier's approvers | The affected person; a dual-role Admin while their exception is in force |
| Periodic review | Assigned reviewer or the tier's approvers | Requestor, affected person |
| Propose an override | FCRM Analyst (incl. Senior Analyst) | Other roles |
| Review an override | Senior Analyst, QA Reviewer, FCRM Manager | Proposer, owner |
| Approve a material / critical override | FCRM Manager or Head of FCRM / Head of FCRM only | Proposer, reviewer, owner |
| Independent challenge review | Challenge Reviewer, Senior Analyst, QA Reviewer | Owner, anyone who prepared the case |
| Challenge sign-off | Assigned FCRM Manager (or delegate), Head of FCRM | Owner, the reviewer |
| Committee vote or decision | Committee Member or delegate; conflicted member or Admin only with an approved, declared exception | — |
| Assign designations | Admin | Self |

**New or changed endpoints**
- `POST /api/sod-exceptions`, `GET /api/sod-exceptions?scope=mine|awaiting_me|all`, `GET /api/sod-exceptions/{id}`, `POST /api/sod-exceptions/{id}/submit|decision|declaration|revoke|review`, `GET /api/sod-exceptions/candidates`
- `GET /api/governance/policy`
- `GET /api/assessments/{id}/readiness?purpose=COMMITTEE_SUBMISSION|FINAL_DECISION`
- `PUT /api/users/{id}/designations`
- `POST /api/assessments/{id}/challenge-review/review` (new). `.../signoff` now needs a prior review and an FCRM Manager or Head; `GET .../signoff` returns both steps and per-user `actions`.
- `PATCH /api/assessments/{id}/overrides/{oid}/approval` (new). `.../review` now also reviews material typed changes. `GET .../overrides` returns `materiality`, `state` and per-user `actions`.
- `POST .../committee-decision` checks final-decision readiness (409 with `blockers`) after the decision-record check.

**Migration `0018_sod_governance`**
- Adds:
  - `users.governance_designations`
  - `risk_factors.rated_by_id`
  - override origin, materiality and approval columns
  - `challenge_review_signoffs.stage` (existing rows → SIGNOFF) and `committee_escalation`
  - new tables `sod_exceptions` and `sod_exception_events`
- Additive and idempotent. Legacy rows are kept and never relabelled. Downgrade refuses while SoD exceptions or independent reviews exist.
- Note: P2 sign-offs recorded before 0018 have no REVIEW row, so under P3 those cases need a new independent review. The primary and staging databases had 0 sign-offs.

**Test results (2026-10-02; isolated databases only)**

| Suite | Result |
|---|---|
| Backend `pytest` | **374 passed**, incl. `test_p3_sod.py` 29, `test_migration_0018.py` 4, `test_p2_governance.py` 29 (aligned to P3) and the guard/staging tests |
| Legacy scripts | NFR 22, workflow 9, delegation 13, reporting 7; degraded 59, phase-1 68, phase-2 86 checks: all pass |
| `tsc -b` | clean |
| ESLint | 19 findings, all pre-existing in files P3 didn't introduce (same counts as before P3) |
| Playwright | **17 passed, 3 failed**; the 3 are the known baseline (§5). **No new failures.** New `sod-governance.spec.ts` 4/4 covers request, self-approval refused, independent approval, expiry, same-case admin refusal, material override and challenge review gating, and readiness clearing. `governance-records.spec.ts` 4/4 (aligned) |
| Frontend unit tests | none: the project has no frontend unit-test framework. The UI is covered by type-check, lint and Playwright |

**Changes to existing tests**
- The P2 tests and the legacy workflow/delegation scripts now follow the P3 matrix: a designated independent reviewer, then a manager sign-off; Admin refused even with the retired switch; materiality-based override states.
- The legacy scripts no longer stub `has_blocking_findings`. Their fixtures accept open findings with a reason through the API, as the real journey does.
- The decision-record check runs before final-decision readiness, so its existing 400 contract is unchanged.

**Staging validation (isolated Neon child branch, verified before use: different host from the primary, not protected)**

`validate_staging_migration.py --rollback-check` passed **15/15** on a real **0017 → 0018** upgrade of production-shaped data:
- at head
- governed row counts unchanged
- vote values unchanged
- 0017 and 0018 objects present
- existing overrides and sign-offs left unlabelled
- app start-up and `/health`
- downgrade −1 lossless
- re-upgrade
- votes unchanged after the round trip

No protected-host flags were used, the primary was neither migrated nor written to, and nothing was deployed. The P3 session did run one **read-only** status check against the primary, which confirmed it is still at `0017_governance_records`; the "0 sign-offs" figure above comes from that check. The primary is **not** migrated to 0018; that needs a separate, approved production change.

### P3 closure: final verification (2026-10-02, P5–P8 session)

Isolated databases only. The primary was not accessed from this session, nothing was migrated and nothing was deployed.

**Fix: `policy_status` on every provisional-governance API.** P3's contract (above) says every API that applies the provisional rules returns `policy_status`. The override ledger and user responses didn't.
- `OverrideResponse` (`app/schemas/assessment_override.py`) now carries `policy_status`. This covers ledger list, propose, review and approval.
- `UserResponse` (`app/schemas/user.py`) now carries it too. This covers `/api/users`, designation PUT and `/api/auth/me`. The field describes the designation rules, not the user.
- Both are a schema default taken from the central `POLICY_STATUS` constant (`app/governance/policy.py`). They are never read from the request: a client-sent `"policy_status": "APPROVED"` is ignored. The value is `PROVISIONAL_PENDING_GOVERNANCE_APPROVAL` even on an **approved** override, because the override is approved but the policy that governed it isn't.
- The frontend types (`src/api/auth.ts`, `src/api/governanceRecords.ts`) gained the optional field. No consumer changed.

**New tests: `tests/api/test_p3_policy_status.py` (6).**
- The constant's value.
- The ledger at propose, review, approve and list.
- A client can't set the field.
- Users, designation update and `/me`.
- One status across the policy, readiness, challenge review, ledger and user APIs.
- **Designation visibility.** A QA Reviewer Manager sees cases outside their chain, as the provisional matrix intends, but **entity scope still applies**. A plain Manager doesn't. A designation held with the wrong base role grants nothing.
- Self-approval, approval by the affected person or their direct manager, wrong-tier approval, duplicate decisions, involved reviewers, sign-off of one's own review and same-case admin actions were already covered in `test_p3_sod.py` and `test_p2_governance.py`.

**Results (commands from `backend/` unless noted; every command sets a temp `DATABASE_URL` and an empty `STAGING_DATABASE_URL`)**

| Check | Command | Result |
|---|---|---|
| Backend | `.venv/Scripts/python.exe -m pytest tests/unit tests/api tests/workflow -q` | **380 passed, 0 failed, 0 skipped** (374 + 6 new) |
| Legacy NFR | `.venv/Scripts/python.exe test_stage19_nfr.py` | 22/22, exit 0 |
| Legacy workflow | `… test_stage14_workflow.py` | 9/9, exit 0 |
| Legacy delegation | `… test_aw7_delegation.py` | 13/13, exit 0 |
| Legacy reporting | `… test_stage17_reporting.py` | 7/7, exit 0 |
| Degraded mode (plain script, not pytest) | `… test_degraded_mode.py` | 59 checks pass, exit 0 |
| Phase-1 evidence/scoring | `… test_phase1_evidence_scoring.py` | 68 checks pass, exit 0 |
| Phase-2 governance | `… test_phase2_governance.py` | 86 checks pass, exit 0 |
| Risk engine demo | `… test_risk_engine.py` | runs to completion, exit 0 |
| Type-check (frontend/) | `npx tsc -b` | clean, exit 0 |
| ESLint on changed files (frontend/) | `npx eslint src/api/auth.ts src/api/governanceRecords.ts` | no findings, exit 0 |
| Playwright (frontend/) | `npx playwright test --reporter=line` | **17 passed, 3 failed, 0 flaky, 0 skipped** (exit 1). Ports 8000 and 5173 were free first. The backend is `tests/e2e_server.py`, which forces a fresh temp SQLite database |
| Alembic | `ScriptDirectory.get_heads()` | single head `0018_sod_governance` |
| Staging report | `eval_results/staging_migration_report.json` | 15/15 on the staging endpoint (`ep-small-leaf-…`), not the primary (`ep-falling-water-…`). 0017 → 0018, row counts identical, rollback and re-upgrade lossless |

**The 3 Playwright failures are the §5 baseline, not P3.**
- `status-workflow` ×2: the server log shows the setup's `POST /analyze` refused, because the business profile hasn't been confirmed (the R3.3 gate).
- `analyze-assessment` dashboard test: the row goes "Analyzing…" and back to "Analyze" / Submitted. That's consistent with the same gate refusing an analyze on an unconfirmed profile; the trace has no other error.
- All three were recorded before P1 and have been the same three at every phase since. Fixing the test setup, never the gate, is P8 scope.
- Separately, Vite logs an `[Unhandled rejection]` from `followStageAdvance` (`AssessmentWorkflow.tsx`) in the rules-only test, which passes. It's a UI error-handling nit, noted for P8.

**Closure: ✅ P3 verified and closed on provisional policy.** All checks above ran to completion. R-GOV-01 to R-GOV-04 remain **Pending Governance Approval** (open decisions above). Still to do, by design:
- migrate the primary to 0018, through a separate approved production change;
- Admin assigns designations after deployment;
- broad visibility for designation holders is pending a governance decision.

**Security review**
- Authorization sits at the API and service layers.
- Object-level access is enforced (exception visibility, assessment visibility for linked exceptions).
- Denials are logged as `ACCESS_DENIED`.
- History and audit are append-only; content is frozen after submission.
- No credentials appear in responses or logs.
- Least-privilege candidate lookup.

**Open governance decisions (PENDING GOVERNANCE APPROVAL)**
- R-GOV-01 to R-GOV-04 as a whole: tiers, approver designations, thresholds (30-day / repeat 2-in-365 / max 90 days / 7-day warning), materiality classification rules, and the role matrix.
- Whether designation holders should see all assessments, or only cases routed to them.
- Whether a Committee Chair's approval is required for every dual-role exception (currently only enterprise-wide, HIGH/CRITICAL or long or repeated ones).
- Whether the conservative "all user administration is refused while holding a dual-role exception" rule should narrow to users who are parties to the covered case.
- Q-5 (LOW-risk committee route) unchanged; Q-7 unchanged.

**Known limitations**
- A dual-role Admin votes from the API or the assessment; the Approvals page lists committee rows for Committee Members only.
- Involvement for older rows uses recorded names (a fallback).
- Decision records frozen before P3 don't carry readiness.

### P5 Retention and data lifecycle: ✅ implemented and verified locally (2026-10-02), on **provisional** policy

Isolated databases only. The primary Neon database was not connected to, migrated or written to. Nothing was deployed and nothing was physically deleted.

**Policy status: PENDING GOVERNANCE APPROVAL.**
- No retention period is compliance-approved (Q-3 stays open).
- The 2,555-day default is kept as the provisional ASSESSMENT period.
- D-1 to D-4 are built as configurable defaults in `app/governance/policy.py` (`retention`). They are recorded as open below.
- Every retention API response carries `policy_status: PROVISIONAL_PENDING_GOVERNANCE_APPROVAL`, and every retention UI surface shows "Pending Governance Approval".
- `governance_approval_reference` / `governance_approved_at` exist on each version for a later, separate approval process. The application never sets them.

**Source of truth:** `docs/prompts/P5_RETENTION_PROMPT.md`, as instructed on 2026-10-02.
- The earlier chat brief also asked for a release *request* followed by a separate release *approval*, placement approval, and editable *draft* proposals.
- These were not built because the repo prompt defines a direct release by a different authorized user (D-2) and a PROPOSED → decided lifecycle. They are listed as open.

**What was built**

| Area | Behaviour |
|---|---|
| Versioned policy (R16.4) | `retention_policy_versions`, append-only. Lifecycle `PROPOSED → ACTIVE → SUPERSEDED` or `PROPOSED → REJECTED`. Each version records record type, version, days, previous days, basis (ASSESSMENT: `FINAL_DECISION_DATE`), effective-from, proposer, decider, both reasons and the version it supersedes. Only the decision and supersession fields may change, once each (`data_protection.py`). There are no deletes and no bulk updates. Partial unique indexes allow one ACTIVE and one PROPOSED version per record type, and optimistic locking (`row_version`) blocks concurrent decisions. Only ASSESSMENT is configured; an unconfigured record type has no policy and is never eligible. |
| Independent approval (D-1) | A proposal (days 365–36,500, justification ≥ 20 characters, one open per record type, must differ from the active period) becomes ACTIVE only when an approver who is **not the proposer** and holds FCRM Governance Owner or Compliance Manager (Manager base role) approves it with a reason. The previous version is SUPERSEDED in the same transaction. An Admin can propose but not approve. Rejection needs a reason and changes nothing. With the flag off, the change applies at once but is still versioned, reasoned and audited. |
| Legacy API | `GET /api/retention-policy` reads through to the active ASSESSMENT version and adds `active_version*`, `pending_*` and `policy_status`. `PATCH /api/retention-policy` now **creates a proposal** (needs `change_reason`) and never edits in place. The `retention_policies` row is kept unchanged. |
| Legal holds (R16.1, R16.4, D-2) | `legal_hold_events`, append-only (SET / RELEASED with reason, actor and optional matter reference ≤ 100 chars). `assessment_retention.legal_hold*` is the current state, changed in the same transaction. Placing and releasing both need a reason. A double set or a release with no hold returns 409. The releaser must differ from the setter (by id; by recorded name for pre-P5 holds). A hold can be placed at any stage. A soft-deleted assessment can't get a new hold (409, logged). Released holds stay in the history. No job or policy change ever releases a hold. Hold reasons and history are shown to retention administrators and report readers only; others see the flag. |
| One eligibility rule | `app/governance/retention.py: evaluate()`, used by the per-assessment view, the report and soft delete. It returns record id and type, policy version, days, basis date, eligibility date, status, hold, reason and evaluation time. Outcomes are tested in this order: `LEGAL_HOLD`, `SOFT_DELETED`, `NO_POLICY`, `INVALID_POLICY`, `NOT_STARTED`, `INVALID_DATE` (missing or future decision date), `RETAINED`, `ELIGIBLE`. Missing or invalid data is never eligible. Eligibility is for controlled review only. |
| Soft delete | Same route. Now allowed only when `evaluate()` says ELIGIBLE. Records `retention_policy_version_id`. Still a soft delete; the P1 read and write gates are unchanged. |
| Eligibility report (D-4) | `GET /api/retention/eligibility`, read-only, Admin and Auditor. Filters: status (eligible, upcoming, held, retained, not started, invalid, no policy, deleted), `within_days`, legal hold, policy version, decision-date and eligibility-date ranges. Sorted and paginated. P1 visibility and R15.4 entity scope apply. Soft-deleted rows appear only with `include_deleted=true`, for Admins and Auditors. It returns identifiers, dates and the outcome only (no title, content, evidence or personal data). Every view is audited (`RETENTION_REPORT_VIEWED`, with filters and counts). |
| Audit (R16.3) | New dedicated actions: `RETENTION_POLICY_PROPOSED`, `_APPROVED`, `_REJECTED`, `_ACTIVATED`, `_SUPERSEDED`, `LEGAL_HOLD_SET`, `LEGAL_HOLD_RELEASED`, `ASSESSMENT_SOFT_DELETED`, `RETENTION_REPORT_VIEWED`. Details carry previous and new values (days, version, hold state), the reason and the policy status. Every refusal is `ACCESS_DENIED`. Existing `STATUS_CHANGE` rows are not relabelled. |
| UI | **Retention & Legal Holds** page (nav shown only when `GET /api/retention/permissions` says so): provisional banner; the active period per record type; a pending proposal with current vs proposed side by side and Approve/Reject with reason (only when the server allows, otherwise the server's reason is shown); a propose form; read-only version history (who, when, why, previous → new); current and historical holds; the eligibility report with filters, sorting and paging. The per-assessment **Retention & Legal Hold** panel (`RetentionHoldPanel.tsx`) is now shown at **every stage**. It shows the policy version, the server's eligibility and its explanation, the hold history, and Place / Release with reason and soft delete, each only when the server allows. |

**Permissions (provisional)**

| Action | Allowed | Never |
|---|---|---|
| View policy versions, holds list | Admin, FCRM Governance Owner, Compliance Manager, Auditor | Other roles (403, logged) |
| Propose a retention change | Admin | Auditor / Executive (read-only), others |
| Approve or reject a change | FCRM Governance Owner or Compliance Manager (Manager base role) | The proposer; an Admin; anyone without the designation |
| Place a legal hold | Admin, Compliance Manager | Read-only roles, others |
| Release a legal hold | Admin, Compliance Manager | The user who placed it |
| Soft delete (eligible records only) | Admin | Everyone else |
| Eligibility report | Admin, Auditor (within entity scope) | Manager, Analyst, Business User, Executive, designation holders |

A dual-role Admin (P3) is refused retention policy writes as global configuration (`retention/policies` was added to the access gate).

**New or changed endpoints**
- `GET /api/retention/permissions`
- `GET /api/retention/policies`, `POST /api/retention/policies` (201), `GET /api/retention/policies/{id}`, `POST /api/retention/policies/{id}/decision`
- `GET /api/retention/eligibility`, `GET /api/retention/legal-holds`
- Changed: `GET/PATCH /api/retention-policy` (read-through; PATCH proposes), `GET /api/assessments/{id}/retention` (eligibility, version, hold history, actions, `policy_status`), `PATCH .../retention/legal-hold` (release needs a reason; D-2; `matter_reference`; Compliance Manager allowed), `POST .../soft-delete` (shared rule, records the version, dedicated audit action)

**Migration `0019_retention_lifecycle`** (revises `0018_sod_governance`; single head)
- Additive and idempotent. It adds the two tables, the partial unique indexes and two nullable `assessment_retention` columns.
- It backfills v1 from the existing policy row and a SET event for each current hold.
- Downgrade refuses while any non-backfilled version, hold event or recorded soft-delete version exists.
- `test_migration_0018.py`: the "really at 0017" fixture now also removes the 0019 objects (the baseline builds today's models). Its upgrade check is pinned to `0018_sod_governance`. What it verifies is unchanged.

**Test results (2026-10-02; isolated databases only)**

| Suite | Result |
|---|---|
| Baseline before P5 | pytest 380 passed; `tsc -b` clean; single head `0018_sod_governance`; Playwright 17 passed / 3 failed (§5), from the P3 closure run on the same tree |
| Backend `pytest` (unit, api, workflow) | **407 passed, 0 failed, 0 skipped (380 baseline + 27 new)**, including the new `test_p5_retention.py` (22) and `test_migration_0019.py` (5) |
| Legacy scripts (temp `DATABASE_URL` each) | NFR 22/22, workflow 9/9, delegation 13/13, reporting 7/7; degraded 59, phase-1 68, phase-2 86 checks; `test_risk_engine.py` clean. All exit 0 |
| `tsc -b` | clean |
| ESLint | new files clean; `AssessmentWorkflow.tsx` 10 findings, all pre-existing (was 11; one moved out with the retention panel) |
| Playwright | **20 passed, 3 failed**. The 3 are the §5 baseline. New `retention.spec.ts` 3/3: Admin proposes, self-approval refused (UI and API), independent Governance Owner approves, new version in force and v1 unchanged; hold placed and shown on the report, setter can't release, Compliance Manager releases with reason, history and report updated; Auditor reads within entity scope and every write is refused; analyst and manager refused the report; nav hidden from the analyst |
| Migration rehearsal | `validate_staging_migration.py --local-rehearsal --rollback-check`: **17/17** on SQLite (0018 → 0019, rollback, re-upgrade) |
| Staging Neon | **22/22** on the isolated child branch, 0018 → 0019 with rollback and re-upgrade (see below) |
| Frontend unit tests | none: the project has no frontend unit-test framework |

**Staging validation plan for 0019 (preflight done 2026-10-02; executed on approval, result below)**

*Preflight (no database was connected to or changed):*
- **Migration chain:** single head `0019_retention_lifecycle`, whose `down_revision` is `0018_sod_governance`. 19 revisions, all ids unique.
- **Target:** `STAGING_DATABASE_URL` is set in `backend/.env` only (not in the process environment).
  - It is Postgres on Neon endpoint `ep-small-leaf-b34wgiqb-pooler`, database `neondb`, `sslmode=require`. This is the same isolated child branch used for P2 and P3.
  - The primary is `ep-falling-water-b35syv2c-pooler`. The two hosts differ.
  - The primary's host is in `PROTECTED_DATABASE_HOSTS` (1 entry); the staging host is not. No `MIGRATION_*` variables are set.
- **Expected revision before:** `0018_sod_governance` (where the P3 validation left it, after re-upgrade). This is taken from the record, not checked live. `--expect-revision` stops the run if it differs.
- **Validator safeguards:**
  - It refuses a missing URL, SQLite without `--local-rehearsal`, the primary's host, a protected host, and a URL equal to `DATABASE_URL`.
  - It never prints the URL.
  - Subprocesses get `DATABASE_URL` set to staging and `MIGRATION_AUTHORIZED_HOST` set to the staging host only. Inherited `MIGRATION_*` variables are dropped, and it never sets the protected-host flag.
  - Snapshots run in `READ ONLY` transactions.
- **Added for 0019 (validation tooling only; no application change):**
  - `--expect-revision`: stops, exit 2, before any change.
  - The report records `run_at` and `mode`.
  - New read-only checks: the partial unique indexes, the foreign keys, no orphaned hold events, no duplicate ACTIVE/PROPOSED versions, an eligibility probe through the app's own `evaluate()` (v1 in force; held records are never eligible; records with no decision date are never eligible), and the backfill again after the rollback round trip.
  - Tests: `test_staging_validation.py` 5/5, including the new test that a wrong revision stops the run and a rehearsal never writes the staging evidence file. Guard tests pass.
  - The local SQLite rehearsal passed 22/22. It used assessments that are held, eligible and undecided. **It is not staging evidence.**
- **Recovery:** this is a staging child branch, not production. Its data is a copy of the primary, so nothing on it is a record of record.
  - Before the run, take a Neon point-in-time-restore marker, or a child branch of staging (e.g. `staging-pre-0019`), so the branch can be reset. This can't be verified from the code; the operator confirms it.
  - In-band rollback: `alembic downgrade 0018_sod_governance` is lossless while only the backfill exists. The run exercises it with `--rollback-check`.

*Proposed commands (from `backend/`, after approval only):*
1. Read-only identity and revision check (no writes):
   `python scripts/validate_staging_migration.py --status-only --expect-revision 0018_sod_governance`
2. Migrate, verify, start the app, round-trip the rollback:
   `python scripts/validate_staging_migration.py --expect-revision 0018_sod_governance --rollback-check`

*Expected changes on staging:*
- New tables `retention_policy_versions` and `legal_hold_events`.
- The two partial unique indexes.
- Two nullable columns on `assessment_retention`.
- One ASSESSMENT v1 ACTIVE row (the legacy policy's days, or 2,555 if there is no row; "not compliance-approved").
- One backfilled SET event per assessment currently on hold.
- Governed row counts unchanged.
- The run ends at `0019_retention_lifecycle`. The rollback leg drops and re-creates the 0019 objects, so the backfill timestamps are those of the re-upgrade.

*Evidence:* `backend/eval_results/staging_migration_report.json`, written only by a staging run (rehearsals write `staging_migration_rehearsal_report.json`).

*Stop conditions:*
- Any refusal from the validator.
- Revision ≠ `0018_sod_governance`.
- Any FAIL, especially: rows lost or added, v1 not matching the legacy policy, a held record that is not LEGAL_HOLD, orphaned hold events, missing indexes or foreign keys, or app start-up failing.
- A downgrade refusal: retention history exists on staging that shouldn't.
- Any sign the target is the primary.

On a stop, nothing further runs; the failure is reported before anything else is done. A partial upgrade is rolled back by Postgres transactional DDL.

**Staging validation of 0019: ✅ passed 22/22 on the isolated Neon child branch (2026-10-02, approved in chat)**

*Recovery point, taken before any change:*
- A read-only logical snapshot of every staging table (45 tables, 969 rows; columns and rows as JSON, with a SHA-256 manifest) in the git-ignored `backend/backups/staging-pre-0019-20261002T165357Z/`.
- A Neon point-in-time-restore target of **2026-10-02 16:53:57 UTC** (restore the staging branch to that time in the Neon console).
- A Neon branch or snapshot could not be created from here: there is no Neon CLI, Neon API key or `pg_dump` on this machine, and Docker isn't running. The JSON snapshot plus the restore timestamp are the recovery point.

*Run:*
1. `--status-only --expect-revision 0018_sod_governance` (read-only): target `ep-small-leaf-b34wgiqb-pooler`, at `0018_sod_governance`, contents as recorded (10 assessments, 236 audit events, 8 users, no retention rows).
2. `--expect-revision 0018_sod_governance --rollback-check`: **22/22 checks passed**.
   - Upgraded to `0019_retention_lifecycle`.
   - No rows lost or added in governed tables.
   - Votes unchanged.
   - The 0017 and 0018 objects are intact.
   - The 0019 tables, partial unique indexes and foreign keys are present.
   - v1 backfilled with no orphaned or duplicate rows.
   - Eligibility probe: 10 assessments; 8 NOT_STARTED, 2 RETAINED under v1 (2,555 days); 0 violations.
   - The app starts and answers `/health`.
   - Downgrade to 0018 and re-upgrade are lossless, and the backfill is present again.
- Report: `backend/eval_results/staging_migration_report.json` (`mode: staging`, `run_at` 2026-10-02T16:54:18Z). This replaces the P3 report lost earlier with fresh staging evidence.

*Independent read-only post-check:*
- Revision `0019_retention_lifecycle`.
- The only new tables are `retention_policy_versions` and `legal_hold_events`, and no table is missing.
- **Every one of the 43 pre-existing tables has the same row count as the snapshot.**
- One version: ASSESSMENT v1, 2,555 days, ACTIVE, `PROVISIONAL_PENDING_GOVERNANCE_APPROVAL`, "Migrated existing default; not compliance-approved", no governance approval reference.
- 0 hold events (staging had no holds).
- Partial unique indexes `uq_retention_policy_one_active` (`status = 'ACTIVE'`) and `uq_retention_policy_one_proposed` (`status = 'PROPOSED'`), plus `uq_retention_policy_version`.
- Both new `assessment_retention` columns are present.

*Safety:*
- `DATABASE_URL` and `STAGING_DATABASE_URL` were unset in the shell, so the target came only from `backend/.env` `STAGING_DATABASE_URL`, through the validator's refusals.
- Migration was authorized for the staging host only. No protected-host flag was used.
- The primary (`ep-falling-water-…`) was not connected to.
- No credentials were printed. Nothing was deployed, purged or committed.

*What this does not prove:*
- The legal-hold backfill on real data: staging had no holds. It is covered by `test_migration_0019.py` and the local rehearsal.
- Behaviour under load.
- Running from the deployed container image.

*Production recovery branch (2026-10-02, created via the Neon API on request):* `production-pre-0019` (`br-snowy-math-b3pt5x8g`) in project `Trust BY Design` (`summer-flower-19034040`).
- Its parent is `production` (`br-winter-art-b3xohxo2`, the default and primary branch that owns the primary endpoint `ep-falling-water-…`).
- It was branched at 2026-10-02T17:01:16Z (LSN 0/2BFA4D0). It is ready and has no compute endpoint, so no connection string exists.
- The primary database was not connected to; branching is a storage-level copy.
- API calls are verified through the Windows trust store (the network inspects TLS via the corporate Netskope gateway); verification was never disabled.

*Production-copy rehearsal (2026-10-02, on approval): ✅ passed, 17/17 harness steps including the validator's 22/22.* Report: `backend/eval_results/rehearsal_0019_report.json`; the validator's own output is in `rehearsal_0019_validator.json`. Staging evidence is unchanged (SHA-256 verified).

- **Pre-checks:** production had no Neon operations since it suspended at 17:01:31Z (it was last active 16:54:17Z, before the recovery branch). `production-pre-0019` is ready, has no expiry, and was branched at 17:01:16Z (LSN 0/2BFA4D0).
- **Branch:** temporary branch `rehearsal-0019` (`br-tiny-snow-b3ujiv9c`), taken from current production with its own endpoint `ep-summer-sun-b3eof8jk`. Its connection string was held in memory only. It was at `0017_governance_records`.
- **Migration:** the existing guard migrated 0017 → 0018 → 0019 in one run, authorized for the rehearsal host only. The validator's 22 checks passed:
  - integrity and vote fingerprint;
  - 0017, 0018 and 0019 objects;
  - v1 backfilled with 2,555 days, provisional, not approved;
  - partial unique indexes and foreign keys;
  - no orphans;
  - eligibility probe: 10 assessments, 8 NOT_STARTED, 2 RETAINED, 0 violations;
  - app start-up and `/health`;
  - downgrade to 0018 and re-upgrade.
- **Full table check:** all 43 pre-existing tables kept their row counts. The only tables added were `sod_exceptions`, `sod_exception_events`, `retention_policy_versions` and `legal_hold_events`.
- **Rollback limits (tested):**
  - A full downgrade to 0017 restored the exact original table set and counts while only backfill existed. Re-upgrade succeeded.
  - With a simulated retention proposal present, the 0019 downgrade **refused**.
  - With a simulated SoD exception present, a downgrade to 0017 **refused**, and the refused multi-step downgrade left the schema at 0019 (one transaction).
  - Conclusion: rollback is safe only before real use. After that, restore from `production-pre-0019`.
- **Cleanup:** `rehearsal-0019` and its endpoint were deleted after the report was verified. Remaining branches: `production`, `production-pre-0019`, `staging-risk-workbench`.
- **Not proven:** a legal-hold backfill on real data (production has no holds), behaviour under load, and the deployed container image.

*Still to do (separate, approved change):*
- Migrate the primary (0018, then 0019), as a production change with its own approval and a PITR checkpoint.
- Governance approval of D-1 to D-4 and of the periods (Q-3).

**Security review**
- Authorization is enforced in the service layer, for every route, including the legacy PATCH.
- The UI only shows what `actions` and `permissions` allow; the server re-checks every request.
- Object-level access: the assessment gate, P1 soft-delete read and write gates, and R15.4 scope on the report and holds list.
- Read-only roles are refused by the global gate.
- History tables are append-only at the ORM level, and versions are guarded by a state machine.
- Concurrent or duplicate decisions get 409 (optimistic lock plus unique indexes; tested).
- No content, secrets or credentials appear in report rows or audit details. Hold reasons are logged as the rationale.

**Incident (2026-10-02, this session):** the local rehearsal of `validate_staging_migration.py` wrote its report to the same path as real staging runs, `backend/eval_results/staging_migration_report.json`. That overwrote the P3 staging report.
- The file wasn't tracked by git and can't be recovered. The P3 staging results remain recorded in this document (15/15, endpoints, row counts), but the raw JSON evidence is lost.
- Fixed: a `--local-rehearsal` run now writes `staging_migration_rehearsal_report.json`, so a rehearsal can't overwrite real staging evidence again.
- The P5 rehearsal output is in that file. Re-running the approved staging validation recreates the staging report.

**Open governance decisions (PENDING GOVERNANCE APPROVAL)**
- Q-3: the approved retention periods; periods for DOCUMENT, AUDIT_EVENT and AI_USAGE_LOG records (not invented).
- D-1: whether independent approval is required, and the approver designations (FCRM Governance Owner, Compliance Manager).
- D-2: release by a different user; whether a release request needs a separate approval step, and whether placing a hold needs approval.
- D-3: the 365 / 36,500-day bounds and the 20-character justification minimum.
- D-4: Auditor read access to the report.
- Whether a period change applies to existing records (currently the version in force at evaluation time applies to every record, so shortening a period makes older records eligible sooner), or only to records decided after it.
- Who may see legal-hold reasons (currently retention administrators and report readers).

**Known limitations and deferred items**
- No physical purge, crypto-shredding, file removal, backup expiry for disposed records or scheduled disposal. These are irreversible and need approved periods and a disposal sign-off process (P7/P8 or later).
- Effective dates: a version takes effect when approved. Future-dated versions aren't supported.
- No DRAFT state or editing of proposals: a proposal is fixed once made. To change it, reject it and propose again.
- Reassessment-chain retention: each assessment's clock runs from its own final decision (P6 links parent and child).
- The report evaluates in memory over the user's visible assessments. That's fine at current volumes; it needs a set-based query at scale.
- The primary is not migrated to 0018 or 0019; that needs a separate, approved production change.

### Neon database validation completion (2026-10-02)

Writes went only to temporary branches, which were deleted afterwards. Production had read-only checks only, and the recovery branch was never modified. No secrets appear in any report.

**Branches and revisions** (`backend/eval_results/neon_state_20261002.json`):

| Branch | Id | Revision | Tables / rows | How it was checked |
|---|---|---|---|---|
| `production` (default) | `br-winter-art-b3xohxo2` | `0017_governance_records` | 43 / 969 | read-only transaction |
| `staging-risk-workbench` | `br-rough-brook-b3gkusqk` | `0019_retention_lifecycle` | 47 / 970 | read-only transaction |
| `production-pre-0019` (recovery) | `br-snowy-math-b3pt5x8g` | `0017_governance_records` | 43 / 969 | read-only, through a temporary child branch (`verify-recovery-0019`, deleted); the recovery branch itself was not touched |

- **Recovery point equals production:** True. Same revision, same tables, and the same row count in every one of the 43 tables. It was branched at 17:01:16Z, LSN 0/2BFA4D0, with no expiry.
- The read-only production check woke production's compute briefly; it changed no data.

**Functional rehearsal on real Postgres** (temporary branch `rehearsal-0019-functional` from production, deleted; `neon_functional_0019_report.json`): **14/14 checks passed.**
- The branch was seeded with pre-P5 state that production doesn't currently have: an edited legacy policy of 3,650 days, an assessment on legal hold, and a P2 challenge-review sign-off. It was then migrated 0017 → 0019 with the guard. Results:
  - Every pre-existing table kept its row count.
  - v1 carries 3,650 days, provisional, not approved.
  - The hold became one backfilled SET event with the original reason and name; the current hold state is untouched.
  - The legacy policy row is unchanged.
  - The sign-off became `SIGNOFF`, not escalated.
  - No override was classified, and no designations or SoD exceptions were invented.
  - Partial unique indexes and FKs are present, with no orphans or duplicates.
  - The eligibility probe gave LEGAL_HOLD 1, NOT_STARTED 8, RETAINED 1, with 0 violations.
- **This closes the earlier gap:** the legal-hold backfill is now proven on real Postgres.

**Application suites on Postgres** (an empty database `p5_pgtests` on the same temporary branch, from a scratch copy of `tests/` with only the database URL changed): `test_p5_retention`, `test_p3_sod`, `test_p3_policy_status`, `test_p2_governance`, `test_p1_security`, `test_roles_and_scope`. **101 passed in 1018.74s (0:16:58).** The repository's suite normally runs on SQLite only; this is the first run of these governance and retention paths on Postgres.

**Schema consistency** (models compared with the database, read-only):
- Staging and the migrated production copy have **identical** differences: 19 entries, all foreign keys or indexes declared in the models but deliberately not added by the migrations. Earlier migrations add reference columns as plain integers so SQLite can apply them.
- Only 2 are from P5: the `assessment_retention` FKs, intentionally omitted.
- No table or column is missing and no type differs. Staging is consistent with the intended schema.
- Adding the missing FK constraints is a possible future hardening migration; it is not required for 0018/0019.

**Tooling and configuration**
- `validate_staging_migration.py --report-name <file>.json`: a rehearsal on another remote branch writes its own report, and it is refused as a name for the staging evidence file. Tests: `test_staging_validation.py` 6/6.
- `.env.example`: a `NEON_API_KEY` placeholder documented as for operations scripts only; a user-level environment variable is preferred.
- The Neon API is reached through the OS trust store (`truststore`), because this network inspects TLS (Netskope). Verification is never disabled.
- Evidence files are each written once, by their own run: `staging_migration_report.json` (staging 0019, 22/22), `rehearsal_0019_report.json` / `rehearsal_0019_validator.json`, `neon_state_20261002.json`, `neon_functional_0019_report.json`, `neon_staging_and_pgtests_report.json`.

**Still requires approval:**
- The production migration itself (runbook `docs/PRODUCTION_MIGRATION_0018_0019.md`), naming the exact operation and target.
- Governance acceptance of the provisional P3 rules and P5 retention rules, and the named designation holders.
- Optional hardening that changes the production branch: Neon branch protection on `production` (currently `protected=False`), and a longer history retention (currently 6 h).
- Moving `NEON_API_KEY` out of `backend/.env` into a user-level variable (recommended).

### P4 Evidence traceability: ✅ implemented and verified locally (2026-10-02/03); Stage 4 signals **provisional**

Isolated databases only. The primary Neon database was not connected to, migrated or written to; staging was not touched. Nothing was deployed or committed.

**What was built**

| Area | Behaviour |
|---|---|
| Field provenance (R2.1/R2.4) | Every extracted profile field records its source document and version, page (PDF `[Page N]`) or sheet (XLSX `[Sheet: …]`), the quote, the extraction date and method (AI / RULES / INTAKE_FORM), and a confidence (`app/governance/provenance.py`). The AI prompt now asks for a verbatim quote per field; **confidence is derived by string matching against the documents' own text, never from the model** (any confidence it sends is dropped): HIGH = a cited quote is in the document and contains the value; MEDIUM = the value is in the document without such a quote (always the case for the rule-based extractor), or a verified quote supports a paraphrase; LOW = not found anywhere. List fields are checked item by item and take their weakest item; whole-word matching ("UK" is not found in "Ukraine"). Multi-file uploads are verified against every document (`source_document_ids`); `source_document_id` stays the first for compatibility. Intake-form values are `USER_PROVIDED`; a correction replaces the field's record with `USER_CORRECTION`, keeping the extracted record inside it. Each field also shows whether the business owner has confirmed the profile. The pre-creation preview (`/analyze-document`) returns the same provenance. |
| Expired evidence (R2.6, Stage 2 AC) | One rule (`app/services/evidence_currency.py`): superseded → never used; no expiry / not expired → used; expired or unreadable expiry date → used only after a decision `USE_AS_EVIDENCE` (reason ≥ 10 chars) or kept on file but `EXCLUDE_FROM_EVIDENCE`. Decisions are append-only (`evidence_acknowledgements`), bound to the document version and expiry date, by the owner or an FCRM Analyst / Manager / Admin (read-only roles refused by the global gate; others 403 + `ACCESS_DENIED`). Risk identification refuses (409 `EXPIRED_EVIDENCE_UNACKNOWLEDGED`) on `advance-stage`, `advance-stage-async` and `analyze` while any current document is undecided, and the analysis only ever sees usable documents. Superseding a version that is no longer current is refused (409), so one document can't have two current versions. |
| Outdated approved sources (Stage 5 AC) | Attaching a passage from a source past its review date needs `acknowledge_outdated` + a reason (409 `OUTDATED_SOURCE_UNACKNOWLEDGED` otherwise); the link records `outdated_at_attach`, the reason and who. |
| Intake snapshots (R3.4) | `intake_snapshots`, append-only: a version of the request or the profile on every change — creation, edit, submission (both submit paths), profile extraction/creation, correction, confirmation — with the full values after it, each changed field's old and new value, who, when, why, and whether it was validated. A record from before P4 gets a `BASELINE` of its state before its first recorded change; nothing earlier is claimed. `PATCH /assessments/{id}` now needs a `change_reason` once the profile is confirmed, and a change to a triage-relevant field (policy `material_intake_fields`) withdraws the confirmation so the owner confirms again. A no-op edit writes nothing. `GET /api/assessments/{id}/intake-history`. |
| R5.4 categories | Every explainability statement carries `evidence_category`: DIRECT_EVIDENCE (the request as stated, documents on file, verified quotes, approved-source passages), EXTRACTED_INFORMATION (each extracted field with confidence and location), SYSTEM_INTERPRETATION (AI factors, Stage 4 rules, calculations, challenge findings, automated draft text), ANALYST_COMMENTARY (manual factors, analyst ratings, reviewer corrections, edited drafts) and ASSUMPTION (provisional calculations, missing information, draft assumptions). Decisions carry none. Kept alongside the Stage 19 four-way kinds. Quotes from documents the viewer sees masked are masked. |
| Stage 4 rules | `app/risk_engine/stage4_rules.py`, applied to every AI and rules-only factor set: cross-border payment → geographic, product and transaction factors plus a sanctions consideration; remote digital channel → delivery-channel factor with channel and authentication considerations; third-party processor → third-party factor. A category the AI called not applicable (or omitted) becomes applicable and **unrated** (`FORCED_APPLICABLE` / `ADDED`), with the AI's view kept in the rationale; the analyst must rate it or exclude it with a reason. A rule never asserts an indicator (a forced `SANCTIONS_EXPOSURE` would escalate to CRITICAL). Each factor stores `rule_triggers` (rule, ruleset version and status, matched signals). Signals are configuration (`STAGE4_RULES_FILE`), versioned, `PROVISIONAL_PENDING_BUSINESS_VALIDATION`; `GET /api/risk-methodologies/active/stage4-rules`. The AI evaluation report measures the AI's own call (rule-forced categories are not counted as AI applicability, and are not compared until a person rates or excludes them). |
| Indicator editing (R4.2) | `PATCH /api/assessments/{id}/risk-factors/{fid}/indicators` (pipeline roles, reason required, known indicators only, applicable and non-excluded factors). The system value is read from the factor; the change is an `APPLIED` ledger row classified by P3 materiality (an escalation indicator is CRITICAL and needs approval before the committee); the calculation is recomputed. |
| Audit | New actions `INTAKE_UPDATED`, `PROFILE_CORRECTED`, `PROFILE_CONFIRMED` (with old → new values), `EVIDENCE_EXPIRY_ACKNOWLEDGED`, `RISK_INDICATORS_CHANGED`; the ANALYSIS event lists the Stage 4 rules applied. |
| UI | Field-provenance table and intake history on the Intake step; Use / Exclude decision with reason under each expired-document warning; rule note and **Edit indicators** on each factor; acknowledgement field for outdated library sources; evidence-category badge and filter in the explainability panel; reason field on the request edit form. Structured refusals (`{code, message}`) are now shown as their message. |

**Migration `0020_evidence_traceability`** (revises `0019_retention_lifecycle`): new tables `intake_snapshots` and `evidence_acknowledgements`; nullable columns on `assessment_intelligence` (`field_provenance`, `extraction_method`, `extracted_at`, `source_document_ids`) and `risk_factors.rule_triggers`; `source_evidence_links.outdated_at_attach` (default false), `outdated_acknowledgement_reason`, `outdated_acknowledged_by_id`. Additive, idempotent, **no backfill** (existing profiles keep NULL provenance; it was never recorded and isn't invented). Downgrade refuses while any snapshot, acknowledgement, recorded provenance, rule trigger or outdated-source acknowledgement exists.

**Test results (isolated databases only)**

| Suite | Result |
|---|---|
| Backend `pytest` (unit, api, workflow) | **447 passed, 1 failed**. New: `test_p4_traceability.py` 13, `test_p4_provenance_and_rules.py` 19, `test_migration_0020.py` 7. The failure is `test_missing_requirements_batch4.py::test_expired_approval_reaches_owner_and_reviewer`: the test computes "yesterday" from the local date (already 3 Oct in IST) while the service uses the UTC date (still 2 Oct), so it fails between 00:00 and 05:30 local time. Unrelated to P4; the P6 session has a fix |
| Legacy scripts | NFR 22/22, workflow 9/9, delegation 13/13, reporting 7/7; degraded 60, phase-1 71, phase-2 86 checks; `test_risk_engine.py` clean. All exit 0 |
| `tsc -b` | clean |
| ESLint (new/changed files) | no findings |
| Playwright | **22 passed, 3 failed** — the 3 are the §5 baseline. New `evidence-traceability.spec.ts` 2/2 |

**Changes to existing tests, and why**
- `test_methodology_and_sources.py`: attaching the outdated library source now expects the 409, then attaches with an acknowledgement.
- `test_migration_0019.py`: upgrades pinned to `0019_retention_lifecycle` (0020 is now on top).
- `test_phase1_evidence_scoring.py`, `test_phase2_governance.py`, `test_degraded_mode.py`, `test_stage17_reporting.py`: their fixtures are cross-border payment requests, so the brief's rules now (correctly) require product, transaction and channel factors. The scripts keep their own purpose; where they finalise a case, the analyst excludes the rule-required factors with a reason (R4.5); expected AI-evaluation agreement is 5/7 (3 rule-forced categories not yet reviewed are not compared). No gate was weakened.
- `tests/e2e_server.py` / `tests/support/fake_llm.py`: the E2E fake now answers document extraction with quotes from the document's labelled lines (opt-in helper; the pytest default is unchanged), and the extractor never uses a real key in E2E.

**Design defaults and open decisions (not specified by the brief)**
- Q-1 (unchanged, provisional): the Stage 4 signal keyword lists and structural checks. Keyword matching ignores negation ("no third party" still fires the third-party rule); the analyst excludes with a reason.
- The three-level confidence scale and its thresholds.
- Rules-only mode: a rule may add a category the rule engine can't evaluate (e.g. product risk). It is added as applicable, unrated and INSUFFICIENT_EVIDENCE, and is still listed as not evaluated by the engine.
- "Sanctions-related factors are considered" is implemented as an explicit consideration on the geographic factor, not a separate category or a forced indicator.
- Who may decide on expired evidence (owner, FCRM Analyst, Manager, Admin); the 10-character minimum reason.
- Which request fields withdraw a validated profile's confirmation (reuses the P3 `material_intake_fields` list).

**Known limitations**
- Profiles created before P4 have no provenance (shown as nothing, not as low confidence).
- Title, description and evidence are the model's summaries, edited by the requester before submission; they get no field provenance.
- Page numbers exist only for PDFs and sheets only for XLSX; DOCX/TXT/CSV give the document and quote, no finer location.
- The request edit form (drafts) sends the reason when given; the server enforces it.

**Staging:** 0020 has **not** been run on the staging branch (it is at 0019) and the validator has no 0020-specific checks yet. Running it needs your approval. The primary stays at 0017.

### P6 Reassessment (Stage 18): ✅ implemented and verified in isolation (2026-10-03); merged into the shared tree after P4

**How it was built:** in an isolated copy of the working tree, because P4 was editing the same tree at the same time. It was then merged by a three-way merge (`git merge-file`; no commits): base snapshot vs the shared tree (P4) vs the P6 copy. The merge record is under "P6 merge" below.

**What was built**

| Area | Behaviour |
|---|---|
| Parent lifecycle (R18.4) | The approved parent's **pipeline status never changes**. A flag, `assessments.reassessment_state`, tracks its standing: NULL (in force), `UNDER_REASSESSMENT` or `SUPERSEDED` (terminal, with `superseded_by_id` and `superseded_at`). Opening a reassessment marks the parent `UNDER_REASSESSMENT`. Approval of the child (`APPROVED` / `APPROVED_WITH_CONDITIONS`) makes it `SUPERSEDED`. If the child is rejected, manager-rejected, or closed or withdrawn without approval, the parent goes back in force. The rule runs in `workflow.transition()`, which every status change goes through, so no decision path can skip it (`app/services/reassessment_lifecycle.py`). An ORM guard keeps `SUPERSEDED` terminal and `superseded_by_id` write-once. |
| Trigger linking | Opening a reassessment links the parent's OPEN or ACKNOWLEDGED triggers to it (`REASSESSMENT_CREATED`, with a note). On supersession, any that remain are closed against the child. A review-date trigger raised while a reassessment is open is recorded against it, not queued. A superseded parent raises no more review triggers (scheduled sweep, on-demand check and work queue). |
| Flag and resolve | Flag: the owner, or an FCRM Analyst, Manager or Admin; refused on a superseded parent (reassess its successor). Resolve: FCRM Analyst, Manager or Admin. `ACKNOWLEDGED` takes an optional note; `DISMISSED` needs a reason; "linked to reassessment" only if one exists. A settled trigger can't be reopened (409). Identities (`detected_by_id`, `resolved_by_id`) are recorded. Refusals are logged as `ACCESS_DENIED`. |
| Start reassessment | Owner, FCRM Analyst, Manager or Admin, on an approved or closed assessment, not superseded, with none in progress. **The UI gate now follows the server** (`GET /reassessment/status` → `actions`), replacing the old Committee/Admin gate. A reassessment now gets **its own reference** (it had none before). |
| Due queue (AC2) | **Every** open or acknowledged trigger, not only expiry and periodic review. FCRM Analysts see the queue within their visibility and entity scope. Owners and managers see their own; Admins see everything. Superseded parents are excluded. Each item carries its trigger status, the parent's standing, any in-progress reassessment, and `can_start_reassessment` / `can_resolve` from the server. |
| Reused fields (R18.3) | `reused_fields` in the comparison: label, value, source assessment, source date and age in days. Shown on the reassessment from intake onwards, with a prompt to re-confirm anything over a year old. |
| Structured compare (R18.2) | `structured` in the comparison: inherent and residual scores before and after, with the change; risk factors by category (likelihood, impact, score and severity; ADDED, REMOVED, CHANGED or UNCHANGED, with the changed fields); controls with design adequacy and operating effectiveness; committee conditions; and summary counts. The existing flat fields are kept. |
| Audit | Dedicated actions replace `STATUS_CHANGE`: `REASSESSMENT_TRIGGER_FLAGGED`, `REASSESSMENT_TRIGGER_RESOLVED`, `REASSESSMENT_DUE_DETECTED` (still records who was notified), `REASSESSMENT_OPENED`, `REASSESSMENT_PARENT_SUPERSEDED` (on parent and child), `REASSESSMENT_ENDED_WITHOUT_APPROVAL`. |
| UI | New **`ReassessmentPanel.tsx`**, shown at every stage (beside the retention panel). It shows the parent's standing (in force, under reassessment with a link, or superseded by #N), the reused-field table, the comparison, triggers with Acknowledge / Dismiss / Link actions, the flag form, and the propose form. Every action and every "why not" comes from the server. The old inline block in the approval stage was removed. The work queue's "Reassessments due" table shows trigger labels and the next step for each item. |

**New or changed endpoints:**
- New: `GET /api/assessments/{id}/reassessment/status`.
- Changed: `GET …/compare` adds `structured` and `reused_fields`; `PATCH …/triggers/{tid}` adds `resolution_note`, refuses to reopen settled triggers, and logs refusals; `POST …/flag-trigger` and `…/propose-change` use the server rules and log refusals; `GET /api/workflow/work-queue` `reassessment_alerts` gains the new item fields.

**Migration `0021_reassessment_lifecycle`** (revises `0020_evidence_traceability`):
- Additive and idempotent. Adds three `assessments` columns and an index, and three `reassessment_triggers` columns (plain integers, no FK).
- Backfill is derived only from existing rows: a parent whose latest reassessment is approved becomes `SUPERSEDED` (with the child and the decision time); a parent with an undecided reassessment becomes `UNDER_REASSESSMENT`. Pipeline statuses are untouched.
- Downgrade refuses once triggers carry P6 identities or notes. The state columns are derived and are dropped.

**Tests (isolated workspace; the merged-tree results are under "P6 merge")**
- `tests/api/test_p6_reassessment.py` (12): opening marks the parent and links its triggers; child approval supersedes (no more review triggers, no re-propose or flag); rejection, manager rejection and closure leave the parent in force; the real withdraw route releases the parent; the ORM guard; flag and resolve with dedicated audit, refusals logged, dismiss needs a reason, no reopen; the due queue (all triggers, analysts, actions, linking while under reassessment); the role rule; reused fields with age; the structured comparison.
- `tests/unit/test_migration_0021.py` (3): backfill, a no-op rerun, a lossless downgrade with re-upgrade, and the downgrade refusal.
- `frontend/tests/e2e/reassessment.spec.ts` (3): the owner proposes through the UI on an approved assessment and the parent shows "Under reassessment"; a committee member gets no propose action; the reassessment shows carried-over fields with source and the comparison; approving the reassessment through the real committee route supersedes the parent, whose status stays APPROVED.
- Related existing tests still pass (24 in the reassessment, actor-identity and expiry suites). **Fixed:** `test_expired_approval_reaches_owner_and_reviewer` failed between 00:00 and 05:30 IST, because the test used the local date and the code uses UTC. The test now uses the UTC date.

**Open decisions and limitations**
- A reassessment still clones intake fields only. Its analysis, factors, controls and conditions are produced afresh by its own pipeline; the comparison shows the differences.
- Amending an approved child (amendment path) leaves its parent superseded.
- The 12-, 24- and 36-month review intervals are the existing Stage 18 defaults, unchanged by P6.
- Retention: each assessment's retention clock still runs from its own final decision (P5 note); chain-level retention is an open governance question.

**P6 merge (2026-10-03, ~00:58, after P4 reported done):**
- **Method:** a three-way merge (`git merge-file`; no commits) of base (snapshot at P6 start) vs the shared tree (P4 final) vs the P6/P7 workspace.
- **Result:** 35 files. 22 applied cleanly, 11 new, 2 merged where both sides added:
  - `AssessmentWorkflow.tsx`: both import blocks kept.
  - This file: P4's report and the P6/P7 reports kept in order.

  No other conflicts. Previous versions are backed up outside the repo.
- **After the merge:**
  - `test_migration_0020.py` pinned to `0020_evidence_traceability`, at P4's request.
  - The shared migration-test helper `_without_columns` (in `test_migration_0017.py`) now uses `PRAGMA legacy_alter_table = ON`. Its table rebuild had rewritten other tables' foreign keys to a dropped `<table>_pre`, which broke the 0018 round-trip tests once 0021's downgrade rebuilt `assessments`. This is a test-fixture fix; the migrations are unchanged.
- **Migration chain:** single head `0021_reassessment_lifecycle` (0018 → 0019 → 0020 → 0021).

**Merged-tree results (2026-10-03; isolated databases only)**

| Suite | Result |
|---|---|
| Backend `pytest` (unit, api, workflow) | **476 passed, 0 failed** |
| Migration tests 0017–0021 | 22/22 |
| Legacy scripts | NFR 22/22, workflow 9/9, delegation 13/13, reporting 7/7, degraded 60, phase-1 71, phase-2 86, `test_risk_engine.py` clean; all exit 0 |
| `tsc -b` | clean |
| ESLint | new or changed files clean; `AssessmentWorkflow.tsx` 10 pre-existing findings (unchanged) |
| Playwright | **28 passed, 0 failed**. Includes P4's correction of the 3 §5 baseline tests (test setup now confirms the profile; the gate is unchanged) |

No database other than throwaway SQLite was used. Staging is still at 0019, production at 0017.

### P7 Encryption (R19): ✅ implemented and verified in isolation (2026-10-03); merged with P6 after P4

**What was built**
- **Database TLS enforced in code.**
  - `app/security_settings.py: database_tls_problem()`: a remote Postgres URL must use `sslmode` require, verify-ca or verify-full.
  - `app/database.py` refuses to create an engine otherwise, with a message that names the host and never the credentials.
  - Local databases (SQLite, Postgres on localhost) are exempt.
  - `DATABASE_ALLOW_INSECURE_TRANSPORT=true` is a deliberate non-production override; it is refused with `APP_ENV=production`.
- **Production key requirement.** With `APP_ENV=production`, `prepare_database()` refuses to start (`InsecureConfiguration`) unless all of these hold: `JWT_SECRET` is at least 32 characters; `FILE_ENCRYPTION_KEY` is a valid Fernet key; `FORCE_HTTPS=true`; database TLS is required; and the insecure override is off. Messages never echo a key or secret. Development mode reports the same gaps without enforcing them.
- **Security posture** (`GET /api/system/security-posture`, Admin) gains `app_env`, `production_enforced`, `production_ready`, `jwt_secret_strong`, `file_encryption_key_valid`, `database_insecure_transport_override` and `database_encryption_at_rest` (`PROVIDER_MANAGED_NOT_VERIFIABLE_IN_APP` on Postgres). `database_tls_required` now uses the real rule instead of a text match.
- **Scripts.**
  - `scripts/verify_encryption.py`: read-only evidence covering live TLS (driver view), files, backups and keys.
  - `scripts/encrypt_existing_files.py`: a dry run by default; with `--apply` it encrypts each plaintext upload atomically, after checking it decrypts back to the original bytes.

**Verification evidence (2026-10-03; read-only; `backend/eval_results/encryption_verification_staging.json`)**

| Check | Result |
|---|---|
| Staging URL requires TLS | ✅ `sslmode=require` |
| Live connection encrypted (driver view) | ✅ **TLSv1.3, TLS_AES_256_GCM_SHA384, 256-bit**; read-only query OK |
| `FILE_ENCRYPTION_KEY` valid; `JWT_SECRET` ≥ 32 chars | ✅ / ✅ |
| Uploaded files encrypted | ❌ **379/405**. 26 plaintext files predate the key; many local uploads are test-suite artifacts. **→ ✅ 2026-10-03: the 26 encrypted with `encrypt_existing_files.py --apply` (run by the user); re-check: 628 files, 0 plaintext** (the total grew with test-suite uploads, written encrypted) |
| Backups encrypted | ❌ **0/46**: the plaintext staging snapshot `backups/staging-pre-0019-…` (a copy of production data). **2026-10-03:** application backups are now encrypted by the backup service (below); the later staging snapshots (`staging-pre-0021-…`, `staging-pre-0022-…`) are Fernet-encrypted; the 0019 snapshot was **deleted by the user (2026-10-03)**. Remaining backups: all data files encrypted |
| `FORCE_HTTPS` | ❌ off (local development) |
| Database encryption at rest | Provider-managed (Neon). **Not verifiable from the application**; confirm from Neon's current security and compliance documentation |

Production was **not** checked in this phase (a read-only `--target database` run is available on approval).

**Tests:** `tests/unit/test_p7_encryption.py` (13): the TLS rule matrix (8 URL forms; messages never include credentials); the database module refusing a non-TLS remote URL in a subprocess (override honoured only outside production); production start refused until every requirement is met; files encrypted at rest and read back; the posture fields and Admin-only access; and the encrypt script (dry run changes nothing, `--apply` converts and verifies, never prints names).

**Open items (need a decision)**
- ~~Encrypt the 26 legacy uploads~~ **Done 2026-10-03** (user ran `--apply`; dry-run re-check: 0 plaintext, 628 encrypted). Keep `FILE_ENCRYPTION_KEY` backed up outside the repo; without it these files cannot be read.
- Plaintext staging snapshot `backups/staging-pre-0019-…`: **decision 2026-10-03: delete** (superseded by the encrypted 0021/0022 snapshots and Neon recovery branches). **Deleted by the user (2026-10-03)**; confirmed absent.
- ~~Backup encryption in the backup service~~ **Done 2026-10-03:** with `FILE_ENCRYPTION_KEY` set, `create_backup` takes the database copy into memory and writes it only encrypted (`database.sqlite.enc` / `database.dump.enc`, RAWENC1 + Fernet), encrypts any plaintext upload on the way, and flags `encrypted` in the manifest and on System Health; verify and restore decrypt in memory (no plaintext copy on disk). Without a key the backup is plaintext, flagged and logged. Docs: `BACKUP_AND_RECOVERY.md` (incl. decrypting a Postgres dump for `pg_restore`). Checked in the browser (E2E server): a new backup shows "Encrypted" and "✓ Verified".
- ~~Key rotation~~ **Done 2026-10-03:** `FILE_ENCRYPTION_PREVIOUS_KEYS` (decrypt-only, validated without echoing) and `scripts/rotate_file_encryption_key.py` (dry run by default; `--apply` re-encrypts uploads, backups and snapshot `*.enc` files under the current key, verifies each, replaces atomically, updates backup manifest checksums; exits non-zero on unreadable files; never prints names or keys). `verify_encryption.py` now counts snapshot Fernet files as encrypted and manifests as metadata. Tests: `tests/unit/test_backup_encryption_and_rotation.py` (8). Not done: rotating the real key — an operational step for whoever holds the secret store.
- Database encryption at rest: obtain the provider's evidence (e.g. its compliance report).

### P8 Tests and validation: ✅ complete (2026-10-03), including the staging run of 0020/0021 (32/32)

Isolated databases only. No Neon database was connected to; nothing was migrated, deployed or committed.

**What was done**

| Item | Result |
|---|---|
| The 3 baseline Playwright failures (§5) | **Fixed by correcting the test setup, never the gate.** `status-workflow.spec.ts` ×2 and `analyze-assessment.spec.ts` (dashboard Analyze) ran analysis before the owner had confirmed the profile; they now call `readyForRiskIdentification` first. The R3.3 gate is unchanged. |
| Route × role permission sweep | New `tests/api/test_p8_permission_sweep.py` (18). The operations are enumerated from the OpenAPI schema (**197**: 92 GET, 72 POST, 30 PATCH, 5 PUT; 115 assessment-scoped), so a route added later is covered automatically. Invariants on **every** route: no token → 401 (only `POST /api/auth/login` is public); Auditor and Executive → 403 on every write, each refusal logged as `ACCESS_DENIED`, nothing changed (status, lifecycle, `updated_at`, audit count); another Business User → 403 on every `{assessment_id}` / `{document_id}` route, read or write, each logged, nothing changed; an entity-scoped analyst → 403 on every assessment-scoped read outside their legal entity, while their own entity opens. Plus a curated table of 12 privileged operations: system, user and methodology administration, the source library, AI and retention reports, analysis, rating and indicator edits. Probes use empty bodies and placeholder ids, so nothing a probe reaches can change data. |
| E2E journeys | New `access-control.spec.ts` (2): a reviewing manager (case submitted to them, so visibility is not the reason) sees a CONFIDENTIAL document masked, Download disabled, the card number never shown, the original refused by the API (403) and the refusal on the audit trail; an Auditor sees the read-only banner and no "New Assessment", can open a case, and every write is refused (403) with the case unchanged. With P4's `evidence-traceability.spec.ts` (2) and P6's `reassessment.spec.ts` (3), the major journeys are covered. The P1 "browser check of the disabled Download button" is now covered. |
| UI nit | The `[Unhandled rejection]` Vite logged when a stage move was refused is gone: three callers of `handleAdvanceStage` now catch the already-reported refusal, as the other caller did. |
| Migration validation tooling | `validate_staging_migration.py`: new checks for 0020 (tables and columns present, nothing backfilled) and 0021 (columns present; every SUPERSEDED / UNDER_REASSESSMENT flag backed by its parent and child rows, also after the round trip); a fingerprint of every assessment's pipeline status, lifecycle status and draft flag, compared after the upgrade, the rollback and the re-upgrade; and `--rollback-to <revision>` (needs `--rollback-check`), so the rollback leg undoes every new migration instead of only the last. New rehearsal test: a database really at 0019 (0020 objects removed), with an approved reassessment and one in progress, migrated to head, rolled back to 0019 and upgraded again; all checks pass, and the real staging evidence file is byte-for-byte unchanged. |
| Test isolation | The access-control journey returns its case afterwards, so the Approvals-page journeys see only their own case. |

**Test results (isolated databases only)**

| Suite | Result |
|---|---|
| Backend `pytest` (unit, api, workflow) | **495 passed, 0 failed** (476 merged-tree baseline + 18 sweep + 1 rehearsal) |
| Validator tests | `test_staging_validation.py` 7/7 |
| Legacy scripts | NFR 22/22, workflow 9/9, delegation 13/13, reporting 7/7; degraded 60, phase-1 71, phase-2 86 checks; `test_risk_engine.py` clean. All exit 0 |
| `tsc -b` | clean |
| ESLint | `AssessmentWorkflow.tsx` 10 findings, the same pre-existing count as at P5; none in changed lines |
| Playwright | **30 passed, 0 failed** (first time since the baseline; was 17/20 passed with 3 failures before P4) |

**Staging validation plan for 0020 and 0021 (needs your approval; nothing has been run)**

- Target: the isolated child branch `staging-risk-workbench` (`ep-small-leaf-…`), currently at `0019_retention_lifecycle`. The primary (`ep-falling-water-…`, at 0017) is never touched; the validator refuses it and any protected host.
- Recovery point before any change: a Neon point-in-time-restore timestamp, plus a read-only JSON snapshot of every table, as for 0019.
- Commands (from `backend/`):
  1. `python scripts/validate_staging_migration.py --status-only --expect-revision 0019_retention_lifecycle` (read-only identity and revision check)
  2. `python scripts/validate_staging_migration.py --expect-revision 0019_retention_lifecycle --rollback-check --rollback-to 0019_retention_lifecycle`
- Stop conditions: any refusal; revision ≠ 0019; any FAIL (rows lost, a status changed, a backfill invented, a flag without its rows, start-up failing); any sign the target is the primary.
- Expected: 0019 → 0021; two new tables and the new columns; no rows changed in existing tables; staging has no reassessments, so the 0021 states should all be "in force"; rollback to 0019 and re-upgrade lossless. Report: `eval_results/staging_migration_report.json` (replaces the 0019 report; the 0019 result stays recorded above).

**Staging validation of 0020 + 0021: ✅ passed 32/32 on the isolated Neon branch (2026-10-03, approved in chat for staging only)**

*Recovery point (taken before any change):*
- Neon child branch `staging-pre-0021-20261003T033511Z` (`br-square-sun-b34kc03v`), parent `staging-risk-workbench` at LSN 0/2D7D5D8, no compute endpoint.
- Point-in-time-restore target **2026-10-03 03:35:11 UTC**.
- A logical snapshot of all 47 tables (970 rows) in `backend/backups/staging-pre-0021-20261003T033511Z/`, **each file Fernet-encrypted with `FILE_ENCRYPTION_KEY`** (no plaintext copy of the data); every file was decrypted and checked against its SHA-256 in the manifest.

*Run:*
1. `--status-only --expect-revision 0019_retention_lifecycle` (read-only): target `ep-small-leaf-…` (not the primary `ep-falling-water-…`), at 0019, 10 assessments.
2. `--expect-revision 0019_retention_lifecycle --rollback-check --rollback-to 0019_retention_lifecycle`: **32/32**. Upgraded 0019 → 0021; no rows lost or added; votes and every assessment's status unchanged; 0017–0019 objects intact; 0020 tables and columns present with nothing backfilled; 0021 columns present and every flag backed by its rows; the app starts; **rolled back to 0019** losslessly with statuses unchanged; re-upgraded to 0021 with the 0019 backfill and the 0021 flags derived again. Report: `eval_results/staging_migration_report.json`.

*Independent read-only post-check:* revision `0021_reassessment_lifecycle`; all 47 pre-existing tables have the same row count as the snapshot; the only new tables are `intake_snapshots` and `evidence_acknowledgements` (empty).

*Observation (legacy data, not changed):* the 0021 backfill marked assessment #6 `UNDER_REASSESSMENT`. #6 is still at INHERENT_RISK_ASSESSMENT (never approved) but has three reassessment children (#8, #9, #10) created before P6's rules existed. The migration rule (`a parent with an undecided reassessment`) doesn't require the parent to have been approved. Production has the same data, so the same flag would be set there. Decide before the production change whether that's acceptable or whether the backfill should only flag approved parents.

*Safety:* migration authorized for the staging host only; no protected-host flag; the primary was not connected to; nothing deployed or committed.

**Still open after P8**
- The staging run above, then a separately approved production change (0018 → 0021 from 0017).
- Governance approvals listed in each phase (P3 R-GOV rules, P5 periods, P4 Stage 4 signals and confidence scale).
- The application suites have run on Postgres only for the P1–P5 governance paths (Neon validation section); P4/P6 paths have run on SQLite only.

### Migration 0022: "under reassessment" only for previously approved assessments (2026-10-03)

**Defect.** 0021's backfill (`0021_reassessment_lifecycle.py`, upgrade) marked a parent `UNDER_REASSESSMENT` whenever it had a reassessment without a final decision (`elif any(c[1] not in FINAL for c in children)`), without checking that the parent had ever been approved. On staging (and the same data in production) assessment #6 was flagged. #6 has never been approved: no committee or manager decision, and its only committee vote was a DISSENT. It has three historical reassessment records (#8, #9, #10, all undecided, created 2026-09-23 before the reassessment rules existed).

**Fix: new forward-only migration `0022_reassessment_approval`** (revises `0021_reassessment_lifecycle`; 0021 is not edited because it is already applied on staging). For every assessment flagged `UNDER_REASSESSMENT` that fails the rule below, it clears the flag (NULL = in force). It never sets a flag and changes nothing else: no row is added or deleted; statuses, SUPERSEDED parents, children and triggers are untouched. Idempotent. Downgrade is a deliberate no-op (restoring the wrong flags would re-introduce the defect).

**Rule (the exact condition).** An assessment `p` stays `UNDER_REASSESSMENT` only if there is a reassessment `c` with
`c.parent_assessment_id = p.id AND c.status NOT IN ('APPROVED','APPROVED_WITH_CONDITIONS','REJECTED','MANAGER_REJECTED','CLOSED')`
and `p` was approved before `c` began:
`(p.committee_decision IN ('APPROVED','APPROVED_WITH_CONDITIONS') OR p.status IN ('APPROVED','APPROVED_WITH_CONDITIONS')) AND p.committee_decided_at IS NOT NULL AND p.committee_decided_at <= c.created_at`.

**Assumptions / ambiguity in "previously approved".**
- Evidence of approval = the committee's binding decision (`committee_decision`), or an approved pipeline status. The decision stays recorded when an approved case is later reopened (e.g. #7 is back at HUMAN_REVIEW with `APPROVED_WITH_CONDITIONS` recorded), so it counts.
- "Before the reassessment began" = the decision time (`committee_decided_at`) is no later than the reassessment's `created_at`. Without a decision time, "before" can't be shown, so the flag is cleared (none on staging or production).
- A manager approval alone is not an approval: only the committee decides (R12).
- Staging / production data: 0 assessments with an approved status but no committee decision, and 0 with an approved decision but no time. So the definition is unambiguous for the real data; the `status` alternative only matters for synthetic or legacy rows.
- Not changed: SUPERSEDED parents (0021 sets them when a reassessment is approved, without checking the parent's approval; no such case exists in the data), and the runtime rule. A reassessment can be started on a CLOSED assessment that was never approved, which would flag it at runtime; a follow-up decision.

**Bug caught on staging, fixed before any change:** the first revision id (`0022_reassessment_state_approval_rule`, 37 characters) exceeded `alembic_version.version_num VARCHAR(32)`. SQLite doesn't enforce the length, so every local test passed; Postgres refused the upgrade and rolled it back (staging verified unchanged at 0021). Renamed to `0022_reassessment_approval`; new test `test_every_revision_id_fits_postgres_alembic_version`.

**Tests:** `tests/unit/test_migration_0022.py` (5): the staging path (0021 wrongly flags, 0022 clears exactly the wrong flags and only that column); the production path (one run to head); approved parent with an open reassessment stays flagged; never-approved parent with historical reassessments (the #6 case), approved-after-the-reassessment-began, approved without a decision time, no reassessment, and never-approved with a rejected reassessment are all in force; superseded untouched; every assessment and trigger row and the row counts unchanged; re-run is a no-op; downgrade 0022 → 0021 → 0020 and re-upgrade preserve data; the full chain from a database really at 0017; and the revision-id length guard. The validator (`validate_staging_migration.py`) now also checks "no UNDER_REASSESSMENT without a prior approval" when the code head is 0022 or later. Regression: backend pytest **514 passed, 0 failed**; validator tests 7/7; legacy scripts all pass; `tsc` clean; Playwright 30/30.

**Staging (approved for staging only):**
- Recovery point: Neon branch `staging-pre-0022-20261003T044522Z` (`br-snowy-snow-b3pltpqw`, LSN 0/2E31AD0, no compute); PITR 2026-10-03 04:45:22 UTC; encrypted snapshot of 49 tables / 970 rows in `backend/backups/staging-pre-0022-20261003T044522Z/`, every file verified. The earlier recovery points are untouched.
- `--expect-revision 0021_reassessment_lifecycle --rollback-check --rollback-to 0021_reassessment_lifecycle --report-name staging_migration_0022_report.json`: **32/32** (report `eval_results/staging_migration_0022_report.json`; the 0021 report is kept). States before: 9 in force + #6 UNDER_REASSESSMENT; after: 10 in force; "under reassessment without prior approval" 0.
- Independent read-only comparison with the decrypted snapshot: all 49 tables have the same row counts; the **only** change in `assessments` is #6 `reassessment_state` UNDER_REASSESSMENT → NULL (no other column, `updated_at` included); `reassessment_triggers` unchanged; #6's three reassessments intact.
- Production was not connected to (still 0017). Its upgrade will now run 0018 → 0022 in one run; 0021 and 0022 apply back to back, so #6 ends in force there too.

### Postgres test pass, source-library uploads, deployment config (2026-10-03)

**Postgres test pass: ✅ 43 passed, 0 failed.** On a temporary Neon branch of *staging* (`pgtests-20261003T051224Z`, never production), with an empty database whose schema the app built from scratch through all migrations (0001 → 0022) on real Postgres: `test_p4_traceability.py`, `test_p6_reassessment.py`, `test_p8_permission_sweep.py` (all 197 operations). The connection string stayed in memory; the temporary branch was deleted afterwards (remaining: production, production-pre-0019, staging-pre-0021-…, staging-pre-0022-…, staging-risk-workbench). Report: `eval_results/pgtests_report_20261003T051224Z.json`. The P4 and P6 paths, previously SQLite-only, now have Postgres evidence.

**Source library: upload a document.** `POST /api/sources/extract` (Policy Admin / Admin) reads an uploaded PDF, DOCX, DOC, XLSX, TXT or CSV (50 MB limit) and returns its text; nothing is stored. In **Add source**, a drag-and-drop zone fills the text, title and reference from the file; the user reviews and saves it as a draft, then approves as before (the R5.2 draft → approve flow is unchanged). The original file isn't kept: the source's text is the record, and the reference names the file. Tests: `tests/api/test_source_upload.py` (4: TXT and DOCX read, nothing stored until saved, only library admins, unsupported / empty / text-less files refused) and `frontend/tests/e2e/source-library.spec.ts` (upload, auto-fill, save, approve, then an analyst finds it; an unsupported file is refused).

**Source library: original documents kept (migration `0023_source_files`, revises `0022_reassessment_approval`).** "Save as draft" after an upload now sends the reviewed text and the original file together (`POST /api/sources/with-file`, library admins). The file is stored like assessment evidence (same store, encrypted at rest with `FILE_ENCRYPTION_KEY`), with its name, type, size and SHA-256 on the source; the storage path is never returned. It is set once and never replaced (draft edits don't touch it; approved sources aren't editable; a revision is a new version). `GET /api/sources/{id}/file` downloads it as an attachment to the same people who can see the source (approved: everyone signed in; drafts and retired: library admins), and every download is audited (`DOCUMENT_DOWNLOADED`). Pasted sources have no file. The table shows the file name, size and a Download button. Migration 0023 adds five nullable columns to `approved_sources` with no backfill; downgrade refuses once any source has a stored file. **Staging: ✅ 0022 → 0023 passed 33/33 (2026-10-03, approved in chat for staging only).** Recovery point first: Neon branch `staging-pre-0023-20261003T055156Z` (`br-dry-flower-b35aqu1x`, LSN 0/2E64FB0, no compute), PITR 2026-10-03 05:51:56 UTC, encrypted snapshot of 49 tables / 970 rows (verified) in `backend/backups/staging-pre-0023-20261003T055156Z/`. Then `--expect-revision 0022_reassessment_approval --rollback-check --rollback-to 0022_reassessment_approval --report-name staging_migration_0023_report.json`: at head, no rows or statuses changed, the 0023 columns present, rollback to 0022 and re-upgrade lossless (report `eval_results/staging_migration_0023_report.json`; earlier reports kept). Independent read-only post-check: revision 0023, all 49 tables have the same row counts as the snapshot, no table added or missing, the five columns present (staging has no library sources yet). Production not connected to (still 0017).

*Regression after 0023:* the full backend run gave 529 passed and 2 failed. Both failures were `test_migration_0022.py` tests that upgraded to "head" and expected 0022, which became stale once 0023 existed. They are now pinned to `0022_reassessment_approval` (the convention for every migration test), and the 0022 + 0023 migration tests pass (8/8). The browser journey `source-library.spec.ts` (upload, keep the original, download it byte for byte, approve, search) passed. `tsc` and ESLint are clean. Tests: `test_source_upload.py` (+2: kept encrypted, SHA-256, exact bytes on download, draft hidden from non-admins, audited, not swappable; pasted sources have no file, non-admins refused, bad input refused), `test_migration_0023.py` (3), the permission sweep's privileged table (+2 routes), and the E2E journey (the row shows the original; Download returns the exact bytes).

**Source library: redesign.** `SourceLibrary.css` (scoped `.sl-*`): padded cards with spacing between them, consistent buttons (primary, secondary, danger, small; 40 px tall with focus rings), a two-column form, a dashed dropzone, status pills (Draft, Approved, Retired, Outdated), a roomier table, reason boxes for approve and retire, and a single-column layout on phones. Checked in the browser at desktop and phone width; no console errors. Fixed a duplicate element id that pointed the "Title" label at the heading.

**Deployment config for the OpenAI key:** `render.yaml` declares `LLM_PROVIDER=openai`, `OPENAI_API_KEY` (set in the Render dashboard), `OPENAI_MODEL=gpt-4.1-mini` and `OPENAI_EMBEDDING_MODEL`; `.github/workflows/ai-evals.yml` uses the `OPENAI_API_KEY` secret with `LIVE_LLM=1`; `docs/DEPLOYMENT.md` updated. The repository secret and the Render variable still have to be set by you.

**Regression after these changes:** backend pytest **518 passed, 0 failed**; legacy scripts all pass; `tsc` clean; ESLint clean on changed files; Playwright **31/31**.

### AI provider switched to OpenAI (2026-10-03, on request)

- `app/ai/provider.py` chooses the provider for every AI call (document extraction, risk-factor identification, likelihood/impact suggestions, control identification and design, draft narrative, embeddings, and the evaluation judge): **OpenAI** (`LLM_PROVIDER=openai`, the default when `OPENAI_API_KEY` is set) or OpenRouter. Both are OpenAI-compatible, so only the URL, key and model names differ. The modules keep their existing variable names.
- **Models:** the key can use 135 models. Chosen: chat `gpt-4.1-mini` (`OPENAI_MODEL`), embeddings `text-embedding-3-small` (`OPENAI_EMBEDDING_MODEL`, 1536 dimensions = the vector column). Every call sets a temperature and JSON mode, which the newer reasoning models (GPT-5.x, o-series) don't accept, so a non-reasoning chat model is required; `gpt-4.1` is the higher-quality option.
- **Fix needed for OpenAI:** metering added an OpenRouter-only `usage` field to every chat request, which OpenAI rejects; it is now sent to OpenRouter only. Usage logs record the real provider (`openai`).
- **Key storage:** `OPENAI_API_KEY` is in `backend/.env` only (git-ignored), never printed. The key was pasted in chat, so it should be rotated in the OpenAI dashboard and the new one put in `backend/.env`.
- **Tests stay offline:** `tests/conftest.py`, the E2E server and every legacy script pin the offline OpenRouter configuration with an empty OpenAI key before the app is imported, so no test run can use the real key. Live evaluations opt in with `LIVE_LLM=1`.
- **Live check** (synthetic data, throwaway SQLite): document extraction, risk-factor identification (10 categories, every quote verified) and embeddings (1536 dimensions) all succeeded on `gpt-4.1-mini` / `text-embedding-3-small`, each logged with provider `openai`.
- Tests: new `tests/unit/test_ai_provider.py` (provider and model selection; OpenRouter-only fields never sent to OpenAI; the suite is pinned offline). Regression: backend pytest **501 passed, 0 failed** (495 + 6 provider tests); legacy scripts all pass; `tsc` clean; Playwright 30/30.

### Backups encrypted, key rotation, manager-decision fix, committee quorum and finding rules (2026-10-03, "Requirements completion check" session)

All of this is provisional policy: `policy_status` stays `PROVISIONAL_PENDING_GOVERNANCE_APPROVAL`, and no production setting has been changed. The direction behind it is recorded in `docs/GOVERNANCE_DECISIONS.md` ("Project direction of 2026-10-03").

| Item | Change |
|---|---|
| **Backups encrypted (R19)** | See P7 open items above: the backup service encrypts the database copy and plaintext uploads, and verify/restore decrypt in memory. Key rotation uses `FILE_ENCRYPTION_PREVIOUS_KEYS` and `scripts/rotate_file_encryption_key.py`. Tests: `test_backup_encryption_and_rotation.py` (8). |
| **Manager decision** | Admin is removed from the three SUBMITTED_TO_MANAGER transitions. Only the assigned Manager or a time-bound delegate may decide, and refusals are logged as `ACCESS_DENIED`. An **emergency approver** is an Admin-created delegation to a named Manager: labelled `EMERGENCY …` in the `DELEGATION_CREATED` audit event, and it never gives the Admin the authority. Tests: `test_manager_decision_authority.py` (6), plus the emergency test below. |
| **Committee quorum (G-5)** | New `app/governance/quorum.py` (policy key `committee_quorum`). See the details below this table. |
| **Challenge findings (G-4)** | See the details below this table. |
| **Migration `0024_committee_acceptance`** | Adds `challenge_findings.accepted_by_id` and `acceptance_authority`. It is additive and backfills nothing. Downgrade (batch mode) refuses while any Committee acceptance exists. Tests: `test_migration_0024.py` (3). **Not run on staging or production.** |
| **UI** | Accept is offered to committee members for MEDIUM findings only, and an earlier acceptance is shown as no longer counting. Resolve stays with the pipeline roles. The new designations appear in the Users designation editor (it reads the list from the API). |
| **Test setup** | Real quorum users replace any bypass: conftest `fcrm_rep` and `business_rep`, `c3`/`c4` in the AW.7 script, representatives in the Stage 14 script and the E2E seed (`fcrmrep`, `businessrep`). Setups resolve findings instead of a Manager accepting them. |

**Committee quorum (G-5) in detail:**
- A final decision (approve, approve with conditions, reject) needs current votes from 3 eligible members, among them:
  - an FCRM/Compliance representative;
  - an independent Business Risk representative.

  Each is identified by a new designation, `COMMITTEE_FCRM_COMPLIANCE_REP` or `COMMITTEE_BUSINESS_RISK_REP`, held with Committee Member, and the two must be different people.
- **Never counted:** the requester, the requester's manager, the case manager and the manager-decision taker, preparers (`independence.involved`), and both people behind a delegated vote. Abstentions don't count.
- **Readiness blockers:** `COMMITTEE_QUORUM_NOT_MET` at the final decision. `COMMITTEE_QUORUM_UNAVAILABLE` at submission, when the active committee can't form an eligible quorum.
- Deferral needs no quorum. Readiness returns a `quorum` block (counted members, each seat and why it does or doesn't count).
- Tests: `test_committee_quorum_and_findings.py` (12).

**Challenge findings (G-4) in detail:**
- **HIGH/CRITICAL** block submission until RESOLVED (`has_blocking_findings`, readiness `HIGH_FINDINGS_OPEN`). They can't be accepted (409), and an acceptance recorded earlier no longer counts.
- **MEDIUM** is a warning at submission and a blocker at the final decision, until it is resolved or accepted.
- **Who may accept a MEDIUM finding:** an eligible committee member (Committee Member role, not a party or preparer), while the case is Ready for Committee, Committee Review or Deferred. Anyone else gets a 403, logged.
- An acceptance records the member, their id, the reason and `acceptance_authority = COMMITTEE`.

**Assumptions to confirm (provisional):**
- Who may accept a MEDIUM finding on the Committee's behalf: any eligible member, or only the Chair? Currently any eligible member; the setting is `finding_acceptors`.
- That abstentions don't count toward quorum (`count_abstentions`).
- That "independent" means not a party or preparer, since there is no home business unit per user.
- That an Admin-created delegation serves as the emergency approver.

**Behaviour change for existing data:**
- On staging and production, findings accepted under the earlier rule no longer count:
  - HIGH ones must be resolved;
  - MEDIUM ones resolved or re-accepted by the Committee.
- Submission is refused until an Admin assigns both representative designations to active committee members.

**Results (isolated databases only):**
- Backend pytest: **538 passed, then 7 failed, all migration round trips through 0024**. Fixed by batch mode in 0024's downgrade; the affected files were re-run: 17/17.
- Legacy scripts: AW.7 13/13, Stage 14 9/9, phase-2 86, NFR 22/22, reporting 7/7, phase-1 71, degraded 60.
- `tsc`: clean.
- **Playwright: 31/31.**

## 7. Requirement checklist

| Requirement | Status |
|---|---|
| 1. Confidence scores and page numbers for extracted fields | ✅ P4 (confidence scale is a design default, open) |
| 1. Acknowledgement before using expired documents | ✅ P4 |
| 1. Versioned snapshots of intake values | ✅ P4 |
| 1. Five-way evidence categories (R5.4) | ✅ P4 |
| 1. Fixed deterministic Stage 4 rules | ✅ P4 (signal definitions provisional, Q-1) |
| 2. Edit/unmap controls with audit history | ✅ P2 |
| 2. Override logging without overwriting calculated values | ✅ P2 |
| 2. Mandatory challenge review | ✅ P2 |
| 2. Re-cast committee votes with immutable history | ✅ P2 |
| 2. Approved exception workflow for SoD | ✅ P3 (provisional policy, pending governance approval) |
| 3. Secure downloads of confidential files | ✅ P1 |
| 3. Soft-deleted assessments inaccessible (found in audit) | ✅ P1 |
| Migration safety guard (incident follow-up) | ✅ |
| 3. Configurable retention policies | ✅ P5 (provisional periods and rules, pending governance approval; 0019 validated on staging 22/22) |
| 3. Encryption at rest verified and documented | ✅ P7: TLS verified live (TLSv1.3); keys enforced in production; DB at-rest provider-managed (not app-verifiable); all uploads encrypted (628/628), backups encrypted and key rotation tooling (2026-10-03); the plaintext 0019 staging snapshot deleted (user, 2026-10-03) |
| 4. Reassessment screens and journey | ✅ P6 (migration 0021; staging/production not migrated) |
| 5. Backend pytest coverage | ✅ P8: 495 passed, 0 failed (unit, api, workflow; re-run 2026-10-03). Live AI evals (`tests/evals`, opt-in) are a separate quality gate, not counted here |
| 5. Playwright E2E for major workflows | ✅ P8: 30/30 passing (baseline failures fixed by test setup; access-control, traceability, reassessment, retention, SoD and governance journeys) |
| 5. Role- and entity-based access validation | ✅ P8: route × role sweep over all 197 operations (unauthenticated, read-only roles, object-level, entity scope, privileged table) |
| 5. Migrations on staging Neon | ✅ 0011–0019 on an isolated child branch: 0017 13/13 (P2); 0017→0018 15/15 including rollback and re-upgrade (P3). 0018→0019 22/22 including rollback and re-upgrade (P5). **0019→0021 32/32 including rollback to 0019 and re-upgrade (P8); 0021→0022 32/32 including rollback to 0021 and re-upgrade; 0022→0023 33/33 including rollback to 0022 and re-upgrade**; staging is at 0023, production at 0017 |
