# Assessment Lifecycle and Human-in-the-Loop

*(Added to the prescribed package structure because the lifecycle is referenced by API, UX and tests.)*

## 1. Two status fields — keep both, clarify roles

| Field | Values | Role |
|---|---|---|
| `assessments.status` (**pipeline status**) | 19 values (see `database/schema-design.md` §4) | **Authoritative.** Only `workflow.transition()` may change it. Checked against `TRANSITIONS`. |
| `assessments.workflow_status` (**lifecycle status**) | 16 values | **Derived** by `derive_workflow_status()` for display, SLA and work queue. Never set directly. |

## 2. Mapping the brief's states to the implementation

| Brief state | Implemented as | Notes |
|---|---|---|
| DRAFT | `status=INTAKE`, `is_draft=true` → `workflow_status=DRAFT` | |
| SUBMITTED | `status=INTAKE`, `is_draft=false` → `SUBMITTED` | Reference id + triage assigned |
| VALIDATING | `status=EVIDENCE_COLLECTION` → `INTAKE_VALIDATION` (profile unconfirmed) → `EVIDENCE_REVIEW` (confirmed) | |
| ANALYZING | **Not a status.** Active `processing_jobs(RISK_ANALYSIS)` + `analysis_runs(RUNNING)` while status stays `EVIDENCE_COLLECTION` (first run) or current stage (re-run) | Avoids a status that can get stuck if a worker dies; job recovery already exists |
| ANALYSIS_COMPLETE | `RISK_IDENTIFICATION` → `INHERENT_RISK_ASSESSMENT` → `CONTROL_ASSESSMENT` → `RESIDUAL_RISK` (all `RISK_ASSESSMENT_IN_PROGRESS`) | Analyst works through the stages |
| PENDING_REVIEW | `HUMAN_REVIEW` (`ANALYST_REVIEW`) → `SUBMITTED_TO_MANAGER` (`CHALLENGE_REVIEW`) → `READY_FOR_COMMITTEE` → `COMMITTEE_REVIEW` | Two approval tiers |
| APPROVED / REJECTED | `APPROVED`, `APPROVED_WITH_CONDITIONS`, `DEFERRED`, `REJECTED`, `MANAGER_REJECTED` | |
| FINALIZED | Decision record frozen at committee decision; `CLOSED` when conditions/actions complete | |
| (side states) | `INFORMATION_REQUESTED`, `RETURNED_BY_MANAGER`, `REMEDIATION` | |

Diagram: `diagrams/state-machine.mmd`.

## 3. Allowed transitions (`services/workflow.py::TRANSITIONS`, retained)

"Who" is the existing TRANSITIONS table. "Gate" shows **current** behaviour and, where this design changes it, the **target** (→).

| From | To | Who | Gate (current → target) |
|---|---|---|---|
| INTAKE | EVIDENCE_COLLECTION | owner, analyst, manager, admin | not draft; mandatory fields |
| INTAKE | RISK_IDENTIFICATION | analyst, manager, admin | (fast path, existing) |
| INTAKE | CLOSED | owner, admin | reason (withdraw) |
| EVIDENCE_COLLECTION | RISK_IDENTIFICATION | owner, analyst, manager, admin | profile confirmed; analysis runs **synchronously** inside `advance-stage` (500 on exception) → analysis runs as an **async job**; transition applied by the worker on run SUCCEEDED/DEGRADED on behalf of the requester |
| EVIDENCE_COLLECTION | CLOSED | admin | reason |
| RISK_IDENTIFICATION | INHERENT_RISK_ASSESSMENT | analyst, manager, admin | not `unavailable`; rules-only acknowledged; **all eligible factors rated** (existing check) |
| INHERENT_RISK_ASSESSMENT | CONTROL_ASSESSMENT | analyst, manager, admin | inherent frozen; best-effort AI control suggestion (mid-request commit) → suggestion becomes an explicit, separately-audited action; single transaction |
| CONTROL_ASSESSMENT | RESIDUAL_RISK | analyst, manager, admin | legacy `AssessmentChallenge.outcome` required → every eligible factor has ≥1 assessed control or an accepted `NO_CONTROL` gap (legacy challenge retired) |
| RESIDUAL_RISK | HUMAN_REVIEW | analyst, manager, admin | residual frozen; draft generation best-effort → unchanged (draft failure falls back to deterministic template) |
| HUMAN_REVIEW | SUBMITTED_TO_MANAGER | owner (→ + admin) | owner, status, manager assigned → + draft accepted, FCRM review saved |
| SUBMITTED_TO_MANAGER | READY_FOR_COMMITTEE / RETURNED_BY_MANAGER / MANAGER_REJECTED | assigned manager, delegate, admin | approve: no unresolved comments, no blocking findings, inherent not provisional (existing) |
| RETURNED_BY_MANAGER | SUBMITTED_TO_MANAGER | owner | |
| READY_FOR_COMMITTEE | COMMITTEE_REVIEW | committee | opened by first vote or explicit open |
| COMMITTEE_REVIEW | APPROVED / APPROVED_WITH_CONDITIONS / DEFERRED / REJECTED | committee, delegate | approve: `missing_requirements` empty; conditions required for AWC |
| DEFERRED | COMMITTEE_REVIEW | committee | reopened |
| APPROVED, APPROVED_WITH_CONDITIONS, REJECTED, MANAGER_REJECTED | REMEDIATION | committee (amend) | reason |
| same four | CLOSED | analyst, committee, admin | no open action items (approved statuses) |
| REMEDIATION | INTAKE | owner, analyst, manager, admin | |
| REMEDIATION | RISK_IDENTIFICATION | analyst, manager, admin | re-run analysis |
| any active (INTAKE … DEFERRED) | INFORMATION_REQUESTED | analyst, manager, admin (+ committee from committee stages) | target + note |
| INFORMATION_REQUESTED | previous status (default HUMAN_REVIEW) | owner, pipeline, committee | response |
| CLOSED | — | — | terminal |

