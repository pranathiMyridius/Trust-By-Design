# Requirements Traceability

Requirement IDs follow the codebase (`R<stage>.<n>`, `AW.n`) as recovered in `docs/AI_SDLC_EVIDENCE.md`; design-level requirements introduced in this stage are `DR-nn`. Test references: existing files in `backend/` and `frontend/tests/e2e/`; **new** = to be written in Stage 3.

## 1. Functional stages

| Requirement | Architecture component | API | Database entity | UI | AI workflow | Test / evaluation |
|---|---|---|---|---|---|---|
| **R1** Business request intake (R1.1–R1.5) | Intake service, triage, routing | `POST/GET/PATCH /api/assessments`, `/workflow/submit`, `/check-duplicates` | `assessments` | S2 Create, S1 Dashboard | — | `tests/api/test_assessments.py`; e2e `create-assessment.spec.ts`; new: grouped-section form validation |
| **R2** Document & evidence management (R2.1–R2.6) | Documents, file storage (Fernet), processing jobs | `POST/GET /{id}/documents`, `/documents/{doc}/file`, `/evidence-gaps`, `/processing-jobs/*` | `assessment_documents`, `processing_jobs`, `document_embeddings` | S8 Evidence view, S4 progress | Document extraction (AI) | `test_stage19_nfr.py` (processing); new: upload limits, version supersede, masked text by role |
| **R3** Intake validation & structuring (R3.1–R3.4) | Intelligence service, consistency check | `/intelligence`, `/intelligence/confirm`, `/consistency-check` | `assessment_intelligence` | S3 Evidence stage | `validate_input` node | new: confirm gate; `validate_input` unit tests |
| **R4** Risk-factor identification (R4.1–R4.5) | LangGraph workflow, AI gateway, evidence engine, degraded mode | `POST /analyze-async`, `GET /risk-factors`, `POST /risk-factors`, `PATCH …/exclude` | `risk_factors`, `analysis_runs`, `ai_usage_logs` | S4, S5, S7 | `identify_risks`, `verify_evidence`, `tag_and_aggregate`, `rules_fallback` | `tests/workflow/test_langgraph.py`, `test_degraded_mode.py`, `tests/unit/test_llm_integration.py`, `test_phase1_evidence_scoring.py`; DeepEval dataset (category/indicator recall, quote verification); e2e `analyze-assessment.spec.ts` |
| **R5** Risk methodology (AI-inferred title) | Methodology service, versioning, lock-on-use | `/api/risk-methodologies/*` | `risk_methodologies` | Admin > Governance | — | `test_phase2_governance.py`; new: activation validates contiguous bands, weights > 0 |
| **R6** Inherent risk — manual calculation, no AI (R6.1–R6.7) | Scoring engine, inherent service | `PATCH …/rating`, `GET /inherent-risk`, `GET /scores` (new), `POST /inherent-risk/override` | `inherent_risk_calculations`, `risk_factors` | S5, S6 | `score_provisional` | `tests/unit/test_scoring.py`, `test_risk_logic.py`, `test_risk_engine.py`; new: replay test, confidence, weight-0, override floor rules |
| **R7** Control identification & assessment (R7.1–R7.7) | Control engine, gap detection | `/controls*`, `/control-summary`, `/risk-factors/{fid}/identify-controls`, `/controls/{cid}/assess-design` | `controls`, `control_assessments`, `control_gaps`, `control_conditions` | S3 Controls stage | Control identification / design (AI, advisory) | new: D-04 regression (both endpoints return 200/502, not 500); control rating unit tests |
| **R8** Residual risk (AI-inferred title) | Residual service, grid | `GET /residual-risk`, `GET /scores` | `residual_risk_calculations` | S3 Residual stage | — | `test_phase2_governance.py`; new: floors, grid validation, replay |
| **R9** Assessment draft (R9.1–R9.4) | Draft service, advisory wording | `/draft*` | `assessment_drafts` | S9 | Draft narrative (AI, advisory) | new: advisory wording guard; draft versioning |
| **R10** FCRM analyst review (R10.1–R10.6) | Review services, overrides, info requests | `/fcrm-review`, `/overrides`, `/request-information`, `/provide-information`, `/comments` | `assessment_fcrm_reviews`, `assessment_overrides`, `assessment_comments` | S10 | — | new: actor-from-JWT, override record completeness |
| **R11** Conditional challenge review (R11.1–R11.6) | Challenge engine | `/challenge-review`, `/challenge-findings/*`, `/api/challenge-triggers` | `challenge_findings`, `challenge_trigger_configs` | S7, S10 | — | new: trigger rules, blocking logic, `OVERRIDE_REDUCED_RISK` |
| **R12** Committee decision (R12.1–R12.6) | Approvals service, decision record | `/committee-votes`, `/committee-decision`, `/decision-package`, `/decision-record` | `committee_votes`, `committee_conditions`, `decision_records` | S11 | — | new: missing_requirements gate, checksum verify, SoD for non-delegated votes; `docs/E2E_TEST_PLAN.md` AW cases |
| **R13** Conditions & remediation (R13.1–R13.5) | Action items, escalation | `/action-items*` | `action_items` | S11, Approval stage | — | new: closure approval by committee (D-bug §5.2 of inspection) |
| **R14** Workflow & status management (R14.1–R14.5) | Workflow service (`TRANSITIONS`, SLA, escalation) | `/workflow/*`, `/advance-stage` | `workflow_transitions`, `assessments.status/workflow_status` | S3 tracker, Work queue | — | `test_stage14_workflow.py`; e2e `status-workflow.spec.ts`; new: row-lock concurrency test |
| **R15** Authentication, roles, access (AI-inferred) | Auth, permission matrix, visibility | `/api/auth/*`, `/api/users` | `users` | Login, Users admin | — | new: permission matrix table-driven test (every route × role), D-01/D-02/D-05 regressions |
| **R16** Audit trail & explainability (R16.1–R16.4) | Audit service, explainability, retention | `/audit`, `/audit/all`, `/explain*`, `/audit-export`, `/retention*`, `/api/retention/{permissions,policies,eligibility,legal-holds}` (P5) | `audit_events`, `assessment_retention`, `retention_policies` (legacy), `retention_policy_versions`, `legal_hold_events` (P5) | S12, ExplainabilityPanel | — | new: every mutating route writes audit with actor_id; append-only enforcement |
| **R17** Reporting & monitoring (R17.1–R17.4) | Reporting service, AI metrics | `/api/reports/*`, `/api/system/ai-reliability` | `ai_usage_logs`, `ai_evaluation_records` | Reports | Metering | `test_stage17_reporting.py` |
| **AW.1–AW.7** Approval workflow & delegation | Approvals, delegation rules | `/submit-to-manager`, `/manager-decision`, `/api/delegations/*` | `approval_delegations`, `assessments.manager_*` | Approvals, Delegations | — | `test_aw7_delegation.py`; E2E plan AW.2–AW.5 |
| **R18** Reassessment & change management (R18.1–R18.4) | Reassessment service | `/reassessment/*` | `reassessment_triggers`, `assessments.parent_assessment_id` | Decision stage | — | new: permission checks (D-05), parent immutability |
| **R19** Non-functional (security, performance, reliability, accessibility, scalability, explainability) | Middleware, masking, jobs, backups | `/api/system/*`, `/health/ready` (new) | — | All | Masking in `metered_post` | `test_stage19_nfr.py`; new: CSP header, login throttling, Neon pool reconnect test |

