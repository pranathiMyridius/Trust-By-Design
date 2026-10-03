# Schema Design

Conventions (existing, retained): integer surrogate PK `id`; FK columns without ORM relationships; enum-like values as `String` validated in Python; JSON lists/dicts in `Text` (see `database-architecture.md` §5); `created_at` / `updated_at` UTC; versioned rows use `version`, `is_current`, `superseded_at`.

New conventions: every write-side table that records a human act has `*_by_id → users.id` (the display name may be kept for history, but the id is authoritative and comes from the JWT). Every AI-derived row has `analysis_run_id → analysis_runs.id`.

ER diagram: `diagrams/er-diagram.mmd` (core entities only; 37 tables do not fit one readable diagram).

---

## 1. Mapping of requested entities to the schema

| Requested entity | Physical table(s) | Decision |
|---|---|---|
| Assessment | `assessments` | **Retain + extend** |
| Product | columns on `assessments` (`product_or_service_name`, `business_owner`, `legal_entity`, …) | **Retain**. Separate `products` table = production option (product inventory across reassessments; lineage already via `parent_assessment_id`) |
| RiskDimension | Code list `RISK_CATEGORIES` (10) + weights in `risk_methodologies.factor_weights` | **Retain as reference data in code + methodology**; no table (a table would duplicate the fingerprinted config) |
| RiskFactor / RiskFinding | `risk_factors` (AI/RULES/MANUAL factors); `challenge_findings` (rule-based review findings) | **Retain + extend** |
| Evidence | JSON `risk_factors.evidence` (quote, source id, doc id/version, checksum, offset, verification); `assessment_documents`; `assessment_intelligence` | **Retain** for MVP; production adds normalised `factor_evidence` |
| Recommendation | `assessment_drafts` (analyst_recommendation, recommended_conditions, advisory); `challenge_findings.recommended_action` | **Retain** |
| AssessmentIntelligence | `assessment_intelligence` | **Retain** |
| RiskScore | `inherent_risk_calculations`, `residual_risk_calculations` (+ mirrored `assessments.inherent_score/residual_score`) | **Retain + extend** (engine version, confidence) |
| Review | `assessment_fcrm_reviews`, `assessment_overrides`, `assessment_comments`, `challenge_findings` | **Retain + fix actor** |
| Approval | `assessments.manager_* / committee_*`, `committee_votes`, `committee_conditions`, `approval_delegations`, `decision_records` | **Retain**; production adds append-only `approval_decisions` |
| AuditLog | `audit_events` (+ `workflow_transitions`) | **Retain + extend** (`request_id`, `analysis_run_id`; production hash chain) |
| PromptVersion | — | **New** `prompt_versions` |
| ModelVersion | `ai_usage_logs.requested_model/response_model` | **Extend**: model captured on `analysis_runs`; allow-list in config. No table in MVP |
| AssessmentRun | — | **New** `analysis_runs` |
| Legacy dimension scores | `risk_results` | **Freeze** (read-only history; no new writes after Stage 3) |

---

## 2. Disposition of all 37 existing tables

| Table | Disposition | Notes |
|---|---|---|
| `users` | Retain | Add `AUDITOR` role value (production) |
| `assessments` | Extend | See §3.1 |
| `assessment_documents` | Retain | Already versioned (`version`, `supersedes_id`) |
| `assessment_intelligence` | Extend | `confirmed_by_id` |
| `document_embeddings` | Retain | pgvector `Vector(1536)` |
| `risk_factors` | Extend | See §3.2 |
| `risk_results` | Freeze | Stop writing; keep for history |
| `risk_methodologies` | Modify | Drop use of `weights`/`thresholds` legacy columns (keep columns, stop reading) |
| `inherent_risk_calculations` | Extend | See §3.3 |
| `residual_risk_calculations` | Extend | `scoring_engine_version`; add to protected |
| `controls`, `control_assessments`, `control_conditions`, `control_gaps` | Retain | `added_by_id`, `assessed_by_id` |
| `challenge_trigger_configs`, `challenge_findings` | Retain | `resolved_by_id`, `accepted_by_id`; add to protected |
| `assessment_challenges` | **Retire** | Legacy challenge; stop gating on it (read-only) |
| `assessment_fcrm_reviews` | Modify | `reviewed_by_id` (drop hard-coded "FCRM Reviewer") |
| `assessment_overrides` | Extend | `comment`, `previous_value_source`, `analysis_run_id` |
| `assessment_comments` | Retain | |
| `assessment_drafts` | Extend | `analysis_run_id`, `prompt_version` |
| `committee_votes`, `committee_conditions`, `approval_delegations` | Retain | |
| `decision_records` | Extend + protect | Append-only; record format 1.1 adds run ids |
| `action_items` | Retain | |
| `workflow_transitions` | Retain | Append-only |
| `audit_events` | Extend | See §3.4 |
| `retention_policies`, `assessment_retention` | Retain | |
| `reassessment_triggers` | Retain | |
| `processing_jobs` | Extend | `analysis_run_id`; partial unique index |
| `calculator_drafts` | Retain | |
| `ai_usage_logs` | Extend | `analysis_run_id`, `prompt_key`, `prompt_version` |
| `ai_evaluation_records` | Extend | `analysis_run_id` |
| `country_risk`, `reference_data_snapshots` | Retain | Add FK `country_risk.snapshot_id` (production) |