Every transition: reason required (existing 422), row in `workflow_transitions`, audit event, SLA clock reset.

## 4. Failure and edge behaviour

| Situation | Behaviour |
|---|---|
| Analysis fails (AI outage, fallback on) | Run `DEGRADED`, `assessment_mode=rules_only`, factors provisional; status advances to RISK_IDENTIFICATION; cannot go further until an analyst acknowledges (`/acknowledge-degraded`) |
| Analysis fails (unavailable / other error) | Run `UNAVAILABLE`/`FAILED`; **status unchanged**; previous factors (if any) remain current; UI shows banner + Retry |
| Input incomplete | `validate_input` stops the run (no LLM cost); `GATE_NOT_MET` issues shown with "Request information" / "Edit intake" actions |
| Manager returns | `RETURNED_BY_MANAGER` (`AMENDMENT_REQUIRED`); owner edits and resubmits; history in transitions |
| Manager / committee rejects | `MANAGER_REJECTED` / `REJECTED`; locked (read-only); committee may amend → `REMEDIATION` |
| Re-run analysis | Allowed in EVIDENCE_COLLECTION, RISK_IDENTIFICATION, REMEDIATION; requires reason if a prior successful run exists; new `analysis_runs` row (`parent_run_id`); old factors superseded, not deleted; carried-forward ratings flagged (see `ai/langgraph-workflow.md` §6) |
| Re-run after inherent approved | Not allowed directly — send back via REMEDIATION (committee) or information request, so approved stages are not silently changed |
| Reassessment (periodic / trigger) | New child assessment (`parent_assessment_id`); parent stays immutable |
| Previous versions | Every factor, calculation, control assessment, draft and document keeps `version`/`is_current`; decision record freezes the state at decision time |

## 5. Human-in-the-loop design

Reviewer capabilities (screen references in `ux/wireframes.md`):

| Capability | How | Endpoint(s) | Recorded |
|---|---|---|---|
| Review risk findings | Factor cards with applicability, indicators, rationale | `GET /risk-factors` | — |
| Inspect evidence | Quote with source link to document viewer at offset | `GET /documents/{id}/file` | access via audit export only |
| Understand AI reasoning | "AI inference" panel + provenance (model, prompt version, run) | `GET /analysis-runs/{uuid}` | — |
| Review risk scores | Score breakdown, contributions, rules fired, confidence | `GET /scores`, `/explain` | — |
| Rate / re-rate | L×I with AI suggestion side by side; reason if different | `PATCH …/rating` | factor version, audit before/after |
| Challenge a finding | Exclude factor (reason), add manual factor, add comment on section, raise information request | `PATCH …/exclude`, `POST /risk-factors`, `POST /comments`, `POST /request-information` | override/audit |
| Resolve / accept challenge findings | Resolution note or accepted-risk reason | `PATCH /challenge-findings/{id}/resolve|accept` | `resolved_by_id` / `accepted_by_id` |
| Request reassessment | Re-run (analyst) or committee amendment | `POST /analyze-async`, `POST /amend` | run lineage, transition |
| Override (authorised) | Inherent band/value, control outcome, residual (manager+) with rules in `ai/risk-scoring.md` §7 | `POST /inherent-risk/override`, `POST /overrides` | `assessment_overrides` + calc version |
| Approve / reject | Manager decision, committee vote + decision | approvals endpoints | transition, decision record |

**Override record (every override):** user id + name, timestamp, previous value, new value, source of previous value (AI / RULES / CALCULATED / HUMAN), reason, optional comment, analysis run id. The original value is never overwritten — it remains on the prior version row and in `ai_value` / `calculated_*`. Diagram: `diagrams/human-review.mmd`.