## 2. Design requirements introduced in Stage 2

| ID | Requirement | Component | API | DB | UI | AI workflow | Test |
|---|---|---|---|---|---|---|---|
| DR-01 | LLM never produces a score/band of record | Scoring engine | `/scores` | calc tables | S5 | `identify_risks` schema rejects numbers | unit: parser drops numeric fields; eval |
| DR-02 | Every AI output traceable to run, model, prompt, workflow versions | AI gateway, run recorder | `/analysis-runs*` | `analysis_runs`, `prompt_versions` | S7 provenance line | all nodes | new: run row per task; startup fails on unversioned prompt change |
| DR-03 | Scores reproducible | Scoring engine | `/api/system/integrity` | `inputs_sha256`, `scoring_engine_version` | — | `score_provisional` | new: replay test on fixtures |
| DR-04 | Evidence vs inference vs missing distinguished | Evidence engine, explainability | `/risk-factors`, `/explain/statements` | `risk_factors.evidence` | S7 three blocks | `verify_evidence` | e2e: blocks rendered; unit: origin labels |
| DR-05 | Unrated / provisional never displayed as low | UI + API `rated` flag | `/risk-factors`, `/scores` | — | UnratedBadge | — | e2e: convert the two `test.fail()` cases to passing |
| DR-06 | Actor always from authenticated user | Auth + services | all writes | `*_by_id`, `audit_events.actor_id` | — | — | new: table-driven test sending forged `rated_by` |
| DR-07 | Overrides never silent; downward overrides four-eyes | Override service, challenge engine | `/inherent-risk/override`, `/overrides` | `assessment_overrides`, calc versions | S10 ledger | — | new |
| DR-08 | Neon connection resilience, no SQLite runtime fallback | `database.py`, `config.py` | `/health/ready` | — | — | — | new: startup fails without DATABASE_URL in prod; pre-ping reconnect |
| DR-09 | Deterministic confidence | Scoring engine | `/scores` | `confidence_level` | S5 chip | — | unit |
| DR-10 | No GET writes | API layer | listed GETs | — | — | — | new: test asserts no INSERT/UPDATE during GET (SQLAlchemy event counter) |
| DR-11 | Financial-crime typology tagging | `tag_and_aggregate` | `/risk-factors` | `crime_typologies` | S5 chips | `tag_and_aggregate` | unit + eval cases for AML/sanctions/fraud/bribery |
| DR-12 | Structured gate errors | Error envelope | all 409 | — | disabled-action reasons | — | api tests on each gate |

## 3. Coverage gaps to close in Stage 3

`docs/AI_SDLC_EVIDENCE.md` records that stages 1–3, 6, 7, 9–13, 16 and 18 have **no automated tests**. Minimum Stage 3 test additions: permission matrix sweep (R15), actor-from-JWT (DR-06), scoring replay (DR-03), override rules (DR-07), D-04 controls regression (R7), committee gate + checksum (R12), unrated display e2e (DR-05), and a pinned-model eval baseline (R4).