---

## 3. Entity specifications (changed / new)

Types are PostgreSQL types. **Req** = NOT NULL.

### 3.1 `assessments` (extend)

Existing columns are listed in full in the models (`models/assessment.py`, ~90 columns). Changes only:

| Column | Type | Req | Change / purpose |
|---|---|---|---|
| `owner_id` | int FK users | → Req for non-legacy rows | Authoritative owner (already set on create) |
| `submitted_by_id` | int FK users | N | **New** — replaces client-supplied `submitted_by` string as authority |
| `latest_analysis_run_id` | int FK analysis_runs | N | **New** — the run whose factors are current |
| `confidence_level` | varchar(10) | N | **New** — mirror of current inherent calc (`HIGH`/`MEDIUM`/`LOW`) |
| `lock_version` | int default 0 | Req | **New** — optimistic concurrency for edits |

Indexes (existing: `status`, `workflow_status`, `legal_entity`, `business_unit`, `assessment_mode`, `created_at`, `owner_id`, `manager_id`). **Add:** `(workflow_status, updated_at DESC)` for dashboard; `(risk_level)`; partial `WHERE is_draft = false`.

### 3.2 `risk_factors` (extend)

| Column | Type | Req | Purpose |
|---|---|---|---|
| `analysis_run_id` | int FK analysis_runs | N (Req for `source in ('AI','RULES')`) | **New** — run that produced the factor |
| `crime_typologies` | text (JSON list) | N | **New** — subset of `AML`, `TERRORIST_FINANCING`, `SANCTIONS`, `FRAUD`, `BRIBERY_CORRUPTION` |
| `rated_by_id`, `excluded_by_id`, `added_by_id` | int FK users | N | **New** — authoritative actors |
| `inference_statement` | — | — | Not a column: `rationale` and `misuse_scenario` are explicitly labelled AI inference in API/UI |

Existing important fields retained: `category`, `applicable`, `likelihood`, `impact`, `score`, `severity`, `ai_suggested_likelihood/impact/rationale/at`, `rating_source`, `indicators`, `evidence_status`, `evidence`, `rejected_indicators`, `missing_information`, `rationale`, `misuse_scenario`, `source` (`AI`/`RULES`/`MANUAL`), `excluded*`, `version`, `is_current`, `superseded_at`.

Indexes: existing `assessment_id`; **add** `(assessment_id, is_current)`.

### 3.3 `inherent_risk_calculations` (extend)

| Column | Type | Req | Purpose |
|---|---|---|---|
| `scoring_engine_version` | varchar(30) | Req (default `'1.0.0'` on backfill) | **New** — code version of `risk_engine/scoring.py` formula |
| `rating_coverage` | numeric(5,4) | N | **New** — rated / applicable non-mitigant factors |
| `evidence_coverage` | numeric(5,4) | N | **New** — applicable AI/RULES factors with `EVIDENCE_FOUND` / applicable AI/RULES factors |
| `confidence_level` | varchar(10) | N | **New** — deterministic, see `ai/risk-scoring.md` §6 |
| `analysis_run_id` | int FK analysis_runs | N | **New** — run whose factors fed this calc |
| `inputs_sha256` | varchar(80) | N | **New** — hash of canonical inputs JSON (replay check) |

Retained: methodology id/name/version/fingerprint, `inputs`, `weights`, `risk_bands`, `escalation_rules`, `triggered_rules`, `reference_data`, `jurisdiction_matches`, `final_score`, `risk_band`, `is_provisional`, `mandatory_review`, override fields (`calculated_score/band`, `override_value/band/reason/by/at`), versioning.

### 3.4 `audit_events` (extend)

| Column | Type | Req | Purpose |
|---|---|---|---|
| `actor_id` | int FK users | Req except `actor='System'` | Existing column — now always populated from JWT |
| `request_id` | varchar(40) | N | **New** — correlates with logs / `X-Request-ID` |
| `analysis_run_id` | int FK analysis_runs | N | **New** |
| `entity_type`, `entity_id` | varchar(40), varchar(50) | N | **New** — what was changed (e.g. `risk_factor`, `42`) |
| `before_json`, `after_json` | text | N | **New** — field-level diff for overrides/edits (masked) |
| `prev_hash`, `row_hash` | varchar(80) | N | **Production** — SHA-256 hash chain per assessment |

Indexes: existing `assessment_id`, `action`, `created_at`; **add** `(assessment_id, created_at)`.

### 3.5 `analysis_runs` (new) — "AssessmentRun"

One row per execution of an AI task that affects an assessment (risk identification, L×I suggestion, control identification, draft narrative, document extraction).

| Column | Type | Req | Notes |
|---|---|---|---|
| `id` | serial PK | Req | |
| `run_uuid` | uuid | Req, unique | Public id (`GET /api/analysis-runs/{run_uuid}`) |
| `assessment_id` | int FK assessments | Req | indexed |
| `run_type` | varchar(40) | Req | `RISK_IDENTIFICATION`, `LIKELIHOOD_IMPACT_SUGGESTION`, `CONTROL_IDENTIFICATION`, `CONTROL_DESIGN_ASSESSMENT`, `DRAFT_NARRATIVE`, `DOCUMENT_EXTRACTION` |
| `status` | varchar(20) | Req | `QUEUED`, `RUNNING`, `SUCCEEDED`, `DEGRADED`, `UNAVAILABLE`, `FAILED` |
| `processing_job_id` | int FK processing_jobs | N | |
| `parent_run_id` | int FK analysis_runs | N | Re-run lineage |
| `workflow_name` / `workflow_version` | varchar(60) / varchar(20) | Req | e.g. `risk_identification` / `2.0.0` (constant in `langgraph/graph.py`) |
| `graph_hash` | varchar(80) | N | SHA-256 of node names + edges |
| `prompt_version_id` | int FK prompt_versions | N | Null for rules-only |
| `provider` | varchar(40) | Req | `openrouter` |
| `requested_model` / `response_model` | varchar(255) | N | Pinned model vs what provider actually served |
| `model_params` | text (JSON) | N | temperature, max_tokens, response_format |
| `methodology_id` / `methodology_fingerprint` | int / varchar(80) | N | Config in force when run started |
| `reference_snapshot_ids` | text (JSON list) | N | Attested FATF/EU snapshots used |
| `input_sha256` | varchar(80) | Req | Hash of canonical masked input (assessment + intelligence + sources) |
| `input_snapshot` | text | N | Masked canonical input (retained per retention policy) |
| `raw_output` | text | N | Masked raw model response |
| `output_summary` | text (JSON) | N | counts: factors, applicable, verified/rejected quotes, rejected indicators |
| `assessment_mode` / `ai_status` / `technical_error_code` | varchar | N | Degraded-mode contract |
| `trace_id` | varchar(64) | N | Langfuse |
| `requested_by_id` | int FK users | N | Null for system-triggered |
| `started_at` / `finished_at` / `duration_ms` | timestamptz / int | N | |
| `created_at` | timestamp | Req | |

Constraints: append-only except the single `RUNNING → terminal` update performed by the worker (enforced in the service; production trigger allows only status/finished_at/duration/outputs to be set once). Index `(assessment_id, run_type, created_at DESC)`.

### 3.6 `prompt_versions` (new)

Prompts stay in code (reviewed in PRs); the table is a **registry of what was deployed**, seeded idempotently at startup from `app/ai/prompts/registry.py`.

| Column | Type | Req | Notes |
|---|---|---|---|
| `id` | serial PK | Req | |
| `prompt_key` | varchar(60) | Req | e.g. `risk_factor_identification` |
| `version` | varchar(20) | Req | SemVer, bumped manually in code |
| `template_sha256` | varchar(80) | Req | Hash of the template text (placeholders unfilled) |
| `template_text` | text | Req | Exact template |
| `output_schema_version` | varchar(20) | Req | JSON schema the parser expects |
| `created_at` | timestamp | Req | |
| `retired_at` | timestamp | N | Set when a newer version of the same key is registered |

Unique `(prompt_key, version)`. If code registers an existing `(key, version)` with a different hash, **startup fails** ("prompt changed without version bump").

### 3.7 `processing_jobs` (extend)

Add `analysis_run_id` FK; add partial unique index `ux_one_active_analysis ON processing_jobs(assessment_id) WHERE job_type='RISK_ANALYSIS' AND status IN ('QUEUED','RUNNING')`.

### 3.8 `ai_usage_logs` (extend)

Add `analysis_run_id` FK (indexed), `prompt_key` varchar(60), `prompt_version` varchar(20).

### 3.9 `assessment_overrides` (extend)

Existing: `section`, `field_name`, `entity_id`, `ai_value`, `human_value`, `reason` (Req), `overridden_by`, `overridden_by_id`, `created_at`. Add `comment` text N; `previous_value_source` varchar(20) (`AI`, `RULES`, `CALCULATED`, `HUMAN`); `analysis_run_id` N. `overridden_by_id` becomes Req.

### 3.10 `idempotency_keys` (new, small)

`id` PK · `user_id` FK users (Req) · `route` varchar(120) (Req) · `idem_key` varchar(80) (Req) · `response_status` int · `response_body` text · `created_at` (Req). Unique `(user_id, route, idem_key)`. Rows older than 24 h are purged by the scheduled loop. Used only by the POST routes listed in `api/api-design.md` §1.

### 3.11 Production-only tables

| Table | Purpose |
|---|---|
| `factor_evidence` | One row per verified quote: `risk_factor_id`, `analysis_run_id`, `source_type`, `document_id`, `document_version`, `source_checksum`, `quote`, `normalized_offset`, `page`, `indicator`, `verification`, `origin` (`USER_PROVIDED`/`DOCUMENT`/`SYSTEM_DERIVED`/`EXTERNAL`) — enables cross-portfolio evidence queries |
| `approval_decisions` | Append-only history of each manager/committee decision (today only the latest is on `assessments`) |
| `products` | Product inventory linking successive assessments of the same product |

---

## 4. Enumerations (reference)

| Name | Values | Source |
|---|---|---|
| Pipeline `status` | INTAKE, EVIDENCE_COLLECTION, RISK_IDENTIFICATION, INHERENT_RISK_ASSESSMENT, CONTROL_ASSESSMENT, RESIDUAL_RISK, HUMAN_REVIEW, SUBMITTED_TO_MANAGER, RETURNED_BY_MANAGER, READY_FOR_COMMITTEE, COMMITTEE_REVIEW, APPROVED, APPROVED_WITH_CONDITIONS, DEFERRED, REJECTED, MANAGER_REJECTED, REMEDIATION, INFORMATION_REQUESTED, CLOSED | `services/workflow.py` |
| `workflow_status` (derived) | DRAFT, SUBMITTED, INTAKE_VALIDATION, INFORMATION_REQUESTED, EVIDENCE_REVIEW, RISK_ASSESSMENT_IN_PROGRESS, ANALYST_REVIEW, CHALLENGE_REVIEW, READY_FOR_COMMITTEE, COMMITTEE_REVIEW, APPROVED, APPROVED_WITH_CONDITIONS, DEFERRED, REJECTED, CLOSED, AMENDMENT_REQUIRED | `WorkflowStatus` |
| Risk band | LOW, MEDIUM, HIGH, CRITICAL, UNRATED | `scoring.py` |
| Risk category | PRODUCT_SERVICE_RISK, CUSTOMER_SEGMENT_RISK, GEOGRAPHIC_RISK, DELIVERY_CHANNEL_RISK, TRANSACTION_ACTIVITY_RISK, TECHNOLOGY_DEVELOPMENT_RISK, THIRD_PARTY_VENDOR_RISK, OWNERSHIP_ENTITY_COMPLEXITY_RISK, FINANCIAL_CRIME_TYPOLOGY_RISK, CONTROL_ENVIRONMENT_RISK (mitigant) | `schemas/risk_factor.py` |
| Indicator | 12 existing + **proposed** `PEP_EXPOSURE`, `PUBLIC_OFFICIAL_INTERACTION` (bribery/corruption; open question Q-02) | |
| Crime typology (new) | AML, TERRORIST_FINANCING, SANCTIONS, FRAUD, BRIBERY_CORRUPTION | |
| Evidence status | EVIDENCE_FOUND, INSUFFICIENT_EVIDENCE, CONFLICTING_EVIDENCE, NOT_VERIFIED, NOT_APPLICABLE | `evidence.py` |
| Assessment mode | ai_assisted, rules_only, unavailable | `degraded.py` |
| Role | BUSINESS_USER, FCRM_ANALYST, MANAGER, COMMITTEE_MEMBER, ADMIN, (+AUDITOR prod) | `models/user.py` |

Production: add `CHECK` constraints for status, band and role columns (generated from the Python enums in a migration) so the database rejects invalid values.
